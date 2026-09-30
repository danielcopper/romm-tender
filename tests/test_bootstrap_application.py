"""The built backend: its start-up repairs, its network step, its shutdown, and how it is built."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from _factories import _make_services_bundle
from bootstrap import Application, ServicesBundle, WiringConfig, build_application
from fakes.fake_event_sink import FakeEventSink
from fakes.fake_steam_interface import FakeSteamInterface

from domain.app_directories import AppDirectories
from domain.identity import MIN_ROMM_VERSION, PACKAGE_NAME, VERSION
from domain.update_release import UpdateSource
from services.library import LibraryService

if TYPE_CHECKING:
    import pytest

LOGGER = logging.getLogger("test_bootstrap_application")

# Every repair in the order it runs. ``prune_stale_installed_roms`` runs only
# after ``detect_retrodeck_path_change`` succeeded; ``record_save_directories``
# and ``run_due_update_checks`` start a background task rather than performing it.
_REPAIRS = [
    "note_update_outcome",
    "detect_retrodeck_path_change",
    "prune_stale_installed_roms",
    "reconcile_orphaned_sync_runs",
    "prune_orphaned_artwork_cache",
    "prune_orphaned_staging_artwork",
    "prune_orphaned_cover_cache",
    "cleanup_leftover_tmp_files",
    "remove_update_leftovers",
    "note_update_attempt",
    "record_save_directories",
    "run_due_update_checks",
]


class _Recorded:
    """Services whose every start-up and shutdown method records its name, in order, into one list."""

    def __init__(self, *, failing: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self._failing = failing
        self.backfill_started = asyncio.Event()
        self.backfill_release = asyncio.Event()
        self.backfill_ran_to_the_end = False
        self.due_checks_started = asyncio.Event()

    def _step(self, name: str) -> MagicMock:
        def run() -> None:
            self.calls.append(name)
            if name in self._failing:
                raise RuntimeError(f"{name} broke")

        return MagicMock(side_effect=run)

    def _async_step(self, name: str) -> AsyncMock:
        return AsyncMock(side_effect=lambda: self.calls.append(name))

    def _backfill(self) -> Any:
        self.calls.append("record_save_directories")

        async def backfill() -> None:
            self.backfill_started.set()
            try:
                await self.backfill_release.wait()
            except asyncio.CancelledError:
                self.calls.append("backfill cancelled")
                raise
            self.backfill_ran_to_the_end = True

        return backfill()

    def _due_checks(self) -> Any:
        self.calls.append("run_due_update_checks")

        async def due_checks() -> None:
            self.due_checks_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.calls.append("due checks cancelled")
                raise

        return due_checks()

    def bundle(self) -> ServicesBundle:
        return _make_services_bundle(
            update_outcome_service=MagicMock(note_start=self._step("note_update_outcome")),
            migration_service=MagicMock(
                detect_retrodeck_path_change=self._step("detect_retrodeck_path_change"),
                shutdown=self._async_step("migration_service.shutdown"),
            ),
            startup_healing_service=MagicMock(
                prune_stale_installed_roms=self._step("prune_stale_installed_roms"),
                reconcile_orphaned_sync_runs=self._step("reconcile_orphaned_sync_runs"),
            ),
            sgdb_service=MagicMock(prune_orphaned_artwork_cache=self._step("prune_orphaned_artwork_cache")),
            artwork_service=MagicMock(
                prune_orphaned_staging_artwork=self._step("prune_orphaned_staging_artwork"),
                prune_orphaned_cover_cache=self._step("prune_orphaned_cover_cache"),
            ),
            leftover_tmp_cleanup_service=MagicMock(
                cleanup_leftover_tmp_files=self._step("cleanup_leftover_tmp_files"),
            ),
            save_sync_service=MagicMock(record_save_directories_once=self._backfill),
            update_check_service=MagicMock(run_due_checks=self._due_checks),
            update_install_service=MagicMock(
                remove_leftovers=self._step("remove_update_leftovers"),
                note_start=self._step("note_update_attempt"),
                shutdown=self._async_step("update_install_service.shutdown"),
            ),
            sync_service=MagicMock(shutdown=self._step("sync_service.shutdown")),
            prune_service=MagicMock(shutdown=self._async_step("prune_service.shutdown")),
            download_service=MagicMock(shutdown=self._async_step("download_service.shutdown")),
            session_lifecycle_service=MagicMock(shutdown=self._async_step("session_lifecycle_service.shutdown")),
            playtime_service=MagicMock(shutdown=self._async_step("playtime_service.shutdown")),
            connection_service=MagicMock(migrate_legacy_credentials=self._async_step("migrate_legacy_credentials")),
        )


def _application(recorded: _Recorded) -> Application:
    return Application(
        recorded.bundle(),
        logger=LOGGER,
        loop=asyncio.get_running_loop(),
        user_agent="romm-tender/0.0.0-test",
    )


# Every service shutdown, in the order they run.
_SHUTDOWNS = [
    "update_install_service.shutdown",
    "sync_service.shutdown",
    "prune_service.shutdown",
    "download_service.shutdown",
    "migration_service.shutdown",
    "session_lifecycle_service.shutdown",
    "playtime_service.shutdown",
]


class TestTheStartUpRepairs:
    async def test_every_repair_runs_once_in_order(self):
        recorded = _Recorded()
        app = _application(recorded)

        app.run_startup_repairs(lambda _name: None)

        assert recorded.calls == _REPAIRS
        await asyncio.wait_for(app.shutdown(), 5)

    async def test_a_clean_start_reports_nothing(self):
        recorded = _Recorded()
        failures: list[str] = []
        app = _application(recorded)

        app.run_startup_repairs(failures.append)

        assert failures == []
        await asyncio.wait_for(app.shutdown(), 5)

    async def test_a_failing_repair_is_reported_and_the_rest_still_run(self):
        recorded = _Recorded(failing=frozenset({"prune_orphaned_artwork_cache"}))
        failures: list[str] = []
        app = _application(recorded)

        app.run_startup_repairs(failures.append)

        assert failures == ["prune_orphaned_artwork_cache"]
        assert recorded.calls == _REPAIRS
        await asyncio.wait_for(app.shutdown(), 5)

    async def test_the_prune_is_skipped_when_the_detection_fails(self):
        """The prune reads the pending homes the detection writes.

        Without them it deletes the row of every install under the home
        RetroDECK just left whose files are no longer there.
        """
        recorded = _Recorded(failing=frozenset({"detect_retrodeck_path_change"}))
        failures: list[str] = []
        app = _application(recorded)

        app.run_startup_repairs(failures.append)

        assert failures == ["detect_retrodeck_path_change"]
        assert recorded.calls == [name for name in _REPAIRS if name != "prune_stale_installed_roms"]
        await asyncio.wait_for(app.shutdown(), 5)

    async def test_the_backfill_is_started_without_holding_start_up(self):
        recorded = _Recorded()
        app = _application(recorded)

        app.run_startup_repairs(lambda _name: None)
        await asyncio.wait_for(recorded.backfill_started.wait(), 5)

        assert recorded.backfill_ran_to_the_end is False
        recorded.backfill_release.set()
        await asyncio.wait_for(app.shutdown(), 5)

    async def test_the_release_check_is_started_to_run_for_as_long_as_the_backend_runs(self):
        recorded = _Recorded()
        app = _application(recorded)

        app.run_startup_repairs(lambda _name: None)
        await asyncio.wait_for(recorded.due_checks_started.wait(), 5)

        assert "due checks cancelled" not in recorded.calls
        await asyncio.wait_for(app.shutdown(), 5)


class TestTheNetworkStep:
    async def test_it_migrates_legacy_credentials(self):
        recorded = _Recorded()
        app = _application(recorded)

        await app.open_network()

        assert recorded.calls == ["migrate_legacy_credentials"]


class TestShutdown:
    async def test_every_service_is_shut_down_once_in_order(self):
        recorded = _Recorded()
        app = _application(recorded)

        await asyncio.wait_for(app.shutdown(), 5)

        assert recorded.calls == _SHUTDOWNS

    async def test_the_background_tasks_still_running_are_cancelled_first(self):
        recorded = _Recorded()
        app = _application(recorded)
        app.run_startup_repairs(lambda _name: None)
        await asyncio.wait_for(recorded.backfill_started.wait(), 5)
        await asyncio.wait_for(recorded.due_checks_started.wait(), 5)
        recorded.calls.clear()

        await asyncio.wait_for(app.shutdown(), 5)

        assert recorded.calls == ["backfill cancelled", "due checks cancelled", *_SHUTDOWNS]


_UPDATE_SOURCE = UpdateSource(release_api="http://127.0.0.1:9/releases/latest", installed_program=False)


def _directories_at(tmp_path) -> AppDirectories:
    return AppDirectories(
        config_dir=str(tmp_path / "config"),
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        state_dir=str(tmp_path / "state"),
        runtime_dir=str(tmp_path / "run"),
        code_dir=str(tmp_path / "code"),
        bin_dir=str(tmp_path / "home" / ".local" / "bin"),
    )


class TestBuildApplication:
    @staticmethod
    def _build(tmp_path, emit) -> Application:
        return build_application(
            directories=_directories_at(tmp_path),
            update_source=_UPDATE_SOURCE,
            installer_environment=(),
            user_home=str(tmp_path / "home"),
            logger=LOGGER,
            loop=asyncio.get_running_loop(),
            emit=emit,
            steam=FakeSteamInterface(),
        )

    async def test_it_wires_the_services(self, tmp_path):
        app = self._build(tmp_path, FakeEventSink().emit)

        assert isinstance(app.services.sync_service, LibraryService)

    async def test_it_answers_under_the_program_s_identity(self, tmp_path):
        app = self._build(tmp_path, FakeEventSink().emit)

        assert app.user_agent == f"{PACKAGE_NAME}/{VERSION}"

    async def test_the_services_get_the_loop_the_emit_and_the_floor_it_was_given(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """Services emit through the sink itself, so its answer reaches them unchanged."""
        import bootstrap.application

        seen: list[WiringConfig] = []
        real_wire_services = bootstrap.application.wire_services

        def recording_wire_services(cfg: WiringConfig) -> ServicesBundle:
            seen.append(cfg)
            return real_wire_services(cfg)

        monkeypatch.setattr(bootstrap.application, "wire_services", recording_wire_services)
        events = FakeEventSink()

        self._build(tmp_path, events.emit)

        [cfg] = seen
        assert cfg.runtime.emit == events.emit
        assert cfg.runtime.loop is asyncio.get_running_loop()
        assert cfg.min_required_version == MIN_ROMM_VERSION
