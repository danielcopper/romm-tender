"""Tests for UpdateInstallService — installing the last seen release, and what a press waits for."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import tarfile
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from fakes.fake_event_sink import FakeEventSink
from fakes.fake_release_download import FakeReleaseDownload
from fakes.fake_steam_interface import FakeSteamInterface
from fakes.fake_transient_units import FakeTransientUnits
from fakes.system_time import FakeClock

from adapters.update_attempt import UpdateAttemptFileAdapter
from adapters.update_staging import UpdateStagingAdapter
from domain.update_install import (
    INSTALLER_UNIT,
    READ_ONLY_CLAIMS,
    UPDATE_ATTEMPT_FILENAME,
    UpdateAttemptRecord,
    WaitReason,
    claims_named_by,
    encode_attempt_record,
)
from domain.update_outcome import UpdateFailure, UpdateFailureKind
from domain.update_release import LatestRelease, ReleaseTarball
from services.update_install import UpdateInstallService, UpdateInstallServiceConfig
from tests._conflict_rules import call_sites_with_rule, claim_names_in_source, claims_held_on_the_prune_conflicts

if TYPE_CHECKING:
    from collections.abc import Callable

_RUNNING = "1.0.0"
_OFFERED = "1.1.0"
_INSTALLER = b"#!/bin/bash\necho installing\n"
_STATE_KEYS = {"offered", "version", "wait_reasons", "paused_downloads", "attempt", "try_again"}
_ENVIRONMENT = (("TENDER_CODE_DIR", "/code"), ("TENDER_PYTHON", "/usr/bin/python3.13"))
# The installer's unit, asked about at a start, has ended: the record may be judged.
_ENDED: list[bool | None] = [False]
# When an attempt's installer starts, as its record states it: FakeClock's instant.
_CLOCK_STAMP = "2026-01-01T00:00:00Z"


def _tarball(installer: bytes | None = _INSTALLER, *, as_link: bool = False) -> bytes:
    """A release tarball holding ``romm-tender/install.sh`` — or not, or as a symlink."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        readme = tarfile.TarInfo("romm-tender/version.txt")
        readme.size = len(_OFFERED)
        archive.addfile(readme, io.BytesIO(_OFFERED.encode()))
        if as_link:
            link = tarfile.TarInfo("romm-tender/install.sh")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            archive.addfile(link)
        elif installer is not None:
            member = tarfile.TarInfo("romm-tender/install.sh")
            member.size = len(installer)
            archive.addfile(member, io.BytesIO(installer))
    return buffer.getvalue()


def _release(version: str = _OFFERED, body: bytes | None = None) -> LatestRelease:
    url = f"https://github.test/releases/download/tender-v{version}/romm-tender-{version}.tar.gz"
    digest = hashlib.sha256(body if body is not None else _tarball()).hexdigest()
    return LatestRelease(version=version, tarball=ReleaseTarball(url=url, digest=digest, checksum_url=f"{url}.sha256"))


def _asset(release: LatestRelease) -> ReleaseTarball:
    assert release.tarball is not None
    return release.tarball


class _Releases:
    """The release check's answer: the stored release."""

    def __init__(self, release: LatestRelease | None) -> None:
        self.release = release

    async def last_seen_release(self) -> LatestRelease | None:
        return self.release


class _Work:
    """One flag per kind of work in flight, and the download queue."""

    def __init__(self) -> None:
        self.library_sync = False
        self.rom_downloads: set[int] = set()
        self.save_sync = False
        self.firmware_downloads = False
        self.save_directory_move = False
        self.cleanup = False
        self.migration_running = False
        self.claims: list[str] = []
        self.queue: list[dict[str, Any]] = []


class _ParkingSleeper:
    """Returns at once for *free* sleeps, then parks until cancelled — so a watch can be held still."""

    def __init__(self, free: int = 0) -> None:
        self.free = free
        self.calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        if len(self.calls) > self.free:
            await asyncio.Event().wait()


class _HeldSteam:
    """A Steam whose running-apps readings all wait until ``answer`` is set, so two presses can wait together."""

    def __init__(self) -> None:
        self.answer = asyncio.Event()
        self.readings = 0

    async def running_apps(self) -> tuple[str, ...] | None:
        self.readings += 1
        await self.answer.wait()
        return ()

    async def reload_frees_at(self) -> float | None:
        return None

    async def both_waiting(self) -> None:
        for _ in range(100):
            if self.readings == 2:
                return
            await asyncio.sleep(0)
        raise AssertionError(f"only {self.readings} reading(s) began")


@dataclass
class _Rig:
    service: UpdateInstallService
    release: LatestRelease
    work: _Work
    steam: FakeSteamInterface
    downloads: FakeReleaseDownload
    units: FakeTransientUnits
    events: FakeEventSink
    staging_dir: str
    releases: _Releases
    sleeper: _ParkingSleeper

    @property
    def tarball(self) -> ReleaseTarball:
        assert self.release.tarball is not None
        return self.release.tarball

    async def settled(self) -> dict[str, Any]:
        """Wait for the attempt to reach a step it stays on, and answer its last frame."""
        for _ in range(500):
            frames = [payload for name, payload in self.events.events if name == "update_install_progress"]
            if frames and frames[-1]["step"] in ("failed", "installer_started"):
                return frames[-1]
            await asyncio.sleep(0.01)
        raise AssertionError(f"the attempt never settled: {self.events.events}")


async def _rig(
    tmp_path,
    *,
    release: LatestRelease | None = None,
    body: bytes | None = None,
    installed_program: bool = True,
    apps: tuple[str, ...] | None = (),
    frees_at: float | None = None,
    failing: set[str] | None = None,
    checksum_body: bytes = b"sidecar\n",
    unit_refusal: str | None = None,
    unit_states: list[bool | None] | None = None,
    unit_no_answer: bool = False,
    sleeper: _ParkingSleeper | None = None,
    failure_record: UpdateFailure | None = None,
    current_version: str = _RUNNING,
) -> _Rig:
    body = body if body is not None else _tarball()
    release = release if release is not None else _release(body=body)
    assert release.tarball is not None
    work = _Work()
    steam = FakeSteamInterface(apps=apps, frees_at=frees_at)
    downloads = FakeReleaseDownload(
        bodies={release.tarball.url: body, release.tarball.checksum_url: checksum_body}, failing=failing
    )
    units = FakeTransientUnits(refusal=unit_refusal, states=unit_states, no_answer=unit_no_answer)
    events = FakeEventSink()
    staging = UpdateStagingAdapter(directory=str(tmp_path / "cache" / "update"))
    releases = _Releases(release)
    sleeper = sleeper if sleeper is not None else _ParkingSleeper()
    service = UpdateInstallService(
        config=UpdateInstallServiceConfig(
            releases=releases,
            current_version=current_version,
            installed_program=installed_program,
            steam=steam,
            library_sync_in_flight=lambda: work.library_sync,
            rom_downloads_in_flight=lambda: work.rom_downloads,
            download_queue=lambda: {"downloads": work.queue},
            save_sync_in_flight=lambda: work.save_sync,
            firmware_downloads_in_flight=lambda: work.firmware_downloads,
            save_directory_move_in_flight=lambda: work.save_directory_move,
            cleanup_running=lambda: work.cleanup,
            migration_running=lambda: work.migration_running,
            held_claims=lambda: tuple(work.claims),
            read_update_failure=lambda: failure_record,
            attempts=UpdateAttemptFileAdapter(state_dir=str(tmp_path / "state"), log_debug=lambda msg: None),
            download_asset=downloads,
            staging=staging,
            units=units,
            installer_environment=_ENVIRONMENT,
            emit=events.emit,
            clock=FakeClock(),
            sleeper=sleeper,
            loop=asyncio.get_running_loop(),
            logger=logging.getLogger("test_update_install"),
            log_debug=lambda msg: None,
        )
    )
    return _Rig(
        service,
        release=release,
        work=work,
        steam=steam,
        downloads=downloads,
        units=units,
        events=events,
        staging_dir=str(tmp_path / "cache" / "update"),
        releases=releases,
        sleeper=sleeper,
    )


@pytest.fixture
async def rigs():
    """Every rig a test built, shut down at teardown so no parked watch outlives its test."""
    built: list[_Rig] = []
    yield built
    for rig in built:
        await rig.service.shutdown()


async def _built(rigs, tmp_path, **kwargs: Any) -> _Rig:
    rig = await _rig(tmp_path, **kwargs)
    rigs.append(rig)
    return rig


# ── What is offered ──────────────────────────────────────────────────────────


class TestWhatIsOffered:
    async def test_a_newer_stored_release_on_the_installed_program_is_offered(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        state = await rig.service.get_update_install_state()

        assert set(state) == _STATE_KEYS
        assert state["offered"] is True
        assert state["version"] == _OFFERED
        assert state["wait_reasons"] == []
        assert state["paused_downloads"] == 0
        assert state["attempt"] is None
        assert state["try_again"] is False

    async def test_a_run_from_a_checkout_is_offered_nothing(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, installed_program=False)

        state = await rig.service.get_update_install_state()

        assert (state["offered"], state["version"], state["wait_reasons"]) == (False, None, [])

    @pytest.mark.parametrize("version", [_RUNNING, "0.9.0"])
    async def test_a_release_no_newer_than_the_running_one_is_offered_nothing(self, rigs, tmp_path, version):
        rig = await _built(rigs, tmp_path, release=_release(version))

        assert (await rig.service.get_update_install_state())["offered"] is False

    async def test_no_stored_release_offers_nothing(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        rig.releases.release = None

        assert (await rig.service.get_update_install_state())["offered"] is False

    async def test_a_stored_release_without_its_tarball_offers_nothing(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        rig.releases.release = LatestRelease(version=_OFFERED, tarball=None)

        assert (await rig.service.get_update_install_state())["offered"] is False

    async def test_nothing_offered_reads_no_running_apps(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, installed_program=False)

        await rig.service.get_update_install_state()

        assert rig.steam.readings == 0

    async def test_paused_downloads_are_counted_and_other_entries_are_not(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        rig.work.queue = [{"status": "paused"}, {"status": "downloading"}, {"status": "paused"}, {"status": "failed"}]

        state = await rig.service.get_update_install_state()

        assert state["paused_downloads"] == 2
        assert state["wait_reasons"] == []


# ── What a press waits for ───────────────────────────────────────────────────


_WORK_REASONS = [
    ("library_sync", "library_sync", True),
    ("rom_downloads", "rom_downloads", {7}),
    ("save_sync", "save_sync", True),
    ("firmware_downloads", "firmware_downloads", True),
    ("save_directory_move", "save_directory_move", True),
    ("cleanup", "removed_games_cleanup", True),
    ("migration_running", "retrodeck_migration", True),
    ("claims", "other_work", ["uninstall_all_roms"]),
]


class TestWaitReasons:
    @pytest.mark.parametrize(("flag", "reason", "value"), _WORK_REASONS)
    async def test_work_in_flight_is_named(self, rigs, tmp_path, flag, reason, value):
        rig = await _built(rigs, tmp_path)
        setattr(rig.work, flag, value)

        assert (await rig.service.get_update_install_state())["wait_reasons"] == [{"reason": reason}]

    async def test_a_running_app_is_named_with_what_runs(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, apps=("Metroid Fusion",))

        reasons = (await rig.service.get_update_install_state())["wait_reasons"]

        assert reasons == [{"reason": "app_running", "apps": ["Metroid Fusion"]}]

    async def test_a_reading_that_could_not_be_taken_is_its_own_reason_never_nothing_running(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, apps=None)

        reasons = (await rig.service.get_update_install_state())["wait_reasons"]

        assert reasons == [{"reason": "running_apps_unknown"}]

    async def test_a_reading_that_raised_is_one_that_could_not_be_taken(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        rig.steam.raises = RuntimeError("debugger gone")

        assert (await rig.service.get_update_install_state())["wait_reasons"] == [{"reason": "running_apps_unknown"}]
        answer = await rig.service.install_update(_OFFERED)
        assert (answer["reason"], answer["wait_reasons"]) == ("update_waiting", [{"reason": "running_apps_unknown"}])

    async def test_the_reload_limit_carries_the_time_it_frees_up(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, frees_at=1_800_000_600.0)

        reasons = (await rig.service.get_update_install_state())["wait_reasons"]

        assert reasons == [{"reason": "interface_reload_limit", "frees_at": 1_800_000_600.0}]

    async def test_a_reload_limit_reading_that_raised_is_its_own_reason_never_no_limit(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        rig.steam.limit_raises = RuntimeError("cannot schedule new futures after shutdown")

        state = await rig.service.get_update_install_state()
        assert state["wait_reasons"] == [{"reason": "interface_reload_limit_unknown"}]
        answer = await rig.service.install_update(_OFFERED)
        assert (answer["reason"], answer["wait_reasons"]) == (
            "update_waiting",
            [{"reason": "interface_reload_limit_unknown"}],
        )
        assert rig.service.is_update_in_progress() is False

    async def test_every_reason_that_holds_is_named(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, apps=None, frees_at=5.0)
        for flag, _reason, value in _WORK_REASONS:
            setattr(rig.work, flag, value)

        reasons = [entry["reason"] for entry in (await rig.service.get_update_install_state())["wait_reasons"]]

        assert reasons == [
            "running_apps_unknown",
            "library_sync",
            "rom_downloads",
            "save_sync",
            "firmware_downloads",
            "save_directory_move",
            "removed_games_cleanup",
            "retrodeck_migration",
            "other_work",
            "interface_reload_limit",
        ]

    @pytest.mark.parametrize(
        ("claim", "reason"),
        [
            ("sync_rom_saves", "save_sync"),
            ("switch_slot", "save_sync"),
            ("finalize_game_session", "save_sync"),
            ("start_download", "rom_downloads"),
            ("download_complete", "rom_downloads"),
            ("sync_complete", "library_sync"),
            ("migrate_retrodeck_files", "retrodeck_migration"),
            ("prune_complete", "removed_games_cleanup"),
            ("remove_all_shortcuts", "other_work"),
            ("launch_reconfirm", "other_work"),
        ],
    )
    async def test_a_claim_on_the_prune_conflicts_is_named_by_the_reason_for_its_work(
        self, rigs, tmp_path, claim, reason
    ):
        rig = await _built(rigs, tmp_path)
        rig.work.claims = [claim]

        assert (await rig.service.get_update_install_state())["wait_reasons"] == [{"reason": reason}]

    async def test_a_save_operation_under_way_and_the_gate_it_holds_are_one_reason(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        rig.work.claims = ["sync_all_saves", "remove_all_shortcuts", "switch_version"]
        rig.work.save_sync = True

        reasons = (await rig.service.get_update_install_state())["wait_reasons"]

        assert reasons == [{"reason": "save_sync"}, {"reason": "other_work"}]

    async def test_a_claim_refuses_the_press_too(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        rig.work.claims = ["adopt_existing_rom"]

        answer = await rig.service.install_update(_OFFERED)

        assert (answer["reason"], answer["wait_reasons"]) == ("update_waiting", [{"reason": "other_work"}])
        assert rig.service.is_update_in_progress() is False

    @pytest.mark.parametrize("claim", sorted(READ_ONLY_CLAIMS))
    async def test_a_read_only_claim_makes_no_press_wait(self, rigs, tmp_path, claim):
        """A restart cuts nothing short that only read: the reason would flicker in and out with every read."""
        rig = await _built(rigs, tmp_path)
        rig.work.claims = [claim]

        assert (await rig.service.get_update_install_state())["wait_reasons"] == []
        assert await rig.service.install_update(_OFFERED) == {"success": True}

    async def test_paused_downloads_do_not_make_a_press_wait(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        rig.work.queue = [{"status": "paused"}]

        assert await rig.service.install_update(_OFFERED) == {"success": True}


# ── The press, refused ───────────────────────────────────────────────────────


class TestARefusedPress:
    async def test_a_press_while_work_is_in_flight_is_refused_with_every_reason(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, apps=("A Game",))
        rig.work.save_sync = True

        answer = await rig.service.install_update(_OFFERED)

        assert answer["success"] is False
        assert answer["reason"] == "update_waiting"
        assert isinstance(answer["message"], str)
        assert answer["message"]
        assert answer["wait_reasons"] == [{"reason": "app_running", "apps": ["A Game"]}, {"reason": "save_sync"}]
        assert rig.service.is_update_in_progress() is False
        assert rig.downloads.calls == []

    async def test_the_press_takes_a_reading_of_its_own(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        await rig.service.get_update_install_state()
        rig.steam.apps = ("Started since",)

        answer = await rig.service.install_update(_OFFERED)

        assert answer["reason"] == "update_waiting"
        assert rig.steam.readings == 2

    async def test_a_version_that_is_not_the_stored_one_is_refused(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        answer = await rig.service.install_update("1.2.0")

        assert answer["reason"] == "version_changed"
        assert answer["success"] is False
        assert rig.service.is_update_in_progress() is False

    @pytest.mark.parametrize("version", [None, 110, ""])
    async def test_a_version_off_the_wire_that_is_not_the_stored_string_is_refused(self, rigs, tmp_path, version):
        rig = await _built(rigs, tmp_path)

        assert (await rig.service.install_update(version))["reason"] == "version_changed"

    async def test_a_press_with_nothing_offered_is_refused(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, installed_program=False)

        answer = await rig.service.install_update(_OFFERED)

        assert answer["reason"] == "not_offered"
        assert rig.steam.readings == 0

    async def test_a_second_press_while_an_attempt_runs_is_refused(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        assert await rig.service.install_update(_OFFERED) == {"success": True}

        answer = await rig.service.install_update(_OFFERED)

        assert answer["reason"] == "update_in_progress"
        assert answer["success"] is False

    async def test_a_press_that_started_while_another_read_steam_is_refused(self, rigs, tmp_path):
        """Both presses pass the first check and wait on Steam together; the one answered second finds the rule held."""
        rig = await _built(rigs, tmp_path)
        steam = _HeldSteam()
        rig.service._steam = steam
        first = asyncio.ensure_future(rig.service.install_update(_OFFERED))
        second = asyncio.ensure_future(rig.service.install_update(_OFFERED))
        await steam.both_waiting()

        steam.answer.set()
        answers = await asyncio.gather(first, second)

        assert steam.readings == 2
        assert sorted(answer["success"] for answer in answers) == [False, True]
        assert {answer.get("reason") for answer in answers} == {None, "update_in_progress"}


# ── The press, through to the installer ──────────────────────────────────────


class TestThePress:
    async def test_the_rule_is_held_from_the_press_on(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        await rig.service.install_update(_OFFERED)

        assert rig.service.is_update_in_progress() is True

    async def test_it_downloads_verifies_and_starts_the_installer_as_its_own_unit(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        await rig.service.install_update(_OFFERED)
        last = await rig.settled()

        assert last == {
            "version": _OFFERED,
            "step": "installer_started",
            "bytes_done": 0,
            "bytes_total": None,
            "failure": None,
        }
        tarball = os.path.join(rig.staging_dir, f"romm-tender-{_OFFERED}.tar.gz")
        installer = os.path.join(rig.staging_dir, "install.sh")
        assert rig.downloads.calls == [
            (rig.tarball.url, tarball),
            (rig.tarball.checksum_url, f"{tarball}.sha256"),
        ]
        assert rig.units.starts == [
            (INSTALLER_UNIT, ("/bin/bash", installer, "--from", tarball, "--yes"), _ENVIRONMENT),
        ]
        with open(installer, "rb") as f:
            assert f.read() == _INSTALLER
        with open(f"{tarball}.sha256", "rb") as f:
            assert f.read() == b"sidecar\n"
        assert rig.service.is_update_in_progress() is True

    async def test_the_steps_are_reported_in_order(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        await rig.service.install_update(_OFFERED)
        await rig.settled()

        steps = [payload["step"] for name, payload in rig.events.events if name == "update_install_progress"]
        assert steps[0] == "downloading"
        assert steps[-2:] == ["verifying", "installer_started"]
        assert set(steps[1:-2]) <= {"downloading"}

    async def test_the_download_reports_its_bytes(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        await rig.service.install_update(_OFFERED)
        await rig.settled()

        downloading = [
            payload
            for name, payload in rig.events.events
            if name == "update_install_progress" and payload["step"] == "downloading"
        ]
        size = len(_tarball())
        assert downloading[-1]["bytes_done"] == size
        assert downloading[-1]["bytes_total"] == size

    async def test_the_download_is_throttled_but_its_last_tick_always_goes_out(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        await rig.service.install_update(_OFFERED)
        await rig.settled()

        with_bytes = [
            payload
            for name, payload in rig.events.events
            if name == "update_install_progress" and payload["step"] == "downloading" and payload["bytes_done"]
        ]
        # The clock never moves, so only the first tick and the final count pass
        # the throttle; the first can land after the download is done and is
        # then dropped, the final count never is.
        size = len(_tarball())
        assert [payload["bytes_done"] for payload in with_bytes] in ([4, size], [size])

    async def test_the_answer_while_the_installer_runs_carries_the_attempt_and_no_reasons(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        await rig.service.install_update(_OFFERED)
        await rig.settled()
        rig.work.save_sync = True

        state = await rig.service.get_update_install_state()

        assert state["attempt"]["step"] == "installer_started"
        assert state["wait_reasons"] == []
        assert state["try_again"] is False

    async def test_a_state_that_cannot_be_read_is_a_warning_once_and_the_watch_goes_on(self, rigs, tmp_path, caplog):
        rig = await _built(rigs, tmp_path, unit_states=[None, None, None], sleeper=_ParkingSleeper(free=3))

        with caplog.at_level(logging.WARNING, logger="test_update_install"):
            await rig.service.install_update(_OFFERED)
            await rig.settled()
            for _ in range(100):
                if len(rig.units.asked) == 3:
                    break
                await asyncio.sleep(0.01)

        unreadable = [record for record in caplog.records if "could not be read" in record.getMessage()]
        assert len(unreadable) == 1
        assert unreadable[0].levelno == logging.WARNING
        assert rig.units.asked == [INSTALLER_UNIT] * 3
        assert rig.service.is_update_in_progress() is True

    async def test_a_manager_that_cannot_be_asked_is_not_the_installer_stopping(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, unit_states=[None, True, None], sleeper=_ParkingSleeper(free=3))

        await rig.service.install_update(_OFFERED)
        await rig.settled()
        for _ in range(100):
            if len(rig.units.asked) == 3:
                break
            await asyncio.sleep(0.01)

        assert rig.units.asked == [INSTALLER_UNIT] * 3
        assert rig.service.is_update_in_progress() is True
        assert (await rig.service.get_update_install_state())["attempt"]["step"] == "installer_started"


# ── How an attempt fails, and what it leaves ─────────────────────────────────


class TestAFailedAttempt:
    async def _failed(self, rig: _Rig) -> dict[str, Any]:
        await rig.service.install_update(_OFFERED)
        return await rig.settled()

    async def test_a_download_that_fails_is_download_failed(self, rigs, tmp_path):
        release = _release()
        rig = await _built(rigs, tmp_path, release=release, failing={_asset(release).url})

        last = await self._failed(rig)

        assert (last["step"], last["failure"]) == ("failed", "download_failed")
        assert rig.units.starts == []

    async def test_a_tarball_that_does_not_match_its_digest_is_checksum_mismatch(self, rigs, tmp_path):
        good = _release()
        rig = await _built(rigs, tmp_path, release=good, body=_tarball(b"#!/bin/bash\nrm -rf ~\n"))

        last = await self._failed(rig)

        assert last["failure"] == "checksum_mismatch"
        assert rig.units.starts == []
        # The checksum file is never fetched for a tarball that failed its own check.
        assert [url for url, _dest in rig.downloads.calls] == [_asset(good).url]

    async def test_a_checksum_file_that_cannot_be_fetched_is_download_failed(self, rigs, tmp_path):
        release = _release()
        rig = await _built(rigs, tmp_path, release=release, failing={_asset(release).checksum_url})

        assert (await self._failed(rig))["failure"] == "download_failed"
        assert rig.units.starts == []

    async def test_a_tarball_with_no_installer_is_installer_not_started(self, rigs, tmp_path):
        body = _tarball(None)
        rig = await _built(rigs, tmp_path, body=body)

        assert (await self._failed(rig))["failure"] == "installer_not_started"
        assert rig.units.starts == []

    async def test_an_installer_that_is_a_link_is_never_followed(self, rigs, tmp_path):
        body = _tarball(as_link=True)
        rig = await _built(rigs, tmp_path, body=body)

        assert (await self._failed(rig))["failure"] == "installer_not_started"
        assert rig.units.starts == []

    async def test_a_game_started_during_the_download_fails_the_attempt_before_anything_changed(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        await rig.service.install_update(_OFFERED)
        rig.steam.apps = ("Celeste",)

        last = await rig.settled()

        assert (last["step"], last["failure"]) == ("failed", "game_started")
        assert rig.units.starts == []
        assert rig.service.is_update_in_progress() is False
        assert not os.path.lexists(rig.staging_dir)
        assert (await rig.service.get_update_install_state())["try_again"] is True

    async def test_no_reading_of_the_running_apps_before_the_installer_fails_the_attempt(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        await rig.service.install_update(_OFFERED)
        rig.steam.apps = None

        last = await rig.settled()

        assert (last["step"], last["failure"]) == ("failed", "running_apps_unknown")
        assert rig.units.starts == []
        assert rig.service.is_update_in_progress() is False

    async def test_a_reading_that_raised_before_the_installer_is_no_reading(self, rigs, tmp_path):
        """Not ``installer_not_started``: the sentence would blame the installer for what Steam's reading did."""
        rig = await _built(rigs, tmp_path)
        await rig.service.install_update(_OFFERED)
        rig.steam.raises = RuntimeError("debugger gone")

        last = await rig.settled()

        assert (last["step"], last["failure"]) == ("failed", "running_apps_unknown")
        assert rig.units.starts == []
        assert rig.service.is_update_in_progress() is False

    async def test_the_running_apps_are_read_again_right_before_the_installer(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        await rig.service.install_update(_OFFERED)
        await rig.settled()

        assert rig.steam.readings == 2
        assert len(rig.units.starts) == 1

    async def test_a_unit_that_would_not_start_is_installer_not_started(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, unit_refusal="Unit romm-tender-update.service was already loaded")

        assert (await self._failed(rig))["failure"] == "installer_not_started"

    async def test_an_installer_that_ends_while_this_process_runs_is_installer_stopped(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, unit_states=[True, False], sleeper=_ParkingSleeper(free=5))

        await rig.service.install_update(_OFFERED)
        for _ in range(200):
            frames = [payload for name, payload in rig.events.events if name == "update_install_progress"]
            if frames[-1]["step"] == "failed":
                break
            await asyncio.sleep(0.01)

        assert frames[-1]["failure"] == "installer_stopped"
        assert rig.units.asked == [INSTALLER_UNIT, INSTALLER_UNIT]

    @staticmethod
    async def _ended_with(rig: _Rig) -> dict[str, Any]:
        """Press, let the installer run for one watch and end, and answer the last frame."""
        await rig.service.install_update(_OFFERED)
        for _ in range(200):
            frames = [payload for name, payload in rig.events.events if name == "update_install_progress"]
            if frames[-1]["step"] == "failed":
                return frames[-1]
            await asyncio.sleep(0.01)
        raise AssertionError(f"the attempt never failed: {rig.events.events}")

    async def test_an_installer_whose_check_refused_this_attempt_is_new_version_does_not_start(
        self, rigs, tmp_path, caplog
    ):
        """The record names this version, leaves the running one in place, and was written after the press."""
        refused = UpdateFailure(
            attempted_version=_OFFERED,
            restored_version=_RUNNING,
            rolled_back_at=_CLOCK_STAMP,
            kind=UpdateFailureKind.CHECK,
        )
        rig = await _built(
            rigs, tmp_path, unit_states=[True, False], sleeper=_ParkingSleeper(free=5), failure_record=refused
        )

        with caplog.at_level(logging.WARNING, logger="test_update_install"):
            last = await self._ended_with(rig)

        assert last["failure"] == "new_version_does_not_start"
        assert rig.service.is_update_in_progress() is False
        assert f"update: the pre-install check refused {_OFFERED}" in caplog.text

    async def test_the_refusal_is_pushed_to_the_panel_before_the_attempt_fails(self, rigs, tmp_path):
        """So the notice on Main shows it now, rather than at the next panel load."""
        refused = UpdateFailure(_OFFERED, _RUNNING, _CLOCK_STAMP, UpdateFailureKind.CHECK)
        rig = await _built(
            rigs, tmp_path, unit_states=[True, False], sleeper=_ParkingSleeper(free=5), failure_record=refused
        )

        await self._ended_with(rig)

        names = [name for name, _payload in rig.events.events]
        pushed = [payload for name, payload in rig.events.events if name == "update_failure_recorded"]
        assert pushed == [refused.to_wire()]
        assert names.index("update_failure_recorded") < max(
            index for index, name in enumerate(names) if name == "update_install_progress"
        )

    @pytest.mark.parametrize(
        "record",
        [
            # An earlier attempt's refusal of the same version, written before this press.
            UpdateFailure(_OFFERED, _RUNNING, "2025-12-31T23:59:59Z", UpdateFailureKind.CHECK),
            # A refusal of another version.
            UpdateFailure("1.2.0", _RUNNING, _CLOCK_STAMP, UpdateFailureKind.CHECK),
            # A refusal that names another version as still running: a leftover.
            UpdateFailure(_OFFERED, "0.9.0", _CLOCK_STAMP, UpdateFailureKind.CHECK),
            # A rollback: the installer that writes one has stopped this process first.
            UpdateFailure(_OFFERED, _RUNNING, _CLOCK_STAMP, UpdateFailureKind.ROLLBACK),
            None,
        ],
    )
    async def test_any_other_record_leaves_it_installer_stopped(self, rigs, tmp_path, record):
        rig = await _built(
            rigs, tmp_path, unit_states=[True, False], sleeper=_ParkingSleeper(free=5), failure_record=record
        )

        assert (await self._ended_with(rig))["failure"] == "installer_stopped"
        assert [name for name, _payload in rig.events.events if name == "update_failure_recorded"] == []

    async def test_a_record_that_cannot_be_read_leaves_it_installer_stopped(self, rigs, tmp_path, monkeypatch):
        rig = await _built(rigs, tmp_path, unit_states=[True, False], sleeper=_ParkingSleeper(free=5))

        def raising() -> UpdateFailure | None:
            raise OSError("state directory gone")

        monkeypatch.setattr(rig.service, "_read_update_failure", raising)

        assert (await self._ended_with(rig))["failure"] == "installer_stopped"

    async def test_a_unit_seam_that_raises_is_installer_not_started(self, rigs, tmp_path, monkeypatch):
        rig = await _built(rigs, tmp_path)

        def raising(*_args: object) -> str | None:
            raise OSError("no user manager")

        monkeypatch.setattr(rig.units, "start", raising)

        assert (await self._failed(rig))["failure"] == "installer_not_started"
        assert rig.service.is_update_in_progress() is False

    async def test_a_state_seam_that_raises_is_not_the_installer_stopping(self, rigs, tmp_path, monkeypatch):
        rig = await _built(rigs, tmp_path, sleeper=_ParkingSleeper(free=2))
        asked: list[str] = []

        def raising(unit: str) -> bool | None:
            asked.append(unit)
            raise OSError("bus gone")

        monkeypatch.setattr(rig.units, "is_active", raising)

        await rig.service.install_update(_OFFERED)
        await rig.settled()
        for _ in range(100):
            if len(asked) == 2:
                break
            await asyncio.sleep(0.01)

        assert asked == [INSTALLER_UNIT, INSTALLER_UNIT]
        assert rig.service.is_update_in_progress() is True

    async def test_a_start_that_gave_no_answer_and_did_not_start_is_installer_not_started(self, rigs, tmp_path):
        """Not started only once the unit still reads ended one watch interval after the first ask."""
        sleeper = _ParkingSleeper(free=1)
        rig = await _built(rigs, tmp_path, unit_no_answer=True, unit_states=[False, False], sleeper=sleeper)

        assert (await self._failed(rig))["failure"] == "installer_not_started"
        assert rig.units.asked == [INSTALLER_UNIT, INSTALLER_UNIT]
        assert sleeper.calls == [3.0]
        assert rig.service.is_update_in_progress() is False

    @pytest.mark.parametrize("later", [True, None])
    async def test_an_ended_reading_right_after_no_answer_is_not_yet_a_start_that_failed(self, rigs, tmp_path, later):
        """``systemd-run`` may not have made the unit yet, and a unit nobody knows reads as ended."""
        rig = await _built(
            rigs, tmp_path, unit_no_answer=True, unit_states=[False, later], sleeper=_ParkingSleeper(free=1)
        )

        assert (await self._failed(rig))["step"] == "installer_started"
        assert rig.units.asked == [INSTALLER_UNIT, INSTALLER_UNIT]
        assert rig.service.is_update_in_progress() is True

    @pytest.mark.parametrize("state", [True, None])
    async def test_a_start_that_gave_no_answer_is_watched_where_the_unit_may_run(self, rigs, tmp_path, state):
        """A unit that runs, or one nobody can say about, keeps the rule: the installer may be running."""
        rig = await _built(rigs, tmp_path, unit_no_answer=True, unit_states=[state])

        assert (await self._failed(rig))["step"] == "installer_started"
        assert rig.units.asked[0] == INSTALLER_UNIT
        assert rig.service.is_update_in_progress() is True

    async def test_an_unforeseen_failure_before_the_installer_fails_the_attempt_and_says_so(
        self, rigs, tmp_path, monkeypatch, caplog
    ):
        rig = await _built(rigs, tmp_path)

        def broken(_version: str) -> str:
            raise RuntimeError("staging broke")

        monkeypatch.setattr(rig.service._staging, "tarball_path", broken)

        with caplog.at_level(logging.ERROR, logger="test_update_install"):
            last = await self._failed(rig)

        assert (last["step"], last["failure"]) == ("failed", "download_failed")
        assert rig.service.is_update_in_progress() is False
        assert "failed before the installer was started" in caplog.text
        assert "staging broke" in caplog.text

    async def test_an_unforeseen_failure_once_the_installer_may_run_keeps_the_rule(self, rigs, tmp_path, caplog):
        class _BrokenSleeper:
            async def sleep(self, _seconds: float) -> None:
                raise RuntimeError("sleeper broke")

        rig = await _built(rigs, tmp_path, sleeper=_BrokenSleeper())

        with caplog.at_level(logging.ERROR, logger="test_update_install"):
            await rig.service.install_update(_OFFERED)
            await rig.settled()
            task = rig.service._task
            assert task is not None
            await asyncio.gather(task, return_exceptions=True)

        assert rig.service.is_update_in_progress() is True
        assert (await rig.service.get_update_install_state())["attempt"]["step"] == "installer_started"
        assert "the update rule stays held while it may run" in caplog.text

    @pytest.mark.parametrize(
        "setup",
        [
            {"failing": "tarball"},
            {"body": "tampered"},
            {"unit_refusal": "busy"},
        ],
    )
    async def test_a_failed_attempt_gives_the_rule_back_and_removes_what_it_staged(self, rigs, tmp_path, setup):
        release = _release()
        kwargs: dict[str, Any] = {"release": release}
        if setup.get("failing"):
            kwargs["failing"] = {_asset(release).url}
        if setup.get("body"):
            kwargs["body"] = _tarball(b"tampered")
        if setup.get("unit_refusal"):
            kwargs["unit_refusal"] = setup["unit_refusal"]
        rig = await _built(rigs, tmp_path, **kwargs)

        await self._failed(rig)

        assert rig.service.is_update_in_progress() is False
        assert not os.path.lexists(rig.staging_dir)

    async def test_a_failed_attempt_offers_the_same_version_again(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, unit_refusal="busy")
        await self._failed(rig)

        state = await rig.service.get_update_install_state()

        assert state["try_again"] is True
        assert state["attempt"] == {
            "version": _OFFERED,
            "step": "failed",
            "bytes_done": 0,
            "bytes_total": None,
            "failure": "installer_not_started",
        }
        assert state["wait_reasons"] == []

    async def test_a_failed_attempt_can_be_pressed_again_and_nothing_retries_by_itself(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, unit_refusal="busy")
        await self._failed(rig)
        assert len(rig.units.starts) == 1
        rig.units.refusal = None

        assert await rig.service.install_update(_OFFERED) == {"success": True}
        assert (await rig.settled())["step"] == "installer_started"
        assert len(rig.units.starts) == 2

    async def test_a_failure_for_an_older_version_does_not_say_try_again(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, unit_refusal="busy")
        await self._failed(rig)
        rig.releases.release = _release("1.2.0")

        assert (await rig.service.get_update_install_state())["try_again"] is False


# ── After a rollback ─────────────────────────────────────────────────────────


class TestAfterARollback:
    async def test_a_rollback_of_the_offered_version_says_try_again(self, rigs, tmp_path):
        record = UpdateFailure(
            attempted_version=_OFFERED, restored_version=_RUNNING, rolled_back_at="2026-09-01T00:00:00Z"
        )
        rig = await _built(rigs, tmp_path, failure_record=record)

        assert (await rig.service.get_update_install_state())["try_again"] is True

    async def test_a_rollback_of_another_version_does_not(self, rigs, tmp_path):
        record = UpdateFailure(
            attempted_version="1.0.5", restored_version=_RUNNING, rolled_back_at="2026-09-01T00:00:00Z"
        )
        rig = await _built(rigs, tmp_path, failure_record=record)

        assert (await rig.service.get_update_install_state())["try_again"] is False

    async def test_a_record_that_no_longer_describes_this_start_does_not(self, rigs, tmp_path):
        record = UpdateFailure(
            attempted_version=_OFFERED, restored_version="0.9.0", rolled_back_at="2026-09-01T00:00:00Z"
        )
        rig = await _built(rigs, tmp_path, failure_record=record)

        assert (await rig.service.get_update_install_state())["try_again"] is False


# ── The record of an attempt whose installer was started ────────────────────


def _record_path(tmp_path) -> str:
    return str(tmp_path / "state" / UPDATE_ATTEMPT_FILENAME)


def _leave_record(tmp_path, attempted: str = _OFFERED, started_by: str = _RUNNING) -> None:
    os.makedirs(tmp_path / "state", exist_ok=True)
    record = UpdateAttemptRecord(
        attempted_version=attempted, from_version=started_by, started_at="2026-09-01T10:00:00Z"
    )
    with open(_record_path(tmp_path), "w", encoding="utf-8") as f:
        f.write(encode_attempt_record(record))


def _read_record(tmp_path) -> dict[str, Any] | None:
    if not os.path.exists(_record_path(tmp_path)):
        return None
    with open(_record_path(tmp_path), encoding="utf-8") as f:
        return json.load(f)


class TestTheAttemptRecord:
    async def test_it_is_written_before_the_installer_starts(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        await rig.service.install_update(_OFFERED)
        await rig.settled()

        assert _read_record(tmp_path) == {
            "attempted_version": _OFFERED,
            "from_version": _RUNNING,
            "started_at": "2026-01-01T00:00:00Z",
        }

    @pytest.mark.parametrize("setup", [{"failing": "tarball"}, {"apps_after_press": ("Celeste",)}])
    async def test_an_attempt_that_ends_before_the_installer_writes_none(self, rigs, tmp_path, setup):
        release = _release()
        rig = await _built(
            rigs, tmp_path, release=release, failing={_asset(release).url} if "failing" in setup else None
        )

        await rig.service.install_update(_OFFERED)
        if "apps_after_press" in setup:
            rig.steam.apps = setup["apps_after_press"]
        await rig.settled()

        assert _read_record(tmp_path) is None

    async def test_a_failure_this_process_reports_itself_takes_the_record_away(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, unit_states=[False], sleeper=_ParkingSleeper(free=1))

        await rig.service.install_update(_OFFERED)
        for _ in range(200):
            frames = [payload for name, payload in rig.events.events if name == "update_install_progress"]
            if frames[-1]["step"] == "failed":
                break
            await asyncio.sleep(0.01)

        assert frames[-1]["failure"] == "installer_stopped"
        assert _read_record(tmp_path) is None

    async def test_a_new_attempt_takes_an_earlier_record_away(self, rigs, tmp_path):
        release = _release()
        _leave_record(tmp_path, attempted="1.0.5")
        rig = await _built(rigs, tmp_path, release=release, failing={_asset(release).url})

        await rig.service.install_update(_OFFERED)
        await rig.settled()

        assert _read_record(tmp_path) is None

    async def test_a_record_that_cannot_be_written_does_not_hold_the_install_up(
        self, rigs, tmp_path, monkeypatch, caplog
    ):
        rig = await _built(rigs, tmp_path)

        def unwritable(_record: object) -> None:
            raise OSError("read-only file system")

        monkeypatch.setattr(rig.service._attempts, "write", unwritable)

        with caplog.at_level(logging.WARNING, logger="test_update_install"):
            await rig.service.install_update(_OFFERED)
            last = await rig.settled()

        assert last["step"] == "installer_started"
        assert "could not be written" in caplog.text


async def _judged(rig: _Rig) -> list[dict[str, Any]]:
    """Wait for the judgement a start began to end, and answer what it pushed."""
    task = rig.service._judging
    assert task is not None
    await asyncio.wait_for(task, 2)
    return [payload for name, payload in rig.events.events if name == "update_attempt_stopped"]


class TestTheNextStart:
    async def test_no_record_is_nothing_to_say(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        rig.service.note_start()

        assert await _judged(rig) == []
        assert rig.units.asked == []

        assert rig.service.get_stopped_update_attempt() is None
        assert (await rig.service.get_update_install_state())["attempt"] is None

    async def test_a_start_on_the_version_that_began_it_with_no_rollback_is_an_installer_that_stopped(
        self, rigs, tmp_path, caplog
    ):
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=_ENDED)

        with caplog.at_level(logging.WARNING, logger="test_update_install"):
            rig.service.note_start()
            pushed = await _judged(rig)

        stopped = {"attempted_version": _OFFERED, "from_version": _RUNNING, "started_at": "2026-09-01T10:00:00Z"}
        assert pushed == [stopped]
        assert rig.service.get_stopped_update_attempt() == stopped
        state = await rig.service.get_update_install_state()
        assert state["attempt"] == {
            "version": _OFFERED,
            "step": "failed",
            "bytes_done": 0,
            "bytes_total": None,
            "failure": "installer_stopped",
        }
        assert state["try_again"] is True
        assert _read_record(tmp_path) is not None
        assert "stopped without updating" in caplog.text

    @pytest.mark.parametrize(
        ("running", "rolled_back"),
        [(_OFFERED, False), (_RUNNING, True), ("1.0.5", False)],
        ids=["updated", "rolled-back", "moved-since"],
    )
    async def test_any_other_record_is_removed_and_says_nothing(self, rigs, tmp_path, running, rolled_back):
        _leave_record(tmp_path)
        failure = (
            UpdateFailure(attempted_version=_OFFERED, restored_version=_RUNNING, rolled_back_at="2026-09-01T10:05:00Z")
            if rolled_back
            else None
        )
        rig = await _built(rigs, tmp_path, unit_states=_ENDED, current_version=running, failure_record=failure)

        rig.service.note_start()

        assert await _judged(rig) == []
        assert rig.service.get_stopped_update_attempt() is None
        assert _read_record(tmp_path) is None

    async def test_dismissing_it_removes_the_record_and_keeps_try_again_for_this_process(self, rigs, tmp_path):
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=_ENDED)
        rig.service.note_start()
        await _judged(rig)

        assert await rig.service.dismiss_stopped_attempt() == {"success": True}

        assert rig.service.get_stopped_update_attempt() is None
        assert _read_record(tmp_path) is None
        assert (await rig.service.get_update_install_state())["try_again"] is True

    async def test_a_dismiss_with_no_stopped_attempt_standing_removes_nothing(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        _leave_record(tmp_path)

        assert await rig.service.dismiss_stopped_attempt() == {"success": True}

        assert _read_record(tmp_path) is not None

    async def test_a_dismiss_during_a_held_attempt_keeps_that_attempt_s_record(self, rigs, tmp_path):
        """A card still on screen from before the press must not take the new attempt's record away."""
        _leave_record(tmp_path, started_by=_RUNNING)
        rig = await _built(rigs, tmp_path, unit_states=_ENDED)
        rig.service.note_start()
        await _judged(rig)
        await rig.service.install_update(_OFFERED)
        assert (await rig.settled())["step"] == "installer_started"
        written = _read_record(tmp_path)
        assert written is not None
        assert written["started_at"] != "2026-09-01T10:00:00Z"

        assert await rig.service.dismiss_stopped_attempt() == {"success": True}

        assert _read_record(tmp_path) == written
        assert rig.service.is_update_in_progress() is True

    async def test_a_new_attempt_ends_the_notice(self, rigs, tmp_path):
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=_ENDED)
        rig.service.note_start()
        await _judged(rig)
        assert rig.service.get_stopped_update_attempt() is not None

        assert await rig.service.install_update(_OFFERED) == {"success": True}

        assert rig.service.get_stopped_update_attempt() is None


class TestAnInstallerStillRunningAtTheNextStart:
    """The installer starts this program itself, and may not have ended when the start looks at the record."""

    async def test_the_record_is_judged_once_the_unit_ends_and_the_judgement_is_pushed(self, rigs, tmp_path):
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=[True, True, False], sleeper=_ParkingSleeper(free=2))

        rig.service.note_start()
        assert rig.service.get_stopped_update_attempt() is None
        assert _read_record(tmp_path) is not None

        pushed = await _judged(rig)

        stopped = {"attempted_version": _OFFERED, "from_version": _RUNNING, "started_at": "2026-09-01T10:00:00Z"}
        assert pushed == [stopped]
        assert rig.service.get_stopped_update_attempt() == stopped
        state = await rig.service.get_update_install_state()
        assert (state["attempt"]["failure"], state["try_again"]) == ("installer_stopped", True)
        assert rig.units.asked == [INSTALLER_UNIT] * 3

    async def test_the_start_asks_the_unit_off_the_loop_and_returns_before_it_answers(
        self, rigs, tmp_path, monkeypatch
    ):
        """A user manager slow to answer must not hold up the start steps after this one."""
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=_ENDED)
        asked_on: list[int] = []

        def is_active(unit: str) -> bool | None:
            asked_on.append(threading.get_ident())
            return FakeTransientUnits.is_active(rig.units, unit)

        monkeypatch.setattr(rig.units, "is_active", is_active)

        rig.service.note_start()
        assert rig.units.asked == []

        assert len(await _judged(rig)) == 1
        assert asked_on
        assert threading.get_ident() not in asked_on

    async def test_the_record_is_read_off_the_loop(self, rigs, tmp_path, monkeypatch):
        """The record is a file under the state root; a slow disk must not hold up the start steps after this one."""
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=_ENDED)
        attempts = rig.service._attempts
        read_on: list[int] = []

        def read(read: Callable[[], UpdateAttemptRecord | None] = attempts.read) -> UpdateAttemptRecord | None:
            read_on.append(threading.get_ident())
            return read()

        monkeypatch.setattr(attempts, "read", read)

        rig.service.note_start()

        assert len(await _judged(rig)) == 1
        assert len(read_on) == 2
        assert threading.get_ident() not in read_on

    async def test_a_press_while_the_unit_is_being_asked_ends_the_question(self, rigs, tmp_path, monkeypatch):
        """The unit is asked off the loop; a press can land before its answer is back."""
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path)
        entered, gate = threading.Event(), threading.Event()
        attempts = rig.service._attempts
        reads: list[UpdateAttemptRecord | None] = []

        def slow_ask(unit: str) -> bool | None:
            entered.set()
            gate.wait(2)
            return False

        def counted_read(read: Callable[[], UpdateAttemptRecord | None] = attempts.read) -> UpdateAttemptRecord | None:
            reads.append(read())
            return reads[-1]

        monkeypatch.setattr(rig.units, "is_active", slow_ask)
        monkeypatch.setattr(attempts, "read", counted_read)
        rig.service.note_start()
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()

        assert await rig.service.install_update(_OFFERED) == {"success": True}
        gate.set()

        assert await _judged(rig) == []
        assert len(reads) == 1, "the record was looked at again after the press"

    async def test_a_push_that_fails_leaves_the_judgement_standing_and_says_only_that(
        self, rigs, tmp_path, monkeypatch, caplog
    ):
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=_ENDED)

        async def failing_emit(name: str, payload: dict[str, Any]) -> None:
            raise ConnectionError("socket gone")

        monkeypatch.setattr(rig.service, "_emit", failing_emit)

        with caplog.at_level(logging.WARNING, logger="test_update_install"):
            rig.service.note_start()
            await _judged(rig)

        assert rig.service.get_stopped_update_attempt() is not None
        assert "the panel could not be told" in caplog.text
        assert "could not be judged" not in caplog.text

    async def test_a_unit_nobody_can_say_about_is_asked_again_rather_than_judged(self, rigs, tmp_path):
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=[None, None, False], sleeper=_ParkingSleeper(free=2))

        rig.service.note_start()

        assert len(await _judged(rig)) == 1

    async def test_a_seam_that_raises_at_the_start_is_nobody_able_to_say(self, rigs, tmp_path, monkeypatch):
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=[False], sleeper=_ParkingSleeper(free=1))
        answers = iter([OSError("bus gone")])

        def first_raises(unit: str) -> bool | None:
            error = next(answers, None)
            if error is not None:
                raise error
            return FakeTransientUnits.is_active(rig.units, unit)

        monkeypatch.setattr(rig.units, "is_active", first_raises)

        rig.service.note_start()
        assert rig.service.get_stopped_update_attempt() is None

        assert len(await _judged(rig)) == 1

    async def test_an_update_that_went_through_is_removed_once_the_unit_ends_and_nothing_is_pushed(
        self, rigs, tmp_path
    ):
        _leave_record(tmp_path)
        rig = await _built(
            rigs, tmp_path, current_version=_OFFERED, unit_states=[True, False], sleeper=_ParkingSleeper(free=1)
        )

        rig.service.note_start()

        assert await _judged(rig) == []
        assert _read_record(tmp_path) is None
        assert rig.service.get_stopped_update_attempt() is None

    async def test_a_press_while_the_unit_still_runs_ends_the_question(self, rigs, tmp_path):
        _leave_record(tmp_path)
        sleeper = _ParkingSleeper(free=1)
        rig = await _built(rigs, tmp_path, unit_states=[True], sleeper=sleeper)
        rig.service.note_start()
        assert await rig.service.install_update(_OFFERED) == {"success": True}

        assert await _judged(rig) == []
        assert rig.service.get_stopped_update_attempt() is None

    async def test_a_press_while_the_record_is_being_judged_wins(self, rigs, tmp_path, monkeypatch):
        """The judgement reads the installer's record off the loop; a press can land before it is back."""
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=[True, False], sleeper=_ParkingSleeper(free=1))
        entered, gate = threading.Event(), threading.Event()

        def slow_read() -> UpdateFailure | None:
            entered.set()
            gate.wait(2)
            return None

        monkeypatch.setattr(rig.service, "_read_update_failure", slow_read)
        rig.service.note_start()
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()

        assert await rig.service.install_update(_OFFERED) == {"success": True}
        gate.set()

        assert await _judged(rig) == []
        assert rig.service.get_stopped_update_attempt() is None

    async def test_shutdown_stops_a_judgement_still_waiting(self, rigs, tmp_path):
        _leave_record(tmp_path)
        rig = await _built(rigs, tmp_path, unit_states=[True])
        rig.service.note_start()
        task = rig.service._judging
        assert task is not None

        await rig.service.shutdown()

        assert task.cancelled()
        assert _read_record(tmp_path) is not None


# ── Leftovers and shutdown ───────────────────────────────────────────────────


class TestLeftoversAndShutdown:
    async def test_leftovers_of_an_earlier_attempt_are_removed(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        os.makedirs(rig.staging_dir)
        with open(os.path.join(rig.staging_dir, "romm-tender-0.9.0.tar.gz"), "wb") as f:
            f.write(b"old")

        rig.service.remove_leftovers()

        assert not os.path.lexists(rig.staging_dir)

    async def test_no_leftovers_is_nothing_to_do(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        rig.service.remove_leftovers()

        assert not os.path.lexists(rig.staging_dir)

    async def test_shutdown_stops_the_watch_and_keeps_the_rule_held(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        await rig.service.install_update(_OFFERED)
        await rig.settled()

        await rig.service.shutdown()

        assert rig.service.is_update_in_progress() is True

    async def test_shutdown_ends_a_download_still_running_on_its_thread(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        ticking = threading.Event()
        ended: list[BaseException] = []

        def endless(url: str, dest: str, progress: Callable[[int, int | None], None] | None) -> None:
            assert progress is not None, (url, dest)
            try:
                for done in range(1, 10_000):
                    progress(done, None)
                    ticking.set()
                    time.sleep(0.001)
            except BaseException as e:
                ended.append(e)
                raise

        rig.service._download_asset = endless
        await rig.service.install_update(_OFFERED)
        await asyncio.get_running_loop().run_in_executor(None, ticking.wait, 5)

        await rig.service.shutdown()
        for _ in range(500):
            if ended:
                break
            await asyncio.sleep(0.01)

        assert len(ended) == 1
        assert "shutting down" in str(ended[0])

    async def test_shutdown_with_no_attempt_is_nothing_to_do(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        await rig.service.shutdown()

        assert rig.service.is_update_in_progress() is False


# ── Which claims a reason names ──────────────────────────────────────────────


class TestTheClaimsAReasonNames:
    """Read from the source: the names are the endpoints' and the leases' own, spelled at their call sites."""

    def test_every_claim_a_reason_names_is_one_the_source_takes(self):
        named = set().union(*(claims_named_by(reason) for reason in WaitReason))

        assert named - claim_names_in_source() == set()

    def test_every_claim_the_prune_conflicts_can_hold_is_classified(self):
        """A new endpoint under the prune rule, retained task or lease is named by a reason or declared read-only.

        Unclassified it would still make a press wait, as ``other_work``; this
        makes the choice a deliberate one.
        """
        named = set().union(*(claims_named_by(reason) for reason in WaitReason))
        held = claims_held_on_the_prune_conflicts()

        # One of each way a claim is taken, so a read that sees nothing fails here.
        one_of_each = {"test_connection", "record_session_start", "start_download", "launch_reconfirm", "sync_complete"}
        assert one_of_each <= held
        assert held - named - READ_ONLY_CLAIMS == set()

    def test_every_read_only_claim_is_one_the_prune_conflicts_can_hold(self):
        assert READ_ONLY_CLAIMS - claims_held_on_the_prune_conflicts() == set()

    def test_every_save_operation_the_update_rule_refuses_counts_as_the_save_sync(self):
        """The save use cases outside the device gate: a restart would cut each short, and the panel calls it save sync.

        Only a call that names the prune rule takes a claim at all.
        """
        save_operations = {
            label
            for where, label in call_sites_with_rule("update") & call_sites_with_rule("prune")
            if where.startswith("services/saves/")
        }

        assert save_operations
        assert save_operations - claims_named_by(WaitReason.SAVE_SYNC) == set()
