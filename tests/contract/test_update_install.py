"""Contract tests for installing the last seen release from the panel, over the real wiring.

Driven frontend-shaped per ``frontend/src/api/backend.ts``: ``getUpdateInstallState``
takes nothing and ``installUpdate`` the version string it was offered. The real
``bootstrap()`` is what makes it worth having: the release is the one the real
release check stored in SQLite, the tarball is staged under the real cache root,
and the update rule a press takes is the one every other endpoint's use case
asks. The download, the installer's unit and Steam's running apps are the
harness's fakes — nothing here reaches the network or the user manager.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tarfile
from typing import TYPE_CHECKING, Any

import pytest

from domain.identity import VERSION
from domain.update_install import new_attempt_record
from domain.update_release import LatestRelease, ReleaseTarball

from ._harness import ContractHarness, build_contract_harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_OFFERED = "99.0.0"
_STATE_KEYS = {"offered", "version", "wait_reasons", "paused_downloads", "attempt", "try_again"}
_ATTEMPT_KEYS = {"version", "step", "bytes_done", "bytes_total", "failure"}


def _tarball() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("romm-tender/install.sh")
        body = b"#!/bin/bash\n"
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    return buffer.getvalue()


@pytest.fixture
async def installed(tmp_path) -> AsyncIterator[ContractHarness]:
    """The harness as the installed program, with a newer release stored by a real check.

    The attempt's watch is stopped at teardown the way ``Application.shutdown`` stops it:
    the harness's sleeper returns at once, so a watch left running would spin
    until the loop closed.
    """
    harness = build_contract_harness(tmp_path, installed_program=True)
    body = _tarball()
    url = f"https://github.test/releases/download/tender-v{_OFFERED}/romm-tender-{_OFFERED}.tar.gz"
    harness.releases.answer = LatestRelease(
        version=_OFFERED,
        tarball=ReleaseTarball(url=url, digest=hashlib.sha256(body).hexdigest(), checksum_url=f"{url}.sha256"),
    )
    harness.downloads.bodies = {url: body, f"{url}.sha256": b"digest  romm-tender-99.0.0.tar.gz\n"}
    assert (await harness.endpoints.check_for_update_now())["latest_version"] == _OFFERED
    yield harness
    await harness.app.services.update_install_service.shutdown()


async def _settled(harness: ContractHarness) -> dict[str, Any]:
    for _ in range(500):
        state = await harness.endpoints.get_update_install_state()
        attempt = state["attempt"]
        if attempt is not None and attempt["step"] in ("failed", "installer_started"):
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("the attempt never settled")


async def test_the_state_answer_offers_the_stored_release(installed):
    state = await installed.endpoints.get_update_install_state()

    assert set(state) == _STATE_KEYS
    assert (state["offered"], state["version"]) == (True, _OFFERED)
    assert state["wait_reasons"] == []
    assert (state["paused_downloads"], state["attempt"], state["try_again"]) == (0, None, False)


async def test_the_stored_release_is_offered_with_the_check_switched_off(installed):
    """The switch governs what the program asks GitHub by itself, not what a check already found."""
    assert installed.endpoints.set_update_check_enabled(False) == {"success": True}

    state = await installed.endpoints.get_update_install_state()

    assert (state["offered"], state["version"]) == (True, _OFFERED)


async def test_a_run_that_is_not_the_installed_program_is_offered_nothing(harness):
    state = await harness.endpoints.get_update_install_state()

    assert (state["offered"], state["version"]) == (False, None)
    answer = await harness.endpoints.install_update(_OFFERED)
    assert answer == {"success": False, "reason": "not_offered", "message": answer["message"]}
    assert answer["message"]


async def test_a_running_app_makes_the_press_wait(installed):
    installed.steam.apps = ("A Game",)

    answer = await installed.endpoints.install_update(_OFFERED)

    assert answer["success"] is False
    assert answer["reason"] == "update_waiting"
    assert answer["wait_reasons"] == [{"reason": "app_running", "apps": ["A Game"]}]
    assert installed.units.starts == []


async def test_a_lease_a_frontend_still_holds_makes_the_press_wait_as_other_work(installed):
    token = await installed.prune_conflicts.acquire_lease("launch_reconfirm")

    answer = await installed.endpoints.install_update(_OFFERED)

    assert (answer["reason"], answer["wait_reasons"]) == ("update_waiting", [{"reason": "other_work"}])
    await installed.prune_conflicts.release_lease(token)
    assert (await installed.endpoints.get_update_install_state())["wait_reasons"] == []


async def test_an_endpoint_that_only_reads_makes_no_press_wait(installed):
    registration = await installed.prune_conflicts.hold_operation("test_connection")
    assert registration is not None

    assert (await installed.endpoints.get_update_install_state())["wait_reasons"] == []
    await installed.prune_conflicts.release_operation(registration)


async def test_a_press_installs_the_stored_release_through_the_real_cache_root(installed):
    assert await installed.endpoints.install_update(_OFFERED) == {"success": True}

    state = await _settled(installed)

    assert set(state["attempt"]) == _ATTEMPT_KEYS
    assert state["attempt"]["step"] == "installer_started"
    update_dir = os.path.join(installed.cache_dir, "update")
    tarball = os.path.join(update_dir, f"romm-tender-{_OFFERED}.tar.gz")
    ((unit, command, _environment),) = installed.units.starts
    assert unit == "romm-tender-update"
    assert command == ("/bin/bash", os.path.join(update_dir, "install.sh"), "--from", tarball, "--yes")
    assert os.path.isfile(f"{tarball}.sha256")
    progress = [call.args[1] for call in installed.emit.await_args_list if call.args[0] == "update_install_progress"]
    assert progress[-1]["step"] == "installer_started"


async def test_from_the_press_on_an_endpoint_a_migration_refuses_answers_blocked_by_update(installed):
    await installed.endpoints.install_update(_OFFERED)

    answer = await installed.endpoints.save_platform_sync(7, True)

    assert answer == {
        "success": False,
        "reason": "blocked_by_update",
        "message": "Tender is installing an update and will restart in a moment.",
    }


async def test_a_failed_attempt_gives_the_rule_back_and_says_try_again(installed):
    installed.units.refusal = "Unit romm-tender-update.service was already loaded"
    await installed.endpoints.install_update(_OFFERED)

    state = await _settled(installed)

    assert state["attempt"]["failure"] == "installer_not_started"
    assert state["try_again"] is True
    assert (await installed.endpoints.save_platform_sync(7, True))["success"] is True
    assert not os.path.lexists(os.path.join(installed.cache_dir, "update"))


async def test_the_version_pressed_must_be_the_one_stored(installed):
    answer = await installed.endpoints.install_update(VERSION)

    assert answer["reason"] == "version_changed"


async def test_a_stopped_attempt_judged_once_the_installer_ended_reaches_the_running_panel(tmp_path):
    """A start the installer made itself, still inside its unit: the record waits for the unit, then is pushed."""
    harness = build_contract_harness(tmp_path, installed_program=True)
    service = harness.app.services.update_install_service
    service._attempts.write(new_attempt_record(_OFFERED, VERSION, 0.0))
    states: list[bool | None] = [True, True, False]
    harness.units.states = states

    service.note_start()
    assert harness.endpoints.get_stopped_update_attempt() is None
    judging = service._judging
    assert judging is not None
    await asyncio.wait_for(judging, 2)

    stopped = {"attempted_version": _OFFERED, "from_version": VERSION, "started_at": "1970-01-01T00:00:00Z"}
    pushed = [call.args[1] for call in harness.emit.await_args_list if call.args[0] == "update_attempt_stopped"]
    assert pushed == [stopped]
    assert harness.endpoints.get_stopped_update_attempt() == stopped
    assert await harness.endpoints.dismiss_stopped_update_attempt() == {"success": True}
    assert harness.endpoints.get_stopped_update_attempt() is None
    assert service._attempts.read() is None
