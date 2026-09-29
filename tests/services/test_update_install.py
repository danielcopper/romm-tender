"""Tests for UpdateInstallService — installing the last seen release, and what a press waits for."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import tarfile
from dataclasses import dataclass
from typing import Any

import pytest
from fakes.fake_event_sink import FakeEventSink
from fakes.fake_release_download import FakeReleaseDownload
from fakes.fake_steam_interface import FakeSteamInterface
from fakes.fake_transient_units import FakeTransientUnits
from fakes.system_time import FakeClock

from adapters.update_staging import UpdateStagingAdapter
from domain.update_install import INSTALLER_UNIT
from domain.update_outcome import UpdateFailure
from domain.update_release import LatestRelease, ReleaseTarball
from services.update_install import UpdateInstallService, UpdateInstallServiceConfig

_RUNNING = "1.0.0"
_OFFERED = "1.1.0"
_INSTALLER = b"#!/bin/bash\necho installing\n"
_STATE_KEYS = {"offered", "version", "wait_reasons", "paused_downloads", "attempt", "try_again"}
_ENVIRONMENT = (("TENDER_CODE_DIR", "/code"), ("TENDER_PYTHON", "/usr/bin/python3.13"))


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
    """The release check's answer: the switch, and the stored release."""

    def __init__(self, release: LatestRelease | None, *, enabled: bool = True) -> None:
        self.release = release
        self.enabled = enabled

    def is_check_enabled(self) -> bool:
        return self.enabled

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
    enabled: bool = True,
    installed_program: bool = True,
    apps: tuple[str, ...] | None = (),
    frees_at: float | None = None,
    failing: set[str] | None = None,
    checksum_body: bytes = b"sidecar\n",
    unit_refusal: str | None = None,
    unit_states: list[bool | None] | None = None,
    sleeper: _ParkingSleeper | None = None,
    failure_record: UpdateFailure | None = None,
) -> _Rig:
    body = body if body is not None else _tarball()
    release = release if release is not None else _release(body=body)
    assert release.tarball is not None
    work = _Work()
    steam = FakeSteamInterface(apps=apps, frees_at=frees_at)
    downloads = FakeReleaseDownload(
        bodies={release.tarball.url: body, release.tarball.checksum_url: checksum_body}, failing=failing
    )
    units = FakeTransientUnits(refusal=unit_refusal, states=unit_states)
    events = FakeEventSink()
    staging = UpdateStagingAdapter(directory=str(tmp_path / "cache" / "update"))
    releases = _Releases(release, enabled=enabled)
    sleeper = sleeper if sleeper is not None else _ParkingSleeper()
    service = UpdateInstallService(
        config=UpdateInstallServiceConfig(
            releases=releases,
            current_version=_RUNNING,
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
            read_update_failure=lambda: failure_record,
            download_asset=downloads,
            staging=staging,
            units=units,
            installer_environment=_ENVIRONMENT,
            emit=events.emit,
            clock=FakeClock(),
            sleeper=sleeper,
            loop=asyncio.get_running_loop(),
            logger=logging.getLogger("test_update_install"),
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

    async def test_a_switched_off_check_offers_nothing(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, enabled=False)

        assert (await rig.service.get_update_install_state())["offered"] is False

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

    async def test_the_reload_limit_carries_the_time_it_frees_up(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path, frees_at=1_800_000_600.0)

        reasons = (await rig.service.get_update_install_state())["wait_reasons"]

        assert reasons == [{"reason": "interface_reload_limit", "frees_at": 1_800_000_600.0}]

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
            "interface_reload_limit",
        ]

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
        """Both presses pass the first check; the one that reads Steam second finds the rule held."""
        rig = await _built(rigs, tmp_path)
        first = asyncio.ensure_future(rig.service.install_update(_OFFERED))
        second = asyncio.ensure_future(rig.service.install_update(_OFFERED))

        answers = await asyncio.gather(first, second)

        assert sorted(answer["success"] for answer in answers) == [False, True]
        assert {answer.get("reason") for answer in answers} == {None, "update_in_progress"}
        assert len(rig.units.starts) <= 1


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
        # The clock never moves, so after the first tick only the final one passes.
        size = len(_tarball())
        assert [payload["bytes_done"] for payload in with_bytes] == [4, size]

    async def test_the_answer_while_the_installer_runs_carries_the_attempt_and_no_reasons(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)
        await rig.service.install_update(_OFFERED)
        await rig.settled()
        rig.work.save_sync = True

        state = await rig.service.get_update_install_state()

        assert state["attempt"]["step"] == "installer_started"
        assert state["wait_reasons"] == []
        assert state["try_again"] is False

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

    async def test_shutdown_with_no_attempt_is_nothing_to_do(self, rigs, tmp_path):
        rig = await _built(rigs, tmp_path)

        await rig.service.shutdown()

        assert rig.service.is_update_in_progress() is False
