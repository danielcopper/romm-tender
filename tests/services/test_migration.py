import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock

import pytest
from _factories import (
    _make_conflict_rules,
    _make_prune_conflicts,
    _record_operations_at_lease,
    _refused_by_conflict_rule,
)
from fakes.fake_core_info_provider import FakeCoreInfoProvider, FakeSandboxLauncher
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_firmware_resolver import FakeFirmwareResolver
from fakes.fake_migration_file_store import FakeMigrationFileStore
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_relaunch_options_resolver import FakeRelaunchOptionsResolver
from fakes.fake_retrodeck_paths import FakeRetroDeckPaths
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.running_loop import running_loop

from adapters.migration_file import MigrationFileAdapter
from lib.prune_conflicts import PruneConflicts
from services.active_core_resolver import ActiveCoreResolver, ActiveCoreResolverConfig
from services.migration import MigrationService, MigrationServiceConfig
from services.relaunch_options_resolver import RelaunchOptionsResolver, RelaunchOptionsResolverConfig


class RecordingEmitter:
    """Append-only emit recorder usable as an ``EventEmitter``.

    Stores ``(event_name, payload)`` pairs in ``calls`` so tests can assert
    on the observable emit contract without resorting to ``MagicMock``.
    The call signature mirrors the ``EventEmitter`` Protocol exactly so
    basedpyright accepts the fake wherever ``EventEmitter`` is expected.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def __call__(self, event: str, payload: object, /) -> bool:
        self.calls.append((event, payload))
        return True


class RecordingSaveDirectories:
    """Stands in for ``SaveService.rerecord_save_directories`` and counts its calls.

    Called through ``provide`` the way the composition root's ``LateBinding``
    hands the live recorder over. *error* makes the call raise. *gate*, when
    given, is asked at call time — what a sync arriving mid-re-record would ask.
    """

    def __init__(self, error: Exception | None = None, gate: Any = None) -> None:
        self.calls = 0
        self._error = error
        self._gate = gate
        self.pending_while_recording: list[bool] = []

    def provide(self) -> Self:
        return self

    async def __call__(self) -> None:
        self.calls += 1
        if self._gate is not None:
            self.pending_while_recording.append(self._gate())
        if self._error is not None:
            raise self._error


@dataclass
class MigrationHarness:
    """The migration service and the seams its tests seed or assert against.

    ``uow`` is the one :class:`FakeUnitOfWork` the migration service and the
    relaunch resolvers behind it read and write, so a row a test seeds there is
    the row the migration sees. ``core_info`` is the core-info fake the relaunch
    commands are baked through, so a test can seed ``available_cores``.
    """

    service: MigrationService
    uow: FakeUnitOfWork
    core_info: FakeCoreInfoProvider
    save_directories: RecordingSaveDirectories
    prune_conflicts: PruneConflicts


@pytest.fixture
def migration(logger) -> MigrationHarness:
    settings: dict[str, Any] = {"romm_url": "", "romm_user": "", "romm_pass": "", "enabled_platforms": {}}
    prune_conflicts = _make_prune_conflicts()
    uow = FakeUnitOfWork()
    # The real ActiveCoreResolver folds the DB override over this fake's
    # es_systems default — the seam MigrationService re-bakes through on
    # relocation, so a relaunch test can assert a per-game emulator_override
    # re-bakes the ``-e`` form post-move.
    core_info = FakeCoreInfoProvider()
    active_core = ActiveCoreResolver(
        config=ActiveCoreResolverConfig(
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            core_info=core_info,
            sandbox_launcher=FakeSandboxLauncher(),
            platform_core_reader=FakePlatformCoreReader(),
            resolve_system=lambda platform_slug, platform_fs_slug=None: platform_slug,
            logger=logger,
        ),
    )

    # Real RelaunchOptionsResolver over the shared fake UoW + the active-core
    # resolver so the migration relaunch-emit integration tests bake real launch
    # commands from the relocated rom_installs.file_path.
    relaunch_options = RelaunchOptionsResolver(
        config=RelaunchOptionsResolverConfig(
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            active_core=active_core,
            disc_resolver=FakeDiscResolver(),
            loop=running_loop(),
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
        ),
    )

    save_directories = RecordingSaveDirectories()
    service = MigrationService(
        config=MigrationServiceConfig(
            migration_file_store=MigrationFileAdapter(),
            settings=settings,
            loop=running_loop(),
            logger=logger,
            settings_persister=FakeSettingsPersister(),
            emit=RecordingEmitter(),
            firmware_resolver=FakeFirmwareResolver(),
            retrodeck_paths=FakeRetroDeckPaths(),
            relaunch_options=relaunch_options,
            save_directories=save_directories.provide,
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
        ),
    )
    return MigrationHarness(
        service=service,
        uow=uow,
        core_info=core_info,
        save_directories=save_directories,
        prune_conflicts=prune_conflicts,
    )


def _seed_install(uow, rom_id, *, file_path, rom_dir=None, system="n64", platform_slug="", app_id=None):
    """Seed a Rom (FK parent) then its RomInstall into the shared fake UoW.

    ``rom_dir`` defaults to ``None`` (single-file ROM); pass a dedicated
    directory for a folder-backed (multi-file) ROM. ``app_id`` defaults to
    ``None`` (unbound ROM); pass an int to seed a bound shortcut so the
    re-resolve step picks the install up.
    """
    from domain.rom import Rom
    from domain.rom_install import RomInstall

    with uow:
        uow.roms.save(
            Rom(
                rom_id=rom_id,
                platform_slug=platform_slug or system,
                name=f"Game {rom_id}",
                fs_name=f"game{rom_id}",
                shortcut_app_id=app_id,
                last_synced_at="2025-01-01T00:00:00",
            )
        )
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=rom_id,
                file_path=file_path,
                rom_dir=rom_dir,
                platform_slug=platform_slug,
                system=system,
                installed_at="2025-01-01T00:00:00",
            )
        )


def _seed_bios(uow, *, platform_slug, file_name, file_path, firmware_id=None):
    """Seed a ``BiosFile`` into the shared fake UoW the way FirmwareService writes it.

    ``bios_files`` has no FK onto ``roms``, so no parent row is required (unlike
    ``_seed_install``). Mirrors ``FirmwareDownloader._download_firmware_post_io``'s
    ``uow.bios_files.save(BiosFile.mark_downloaded(...))`` write.
    """
    from domain.bios_file import BiosFile

    with uow:
        uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug=platform_slug,
                file_name=file_name,
                file_path=file_path,
                downloaded_at="2025-01-01T00:00:00",
                firmware_id=firmware_id,
            )
        )


@pytest.fixture(autouse=True)
async def _set_event_loop(migration):
    """Bind the migration service to the running event loop."""
    migration.service._loop = asyncio.get_running_loop()


class _RecordingLoop:
    """Drop-in loop substitute that captures and immediately closes scheduled coroutines.

    Mirrors what the original tests built ad-hoc with ``MagicMock`` for
    ``loop.create_task``: schedule receives the coroutine and stores it
    (closing it so no pending-task warning fires), and the count is
    inspectable via ``len(tasks)``. Use this when a test wants to assert
    *whether* a coroutine was scheduled without actually pumping the
    event loop.
    """

    def __init__(self) -> None:
        self.tasks: list[object] = []

    def create_task(self, coro):
        coro.close()
        self.tasks.append(coro)
        return


class TestPathChangeDetection:
    def test_first_run_stores_path(self, migration, tmp_path):
        """First run (empty stored path) stores current path, no event."""

        loop = _RecordingLoop()
        migration.service._loop = loop

        fake_home = str(tmp_path / "retrodeck")
        os.makedirs(fake_home, exist_ok=True)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=fake_home)
        migration.service.detect_retrodeck_path_change()

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") == fake_home
        # No event emitted on first run
        assert loop.tasks == []
        assert migration.service._emit.calls == []

    def test_no_change_no_notification(self, migration, tmp_path):
        """Same path as stored — no event, no state change."""

        fake_home = str(tmp_path / "retrodeck")
        os.makedirs(fake_home, exist_ok=True)
        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", fake_home)
        loop = _RecordingLoop()
        migration.service._loop = loop

        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=fake_home)
        migration.service.detect_retrodeck_path_change()

        assert loop.tasks == []
        assert migration.service._emit.calls == []

    def test_two_spellings_of_one_home_are_not_a_move(self, migration, tmp_path):
        """#1838: a marker naming the live home through a symlink is the same directory.

        A home stored before the roots were resolved is spelled the way
        ``retrodeck.json`` spelled it. Reading that as a move would raise the
        migration banner and offer to relocate a directory onto itself — whose
        Overwrite branch deletes the destination first.
        """

        base = tmp_path.resolve()
        real_home = base / "var" / "home" / "player" / "retrodeck"
        real_home.mkdir(parents=True)
        (base / "home").symlink_to(base / "var" / "home", target_is_directory=True)
        stored_home = str(base / "home" / "player" / "retrodeck")
        assert stored_home != str(real_home)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", stored_home)
        loop = _RecordingLoop()
        migration.service._loop = loop
        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=str(real_home))

        migration.service.detect_retrodeck_path_change()

        assert loop.tasks == []
        assert migration.service._emit.calls == []
        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path_previous") is None
            assert uow.kv_config.get("retrodeck_home_path_hops") is None
            # Nothing moved, so nothing is written — the marker keeps the
            # spelling it was stored with and is resolved again next startup.
            assert uow.kv_config.get("retrodeck_home_path") == stored_home

    def test_a_pending_marker_naming_the_live_home_is_cleared(self, migration, tmp_path):
        """A marker naming the home RetroDECK is already on is dropped.

        Otherwise it stands until the user migrates or dismisses, over a home
        that was never left.
        """

        base = tmp_path.resolve()
        real_home = base / "var" / "home" / "player" / "retrodeck"
        real_home.mkdir(parents=True)
        (base / "home").symlink_to(base / "var" / "home", target_is_directory=True)
        linked_home = str(base / "home" / "player" / "retrodeck")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", linked_home)
            uow.kv_config.set("retrodeck_home_path_previous", linked_home)
        loop = _RecordingLoop()
        migration.service._loop = loop
        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=str(real_home))

        migration.service.detect_retrodeck_path_change()

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path_previous") is None
            assert uow.kv_config.get("retrodeck_home_path_hops") is None
        assert migration.service.is_retrodeck_migration_pending() is False
        assert loop.tasks == []

    def test_a_pending_hop_that_was_really_left_survives_the_clear(self, migration, tmp_path):
        """Only the marker naming the live home is dropped; a genuine hop stays pending."""

        home = str(tmp_path / "retrodeck")
        os.makedirs(home, exist_ok=True)
        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", home)
            uow.kv_config.set("retrodeck_home_path_previous", home)
            uow.kv_config.set("retrodeck_home_path_hops", json.dumps([str(tmp_path / "sd-card" / "retrodeck")]))
        migration.service._loop = _RecordingLoop()
        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=home)

        migration.service.detect_retrodeck_path_change()

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path_previous") == str(tmp_path / "sd-card" / "retrodeck")
            assert uow.kv_config.get("retrodeck_home_path_hops") is None

    async def test_a_home_that_really_moved_is_still_a_change(self, migration, tmp_path):
        """Comparing directories must not swallow a real move, including one already gone.

        The gone home sits under a symlinked prefix, so ``realpath`` really does
        rewrite it — it follows the links that still exist and leaves the
        missing tail as spelled. What it answers with is still a different
        directory from the one RetroDECK reports now, which is what makes this a
        move rather than a spelling.
        """

        base = tmp_path.resolve()
        (base / "var" / "home" / "player").mkdir(parents=True)
        (base / "home").symlink_to(base / "var" / "home", target_is_directory=True)
        gone_home = str(base / "home" / "player" / "sd-card" / "retrodeck")
        new_home = str(base / "internal" / "retrodeck")
        os.makedirs(new_home, exist_ok=True)
        assert not os.path.exists(gone_home)
        # The marker really is rewritten by resolving, and still is not the live home.
        resolved_gone = os.path.realpath(gone_home)
        assert resolved_gone != gone_home
        assert resolved_gone != new_home

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", gone_home)
        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=new_home)

        migration.service.detect_retrodeck_path_change()
        await asyncio.sleep(0)

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") == new_home
            # What is recorded as pending is the directory the marker named,
            # not the spelling it was stored under.
            assert uow.kv_config.get("retrodeck_home_path_previous") == resolved_gone
        event, payload = migration.service._emit.calls[0]
        assert event == "retrodeck_path_changed"
        assert payload["old_path"] == resolved_gone
        assert payload["new_path"] == new_home

    async def test_path_change_emits_event(self, migration, tmp_path):
        """Path changed — stores both old and new, emits event."""

        old_home = str(tmp_path / "old_retrodeck")
        new_home = str(tmp_path / "new_retrodeck")
        os.makedirs(new_home, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", old_home)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=new_home)
        migration.service.detect_retrodeck_path_change()

        # ``create_task`` schedules the emit coroutine on the running loop —
        # yield once so the scheduled coroutine runs and the emitter records.
        await asyncio.sleep(0)

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") == new_home
            assert uow.kv_config.get("retrodeck_home_path_previous") == old_home

        emit_calls = migration.service._emit.calls
        assert len(emit_calls) == 1
        event, payload = emit_calls[0]
        assert event == "retrodeck_path_changed"
        assert isinstance(payload, dict)
        assert payload["old_path"] == old_home
        assert payload["new_path"] == new_home
        # Path-change emit does NOT carry ``cleared`` — only the auto-clear emit does.
        assert "cleared" not in payload

    def test_empty_current_home_no_action(self, migration, tmp_path):
        """If ``retrodeck_paths`` returns empty string, do nothing."""

        loop = _RecordingLoop()
        migration.service._loop = loop

        migration.service._retrodeck_paths = FakeRetroDeckPaths(home="")
        migration.service.detect_retrodeck_path_change()

        assert loop.tasks == []
        assert migration.service._emit.calls == []
        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") is None

    async def test_detect_path_change_auto_clears_when_reverted_to_previous(self, migration, tmp_path):
        """User reverted RetroDECK to the previous home — drop the marker, emit cleared event."""

        old_home = str(tmp_path / "old_retrodeck")
        new_home = str(tmp_path / "new_retrodeck")
        os.makedirs(old_home, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", new_home)
            uow.kv_config.set("retrodeck_home_path_previous", old_home)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=old_home)
        migration.service.detect_retrodeck_path_change()

        # ``create_task`` schedules the emit coroutine on the running loop —
        # yield once so the scheduled coroutine runs and the emitter records.
        await asyncio.sleep(0)

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") == old_home
            assert uow.kv_config.get("retrodeck_home_path_previous") is None

        emit_calls = migration.service._emit.calls
        assert len(emit_calls) == 1
        event, payload = emit_calls[0]
        assert event == "retrodeck_path_changed"
        assert isinstance(payload, dict)
        assert payload["cleared"] is True
        assert payload["old_path"] == old_home
        assert payload["new_path"] == old_home

    async def test_detect_path_change_auto_clear_emits_cleared_event(self, migration, tmp_path):
        """Auto-clear MUST emit retrodeck_path_changed with cleared=True so the
        frontend listener can dismiss any pending migration UI."""

        old_home = str(tmp_path / "old_retrodeck")
        new_home = str(tmp_path / "new_retrodeck")
        os.makedirs(old_home, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", new_home)
            uow.kv_config.set("retrodeck_home_path_previous", old_home)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=old_home)
        migration.service.detect_retrodeck_path_change()

        # ``create_task`` schedules the emit coroutine on the running loop —
        # yield once so the scheduled coroutine runs and the emitter records.
        await asyncio.sleep(0)

        emit_calls = migration.service._emit.calls
        assert len(emit_calls) == 1
        event, payload = emit_calls[0]
        assert event == "retrodeck_path_changed"
        assert isinstance(payload, dict)
        assert payload["cleared"] is True
        assert payload["old_path"] == old_home
        assert payload["new_path"] == old_home


class TestIsRetroDeckMigrationPending:
    def test_is_retrodeck_migration_pending_returns_false_when_unset(self, migration):
        with migration.uow as uow:
            uow.kv_config.delete("retrodeck_home_path_previous")
        assert migration.service.is_retrodeck_migration_pending() is False

    def test_is_retrodeck_migration_pending_returns_true_when_set(self, migration):
        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", "/some/old/path")
        assert migration.service.is_retrodeck_migration_pending() is True

    def test_is_retrodeck_migration_pending_returns_false_for_empty_string(self, migration):
        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", "")
        assert migration.service.is_retrodeck_migration_pending() is False


class TestDismissRetroDeckMigration:
    def test_dismiss_retrodeck_migration_clears_marker(self, migration):
        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", "/old/path")

        result = migration.service.dismiss_retrodeck_migration()

        assert result == {"success": True}
        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path_previous") is None

    def test_dismiss_retrodeck_migration_idempotent_when_no_marker(self, migration):
        with migration.uow as uow:
            uow.kv_config.delete("retrodeck_home_path_previous")

        result = migration.service.dismiss_retrodeck_migration()

        assert result == {"success": True}
        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path_previous") is None


class TestMigrateRetroDeckFiles:
    @pytest.mark.asyncio
    async def test_no_migration_needed(self, migration):
        """No previous path — nothing to migrate."""

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is False
        assert "No path migration needed" in result["message"]

    @pytest.mark.asyncio
    async def test_migrate_roms(self, migration, tmp_path):
        """Moves ROM files from old to new path, updates state."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        new_rom = os.path.join(new_home, "roms", "n64", "zelda.z64")

        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64")

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True
        assert result["roms_moved"] == 1
        assert os.path.exists(new_rom)
        assert not os.path.exists(old_rom)
        assert migration.uow.committed is True
        with migration.uow as uow:
            install = uow.rom_installs.get(1)
            assert install.file_path == new_rom
            # Single-file ROM owns no folder before or after migration.
            assert install.rom_dir is None

    @pytest.mark.asyncio
    async def test_a_pending_home_that_is_the_live_home_moves_and_destroys_nothing(self, migration, tmp_path):
        """Acting on such a marker must not treat the live home as a move source.

        Source and destination would be the same path, and Overwrite removes the
        destination before moving. What prevents it is the sweep dropping any
        pending home equal to the live one — a comparison that only holds
        because both sides are resolved.
        """

        base = tmp_path.resolve()
        rom = base / "var" / "home" / "player" / "retrodeck" / "roms" / "n64" / "zelda.z64"
        rom.parent.mkdir(parents=True)
        rom.write_text("rom data")
        (base / "home").symlink_to(base / "var" / "home", target_is_directory=True)
        linked_home = str(base / "home" / "player" / "retrodeck")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", linked_home)
            uow.kv_config.set("retrodeck_home_path_previous", linked_home)
        _seed_install(migration.uow, 1, file_path=str(rom), system="n64")

        result = await migration.service.migrate_retrodeck_files("overwrite")

        assert result["success"] is True
        assert result["roms_moved"] == 0
        assert rom.read_text() == "rom data"

    @pytest.mark.asyncio
    async def test_migrating_into_a_symlinked_home_records_the_resolved_path(self, migration, tmp_path):
        """#1838: both markers name directories, so what is recorded is the resolved path.

        A migration left pending across the upgrade carries markers spelled the
        way ``retrodeck.json`` spelled them. The new path each relocated record
        gets is built from the destination marker, and a path recorded through a
        symlink is one the uninstall guard later refuses.
        """

        base = tmp_path.resolve()
        old_rom = base / "old" / "roms" / "n64" / "zelda.z64"
        old_rom.parent.mkdir(parents=True)
        old_rom.write_text("rom data")
        (base / "new" / "retrodeck").mkdir(parents=True)
        (base / "home").symlink_to(base, target_is_directory=True)
        linked_new_home = str(base / "home" / "new" / "retrodeck")
        assert linked_new_home != os.path.realpath(linked_new_home)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", str(base / "old"))
            uow.kv_config.set("retrodeck_home_path", linked_new_home)
        _seed_install(migration.uow, 1, file_path=str(old_rom), system="n64")

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["roms_moved"] == 1
        with migration.uow as uow:
            assert uow.rom_installs.get(1).file_path == str(base / "new" / "retrodeck" / "roms" / "n64" / "zelda.z64")

    @pytest.mark.asyncio
    async def test_migration_records_applied_launch_options_for_bound_rom(self, migration, tmp_path):
        """After the home-move re-bake, each bound ROM's recorded applied state is
        updated to the emitted relaunch command, so the next sync skips the
        now-correct (relocated) shortcut instead of re-touching it (#1383)."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64", app_id=9001)

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True

        # The emitted migration_relaunch_options item and the recorded applied match.
        relaunch = [
            payload for event, payload in migration.service._emit.calls if event == "migration_relaunch_options"
        ]
        assert len(relaunch) == 1
        item = next(i for i in relaunch[0]["items"] if i["app_id"] == 9001)
        with migration.uow as uow:
            rom = uow.roms.get(1)
        assert rom is not None
        assert rom.applied_launch_options == item["launch_options"]
        assert rom.applied_launch_options != ""  # a real re-baked command, not the placeholder

    @pytest.mark.asyncio
    async def test_migrate_multi_file_moves_whole_rom_dir_with_siblings(self, migration, tmp_path):
        """Regression (#784 data-loss): a multi-file ROM moves its WHOLE rom_dir.

        The launch file (an auto-generated ``.m3u``) sits directly in the
        dedicated extract dir, so ``dirname(file_path) == rom_dir`` exactly as
        for a single-file ROM. The old path-shape heuristic moved only the
        launch file and orphaned the sibling disc files. With the rom_dir model
        the whole directory migrates as a unit — every sibling (here
        ``disc2.bin``) must land at the new location.
        """

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom_dir = os.path.join(old_home, "roms", "psx", "FF7")
        new_rom_dir = os.path.join(new_home, "roms", "psx", "FF7")
        old_launch = os.path.join(old_rom_dir, "FF7.m3u")
        new_launch = os.path.join(new_rom_dir, "FF7.m3u")
        old_disc2 = os.path.join(old_rom_dir, "disc2.bin")
        new_disc2 = os.path.join(new_rom_dir, "disc2.bin")

        os.makedirs(old_rom_dir)
        with open(old_launch, "w") as f:
            f.write("disc1.bin\ndisc2.bin\n")
        with open(os.path.join(old_rom_dir, "disc1.bin"), "w") as f:
            f.write("disc1 data")
        with open(old_disc2, "w") as f:
            f.write("disc2 data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_launch, rom_dir=old_rom_dir, system="psx", platform_slug="psx")

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        # One ROM moved — the moved directory counts as a single ROM.
        assert result["roms_moved"] == 1
        # The whole directory moved: launch file AND the sibling disc travelled.
        assert os.path.exists(new_launch)
        assert os.path.exists(new_disc2)
        assert not os.path.exists(old_rom_dir)
        # The sibling disc the data-loss bug orphaned is at the new location.
        with open(new_disc2) as f:
            assert f.read() == "disc2 data"
        assert migration.uow.committed is True
        with migration.uow as uow:
            install = uow.rom_installs.get(1)
            assert install.rom_dir == new_rom_dir
            assert install.file_path == new_launch

    @pytest.mark.asyncio
    async def test_migrate_single_file_moves_only_the_file(self, migration, tmp_path):
        """A single-file ROM (``rom_dir`` is ``None``) moves only its launch file.

        Sibling ROMs sharing the platform's flat ``<roms>/<system>`` directory
        must NOT be dragged along — only this ROM's file moves.
        """

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_dir = os.path.join(old_home, "roms", "n64")
        old_rom = os.path.join(old_dir, "zelda.z64")
        new_rom = os.path.join(new_home, "roms", "n64", "zelda.z64")
        sibling = os.path.join(old_dir, "mario.z64")

        os.makedirs(old_dir)
        with open(old_rom, "w") as f:
            f.write("zelda")
        with open(sibling, "w") as f:
            f.write("mario")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, rom_dir=None, system="n64")

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["roms_moved"] == 1
        assert os.path.exists(new_rom)
        assert not os.path.exists(old_rom)
        # The unrelated sibling ROM stays in the old shared dir — not dragged along.
        assert os.path.exists(sibling)
        with migration.uow as uow:
            install = uow.rom_installs.get(1)
            assert install.file_path == new_rom
            assert install.rom_dir is None

    @pytest.mark.asyncio
    async def test_migrate_bios(self, migration, tmp_path):
        """Moves tracked BIOS files from old to new path."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_bios = os.path.join(old_home, "bios", "scph5501.bin")
        new_bios = os.path.join(new_home, "bios", "scph5501.bin")

        os.makedirs(os.path.dirname(old_bios))
        with open(old_bios, "w") as f:
            f.write("bios data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_bios(migration.uow, platform_slug="psx", file_name="scph5501.bin", file_path=old_bios, firmware_id=42)

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True
        assert result["bios_moved"] == 1
        # File physically moved to the new RetroDECK home.
        assert os.path.exists(new_bios)
        # Persisted BiosFile.file_path updated in SQLite, and the write committed.
        with migration.uow as uow:
            persisted = uow.bios_files.get("psx", "scph5501.bin")
            assert persisted is not None
            assert persisted.file_path == new_bios
        assert migration.uow.committed is True

    @pytest.mark.asyncio
    async def test_migrate_conflicts_need_confirmation(self, migration, tmp_path):
        """Destination file already exists — first call returns conflicts for user decision."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        new_rom = os.path.join(new_home, "roms", "n64", "zelda.z64")

        os.makedirs(os.path.dirname(old_rom))
        os.makedirs(os.path.dirname(new_rom))
        with open(old_rom, "w") as f:
            f.write("old data")
        with open(new_rom, "w") as f:
            f.write("new data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64")

        # First call with no strategy returns conflicts
        result = await migration.service.migrate_retrodeck_files()
        assert result["needs_confirmation"] is True
        assert result["conflict_count"] == 1
        assert "zelda.z64" in result["conflicts"]
        # Nothing moved yet
        with open(new_rom) as f:
            assert f.read() == "new data"
        with open(old_rom) as f:
            assert f.read() == "old data"

    @pytest.mark.asyncio
    async def test_migrate_conflict_overwrite(self, migration, tmp_path):
        """Overwrite strategy replaces destination with source."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        new_rom = os.path.join(new_home, "roms", "n64", "zelda.z64")

        os.makedirs(os.path.dirname(old_rom))
        os.makedirs(os.path.dirname(new_rom))
        with open(old_rom, "w") as f:
            f.write("old data")
        with open(new_rom, "w") as f:
            f.write("new data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64")

        result = await migration.service.migrate_retrodeck_files("overwrite")
        assert result["success"] is True
        assert result["roms_moved"] == 1
        with open(new_rom) as f:
            assert f.read() == "old data"
        with migration.uow as uow:
            assert uow.rom_installs.get(1).file_path == new_rom

    @pytest.mark.asyncio
    async def test_migrate_conflict_skip(self, migration, tmp_path):
        """Skip strategy keeps destination file, updates state path."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        new_rom = os.path.join(new_home, "roms", "n64", "zelda.z64")

        os.makedirs(os.path.dirname(old_rom))
        os.makedirs(os.path.dirname(new_rom))
        with open(old_rom, "w") as f:
            f.write("old data")
        with open(new_rom, "w") as f:
            f.write("new data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64")

        result = await migration.service.migrate_retrodeck_files("skip")
        assert result["success"] is True
        assert result["roms_moved"] == 1
        # Destination file preserved
        with open(new_rom) as f:
            assert f.read() == "new data"
        # Install record updated to new path
        with migration.uow as uow:
            assert uow.rom_installs.get(1).file_path == new_rom

    @staticmethod
    def _conflicting_rom(migration, tmp_path) -> None:
        """A pending home move whose one ROM exists at both ends."""
        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        new_rom = os.path.join(new_home, "roms", "n64", "zelda.z64")
        for path, data in ((old_rom, "old data"), (new_rom, "new data")):
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(data)
        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64")

    @pytest.mark.asyncio
    async def test_the_save_directories_are_recorded_again_once_the_files_moved(self, migration, tmp_path):
        self._conflicting_rom(migration, tmp_path)

        result = await migration.service.migrate_retrodeck_files("skip")

        assert result["success"] is True
        assert migration.save_directories.calls == 1

    @pytest.mark.asyncio
    async def test_a_sync_during_the_re_record_still_finds_the_migration_pending(self, migration, tmp_path):
        # The markers clear with the relocations, before the re-record: without
        # holding the gate, a sync in that window meets the old home's record.
        self._conflicting_rom(migration, tmp_path)
        service = migration.service
        watching = RecordingSaveDirectories(gate=service.is_retrodeck_migration_pending)
        service._save_directories = watching.provide

        result = await migration.service.migrate_retrodeck_files("skip")

        assert result["success"] is True
        assert watching.pending_while_recording == [True]
        assert service.is_retrodeck_migration_pending() is False

    @pytest.mark.asyncio
    async def test_the_status_stays_pending_during_the_re_record(self, migration, tmp_path):
        # The panel reads this status; answering "not pending" while syncs are
        # still held off would let it drop the migration mid-run.
        self._conflicting_rom(migration, tmp_path)
        service = migration.service
        seen: list[dict[str, Any]] = []

        class _Watching(RecordingSaveDirectories):
            async def __call__(self) -> None:
                seen.append(await service.get_migration_status())

        service._save_directories = _Watching().provide

        await migration.service.migrate_retrodeck_files("skip")

        assert seen[0]["pending"] is True
        assert seen[0]["old_path"] == str(tmp_path / "old")
        assert seen[0]["new_path"] == str(tmp_path / "new")
        assert await service.get_migration_status() == {"pending": False}

    @pytest.mark.asyncio
    async def test_the_status_keeps_the_counts_the_run_started_with_for_the_whole_run(self, migration, tmp_path):
        # Counted again once a file has moved, the blocked page would read fewer
        # files, and after the run has cleared the markers "0 ROM(s), 0 BIOS,
        # 0 save(s) to migrate". Asked twice: after the files moved while the
        # markers still stand, and during the re-record once they are gone.
        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        old_save = os.path.join(old_home, "saves", "n64", "zelda.srm")
        for path in (old_rom, old_save):
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write("data")
        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64")
        service = migration.service
        service._retrodeck_paths = FakeRetroDeckPaths(
            home=new_home,
            saves=os.path.join(new_home, "saves"),
            roms=os.path.join(new_home, "roms"),
            bios=os.path.join(new_home, "bios"),
        )
        before = await service.get_migration_status()
        loop = asyncio.get_running_loop()
        seen: list[dict[str, Any]] = []
        apply_relocations = service._apply_relocations

        def _asking_after_the_moves(*args: Any, **kwargs: Any) -> None:
            status = asyncio.run_coroutine_threadsafe(service.get_migration_status(), loop)
            seen.append(status.result(timeout=5))
            apply_relocations(*args, **kwargs)

        class _Watching(RecordingSaveDirectories):
            async def __call__(self) -> None:
                seen.append(await service.get_migration_status())

        service._apply_relocations = _asking_after_the_moves
        service._save_directories = _Watching().provide

        result = await migration.service.migrate_retrodeck_files("skip")

        assert result["success"] is True
        assert os.path.exists(os.path.join(new_home, "saves", "n64", "zelda.srm"))
        assert not os.path.exists(old_save)
        assert (before["roms_count"], before["saves_count"]) == (1, 1)
        assert seen == [before, before]

    @pytest.mark.asyncio
    async def test_a_run_is_running_while_it_moves_files_and_a_pending_one_is_not(self, migration, tmp_path):
        """What an update waits for: a migration that is moving files, never one the user has not answered."""
        self._conflicting_rom(migration, tmp_path)
        service = migration.service
        seen: list[bool] = []

        async def _watched_run(*_args: Any) -> dict[str, Any]:
            seen.append(service.is_retrodeck_migration_running())
            return {"success": True}

        assert service.is_retrodeck_migration_pending() is True
        assert service.is_retrodeck_migration_running() is False
        service._run_migration = _watched_run

        await migration.service.migrate_retrodeck_files("skip")

        assert seen == [True]
        assert service.is_retrodeck_migration_running() is False

    @pytest.mark.asyncio
    async def test_a_run_that_raises_leaves_nothing_in_flight(self, migration, tmp_path):
        # Left behind, the counter would hold every sync off and the status would
        # report a migration nobody is running, until the process restarts.
        self._conflicting_rom(migration, tmp_path)
        service = migration.service

        async def _failing_run(*_args: Any) -> dict[str, Any]:
            assert service.is_retrodeck_migration_pending() is True
            raise RuntimeError("boom")

        service._run_migration = _failing_run

        with pytest.raises(RuntimeError, match="boom"):
            await migration.service.migrate_retrodeck_files("skip")

        assert service._migrations_in_flight == 0
        assert service._status_in_flight is None

    @pytest.mark.asyncio
    async def test_nothing_is_recorded_while_the_user_is_still_asked(self, migration, tmp_path):
        self._conflicting_rom(migration, tmp_path)

        result = await migration.service.migrate_retrodeck_files(None)

        assert result["reason"] == "needs_confirmation"
        assert migration.save_directories.calls == 0

    @pytest.mark.asyncio
    async def test_a_failed_recording_leaves_the_finished_migration_standing(self, migration, tmp_path, caplog):
        self._conflicting_rom(migration, tmp_path)
        failing = RecordingSaveDirectories(error=RuntimeError("database gone"))
        migration.service._save_directories = failing.provide

        result = await migration.service.migrate_retrodeck_files("skip")

        assert result["success"] is True
        assert failing.calls == 1
        assert any("save directories after the home migration failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_migrate_source_missing(self, migration, tmp_path):
        """Source file gone — skip silently."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=os.path.join(old_home, "roms", "n64", "gone.z64"), system="n64")

        result = await migration.service.migrate_retrodeck_files()
        assert result["roms_moved"] == 0
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_migrate_creates_subdirs(self, migration, tmp_path):
        """Target subdirectories are created as needed."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_bios = os.path.join(old_home, "bios", "dc", "dc_boot.bin")

        os.makedirs(os.path.dirname(old_bios))
        with open(old_bios, "w") as f:
            f.write("bios")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_bios(migration.uow, platform_slug="dc", file_name="dc_boot.bin", file_path=old_bios, firmware_id=7)

        result = await migration.service.migrate_retrodeck_files()
        assert result["bios_moved"] == 1
        new_bios = os.path.join(new_home, "bios", "dc", "dc_boot.bin")
        assert os.path.exists(new_bios)

    @pytest.mark.asyncio
    async def test_clears_previous_on_success(self, migration, tmp_path):
        """After successful migration, retrodeck_home_path_previous is cleared."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        # No files to move — success with 0 moved

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True
        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path_previous") is None


class TestMigrateSaveFiles:
    """Tests for save file migration."""

    @pytest.mark.asyncio
    async def test_migrate_saves(self, migration, tmp_path):
        """Save files are moved from old to new saves directory."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_save = os.path.join(old_home, "saves", "gba", "game.srm")
        new_save = os.path.join(new_home, "saves", "gba", "game.srm")

        os.makedirs(os.path.dirname(old_save))
        with open(old_save, "w") as f:
            f.write("save data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(saves=os.path.join(new_home, "saves"))
        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["saves_moved"] == 1
        assert os.path.exists(new_save)
        assert not os.path.exists(old_save)
        with open(new_save) as f:
            assert f.read() == "save data"

    @pytest.mark.asyncio
    async def test_save_conflict_needs_confirmation(self, migration, tmp_path):
        """Save files at both locations trigger conflict confirmation."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_save = os.path.join(old_home, "saves", "gba", "game.srm")
        new_save = os.path.join(new_home, "saves", "gba", "game.srm")

        os.makedirs(os.path.dirname(old_save))
        os.makedirs(os.path.dirname(new_save))
        with open(old_save, "w") as f:
            f.write("old save")
        with open(new_save, "w") as f:
            f.write("new save")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(saves=os.path.join(new_home, "saves"))
        result = await migration.service.migrate_retrodeck_files()

        assert result["needs_confirmation"] is True
        assert result["conflict_count"] == 1
        assert "gba/game.srm" in result["conflicts"]

    @pytest.mark.asyncio
    async def test_save_conflict_overwrite(self, migration, tmp_path):
        """Overwrite strategy replaces destination save with source."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_save = os.path.join(old_home, "saves", "gba", "game.srm")
        new_save = os.path.join(new_home, "saves", "gba", "game.srm")

        os.makedirs(os.path.dirname(old_save))
        os.makedirs(os.path.dirname(new_save))
        with open(old_save, "w") as f:
            f.write("old save")
        with open(new_save, "w") as f:
            f.write("new save")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(saves=os.path.join(new_home, "saves"))
        result = await migration.service.migrate_retrodeck_files("overwrite")

        assert result["success"] is True
        assert result["saves_moved"] == 1
        with open(new_save) as f:
            assert f.read() == "old save"

    @pytest.mark.asyncio
    async def test_save_conflict_skip(self, migration, tmp_path):
        """Skip strategy keeps destination save file."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_save = os.path.join(old_home, "saves", "gba", "game.srm")
        new_save = os.path.join(new_home, "saves", "gba", "game.srm")

        os.makedirs(os.path.dirname(old_save))
        os.makedirs(os.path.dirname(new_save))
        with open(old_save, "w") as f:
            f.write("old save")
        with open(new_save, "w") as f:
            f.write("new save")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(saves=os.path.join(new_home, "saves"))
        result = await migration.service.migrate_retrodeck_files("skip")

        assert result["success"] is True
        assert result["saves_moved"] == 1
        with open(new_save) as f:
            assert f.read() == "new save"

    @pytest.mark.asyncio
    async def test_hidden_dirs_skipped(self, migration, tmp_path):
        """Hidden directories like .romm-backup are not migrated."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_save = os.path.join(old_home, "saves", "gba", "game.srm")
        old_backup = os.path.join(old_home, "saves", "gba", ".romm-backup", "game_old.srm")

        os.makedirs(os.path.dirname(old_save))
        os.makedirs(os.path.dirname(old_backup))
        with open(old_save, "w") as f:
            f.write("save data")
        with open(old_backup, "w") as f:
            f.write("backup data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(saves=os.path.join(new_home, "saves"))
        result = await migration.service.migrate_retrodeck_files()

        assert result["saves_moved"] == 1  # only the real save, not the backup

    @pytest.mark.asyncio
    async def test_status_includes_saves_count(self, migration, tmp_path):
        """get_migration_status includes saves_count."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_save = os.path.join(old_home, "saves", "gba", "game.srm")

        os.makedirs(os.path.dirname(old_save))
        with open(old_save, "w") as f:
            f.write("save data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)

        migration.service._retrodeck_paths = FakeRetroDeckPaths(saves=os.path.join(new_home, "saves"))
        status = await migration.service.get_migration_status()

        assert status["pending"] is True
        assert status["saves_count"] == 1

    @pytest.mark.asyncio
    async def test_status_counts_tracked_bios_from_sqlite(self, migration, tmp_path):
        """get_migration_status counts tracked BIOS from the SQLite ``BiosFile`` snapshot."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_bios = os.path.join(old_home, "bios", "scph5501.bin")

        os.makedirs(os.path.dirname(old_bios))
        with open(old_bios, "w") as f:
            f.write("bios data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_bios(migration.uow, platform_slug="psx", file_name="scph5501.bin", file_path=old_bios, firmware_id=42)

        status = await migration.service.get_migration_status()

        assert status["pending"] is True
        assert status["bios_count"] == 1


class TestMigrationRelaunchOptions:
    """Re-resolve step: after a RetroDECK-home migration relocates ROM files and
    updates ``rom_installs`` to the new paths, the service emits
    ``migration_relaunch_options`` so the frontend rewrites each affected Steam
    shortcut's baked ``launch_options`` (ADR-0005). Only ROMs that are BOTH
    installed (have a ``rom_installs`` row) AND bound (``shortcut_app_id`` set)
    are eligible — uninstalled or unbound ROMs are skipped.
    """

    @staticmethod
    def _relaunch_emit(migration):
        """Return the single ``migration_relaunch_options`` payload, or ``None``."""
        for event, payload in migration.service._emit.calls:
            if event == "migration_relaunch_options":
                return payload
        return None

    @staticmethod
    def _seed_bound_uninstalled(uow, rom_id, *, app_id, system="n64"):
        """Seed a bound Rom row with NO ``rom_installs`` row (downloaded=false)."""
        from domain.rom import Rom

        with uow:
            uow.roms.save(
                Rom(
                    rom_id=rom_id,
                    platform_slug=system,
                    name=f"Game {rom_id}",
                    fs_name=f"game{rom_id}",
                    shortcut_app_id=app_id,
                    last_synced_at="2025-01-01T00:00:00",
                )
            )

    @pytest.mark.asyncio
    async def test_relocated_installed_bound_rom_emits_new_launch_options(self, migration, tmp_path):
        """Happy path: a relocated installed+bound ROM emits its app_id + NEW-path command."""

        from domain.shortcut_data import build_launch_options, resolve_emulator_invocation

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        new_rom = os.path.join(new_home, "roms", "n64", "zelda.z64")

        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64", app_id=4242)

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True

        payload = self._relaunch_emit(migration)
        assert payload is not None
        expected_cmd = build_launch_options(resolve_emulator_invocation({"id": 1}), new_rom)
        assert payload["items"] == [{"app_id": 4242, "launch_options": expected_cmd}]
        # The command must point at the NEW path, never the stale old one.
        assert new_rom in payload["items"][0]["launch_options"]
        assert old_rom not in payload["items"][0]["launch_options"]

    @pytest.mark.asyncio
    async def test_relocated_rom_with_override_rebakes_e_form(self, migration, tmp_path):
        """A relocated ROM with a resolvable ``emulator_override`` re-bakes the ``-e`` form."""

        migration.core_info.available_cores = [
            {"core_so": "pcsx_rearmed_libretro", "label": "PCSX ReARMed", "is_default": True},
        ]

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "psx", "game.chd")
        new_rom = os.path.join(new_home, "roms", "psx", "game.chd")
        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="psx", platform_slug="psx", app_id=4242)
        with migration.uow as uow:
            uow.roms.set_emulator_override(1, "PCSX ReARMed")

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True

        payload = self._relaunch_emit(migration)
        assert payload is not None
        assert payload["items"] == [
            {
                "app_id": 4242,
                "launch_options": (
                    "flatpak run net.retrodeck.retrodeck "
                    '-e "%EMULATOR_RETROARCH% -L /var/config/retroarch/cores/pcsx_rearmed_libretro.so %ROM%" '
                    f'"{new_rom}"'
                ),
            }
        ]

    @pytest.mark.asyncio
    async def test_relocated_rom_with_stale_override_rebakes_plain_and_warns(self, migration, tmp_path, caplog):
        """A stale override LABEL re-bakes the PLAIN launch + WARNs (B4) — never ``None.so``."""
        import logging

        # available_cores does not carry the pinned label → resolution returns None.
        migration.core_info.available_cores = [
            {"core_so": "pcsx_rearmed_libretro", "label": "PCSX ReARMed", "is_default": True},
        ]

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "psx", "game.chd")
        new_rom = os.path.join(new_home, "roms", "psx", "game.chd")
        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="psx", platform_slug="psx", app_id=4242)
        with migration.uow as uow:
            uow.roms.set_emulator_override(1, "Removed Core")

        with caplog.at_level(logging.WARNING):
            result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True

        payload = self._relaunch_emit(migration)
        assert payload is not None
        # Stale → PLAIN launch at the NEW path, never -e None.so.
        assert payload["items"] == [
            {"app_id": 4242, "launch_options": f'flatpak run net.retrodeck.retrodeck "{new_rom}"'}
        ]
        assert "-e" not in payload["items"][0]["launch_options"]
        assert "Removed Core" in caplog.text
        assert "no longer resolves" in caplog.text

    @pytest.mark.asyncio
    async def test_installed_unbound_rom_excluded(self, migration, tmp_path):
        """Edge: installed but UNBOUND (shortcut_app_id None) is excluded from items."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")

        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64", app_id=None)

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True

        # Event still fires (matches sync_stale always-emit convention) but the
        # unbound install is not in the items.
        payload = self._relaunch_emit(migration)
        assert payload is not None
        assert payload["items"] == []

    @pytest.mark.asyncio
    async def test_bound_uninstalled_rom_excluded(self, migration, tmp_path):
        """Edge: bound but NOT installed (no rom_installs row) is excluded."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        # Bound Rom row, but no install — nothing on disk, no rom_installs row.
        self._seed_bound_uninstalled(migration.uow, 7, app_id=9999)

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True

        payload = self._relaunch_emit(migration)
        assert payload is not None
        assert payload["items"] == []

    @pytest.mark.asyncio
    async def test_mixed_batch_includes_only_installed_and_bound(self, migration, tmp_path):
        """Edge: mixed batch — only the installed+bound ROM appears in items."""

        from domain.shortcut_data import build_launch_options, resolve_emulator_invocation

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        bound_rom = os.path.join(old_home, "roms", "n64", "bound.z64")
        new_bound_rom = os.path.join(new_home, "roms", "n64", "bound.z64")
        unbound_rom = os.path.join(old_home, "roms", "n64", "unbound.z64")

        os.makedirs(os.path.dirname(bound_rom))
        with open(bound_rom, "w") as f:
            f.write("bound data")
        with open(unbound_rom, "w") as f:
            f.write("unbound data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        # 1) installed + bound  → included
        _seed_install(migration.uow, 1, file_path=bound_rom, system="n64", app_id=1111)
        # 2) installed + unbound → excluded
        _seed_install(migration.uow, 2, file_path=unbound_rom, system="n64", app_id=None)
        # 3) bound + uninstalled → excluded
        self._seed_bound_uninstalled(migration.uow, 3, app_id=3333)

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True

        payload = self._relaunch_emit(migration)
        assert payload is not None
        expected_cmd = build_launch_options(resolve_emulator_invocation({"id": 1}), new_bound_rom)
        assert payload["items"] == [{"app_id": 1111, "launch_options": expected_cmd}]

    @pytest.mark.asyncio
    async def test_zero_eligible_roms_emits_empty_items(self, migration, tmp_path):
        """Edge: zero eligible ROMs — still emits (sync_stale convention) with empty items."""

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        # No installs, no bound ROMs.

        result = await migration.service.migrate_retrodeck_files()
        assert result["success"] is True

        payload = self._relaunch_emit(migration)
        assert payload is not None
        assert payload["items"] == []

    @pytest.mark.asyncio
    async def test_no_relaunch_emit_on_needs_confirmation(self, migration, tmp_path):
        """The needs-confirmation early return must NOT emit relaunch options.

        Nothing was relocated and no paths were persisted, so re-resolving and
        rewriting shortcuts would point them at files that did not move.
        """

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        new_rom = os.path.join(new_home, "roms", "n64", "zelda.z64")

        os.makedirs(os.path.dirname(old_rom))
        os.makedirs(os.path.dirname(new_rom))
        with open(old_rom, "w") as f:
            f.write("old data")
        with open(new_rom, "w") as f:
            f.write("new data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64", app_id=4242)

        result = await migration.service.migrate_retrodeck_files()
        assert result["needs_confirmation"] is True

        assert self._relaunch_emit(migration) is None

    @pytest.mark.asyncio
    async def test_relaunch_options_built_from_persisted_new_paths(self, migration, tmp_path):
        """The event fires only after the relocated path is persisted to rom_installs.

        Asserting the emitted command equals the command for the persisted
        ``rom_installs.file_path`` ties the emit to post-commit state — a
        pre-commit emit would carry the stale old path.
        """

        from domain.shortcut_data import build_launch_options, resolve_emulator_invocation

        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")

        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom data")

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64", app_id=4242)

        await migration.service.migrate_retrodeck_files()

        # The persisted install now points at the new path.
        with migration.uow as uow:
            persisted_path = uow.rom_installs.get(1).file_path
        payload = self._relaunch_emit(migration)
        assert payload is not None
        expected_cmd = build_launch_options(resolve_emulator_invocation({"id": 1}), persisted_path)
        assert payload["items"][0]["launch_options"] == expected_cmd


class TestMigrationFailureInjection:
    """Adapter-level failure injection tests using FakeMigrationFileStore.

    These tests exercise paths the tmp_path-based integration tests cannot
    reach: simulated ``OSError`` during ``move`` / ``remove``
    must be caught by the service, appended to the ``errors`` list, and
    must not abort the rest of the migration loop. The previous path
    marker is also retained on partial failure so the user can retry.
    """

    def _make_service(self, fake_files, *, uow=None, **overrides):
        uow = uow if uow is not None else FakeUnitOfWork()
        defaults: dict[str, Any] = {
            "settings": {},
            "loop": running_loop(),
            "logger": logging.getLogger("test"),
            "settings_persister": FakeSettingsPersister(),
            "emit": RecordingEmitter(),
            "firmware_resolver": FakeFirmwareResolver(),
            "retrodeck_paths": FakeRetroDeckPaths(),
            "relaunch_options": FakeRelaunchOptionsResolver(),
            "save_directories": RecordingSaveDirectories().provide,
            "uow_factory": FakeUnitOfWorkFactory(uow=uow),
        }
        defaults.update(overrides)
        return MigrationService(
            config=MigrationServiceConfig(
                migration_file_store=fake_files, **defaults, conflict_rules=_make_conflict_rules()
            ),
        )

    def test_move_failure_records_error_and_continues(self):
        """Mid-batch ``move`` failure is captured in ``errors``; other items still move."""
        fake = FakeMigrationFileStore()
        old_home = "/old"
        new_home = "/new"
        bad_rom = "/old/roms/n64/bad.z64"
        good_rom = "/old/roms/n64/good.z64"
        fake.files[bad_rom] = b"bad"
        fake.files[good_rom] = b"good"
        fake.move_failures.add(bad_rom)

        uow = FakeUnitOfWork()
        _seed_install(uow, 1, file_path=bad_rom, system="n64")
        _seed_install(uow, 2, file_path=good_rom, system="n64")
        with uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)

        service = self._make_service(fake, uow=uow)

        result = service._migrate_retrodeck_files_io([old_home], new_home, None)

        assert result["success"] is False
        assert len(result["errors"]) == 1
        assert "bad.z64" in result["errors"][0]
        # Good ROM was moved successfully despite the bad one failing.
        assert result["roms_moved"] == 1
        # Marker is retained so the user can retry.
        with uow:
            assert uow.kv_config.get("retrodeck_home_path_previous") == old_home


class TestRefreshState:
    """Tests for ``MigrationService.refresh_state``.

    These tests exercise the orchestration contract: ``refresh_state``
    drives ``detect_retrodeck_path_change`` and then reports the home
    migration's status. The detect/status methods are patched directly
    because the test is about *how* refresh_state wires them together, not
    what they observe.
    """

    @pytest.mark.asyncio
    async def test_detects_and_returns_the_home_migration_status(self, migration):
        mig = migration.service
        mig.detect_retrodeck_path_change = MagicMock()

        retrodeck_status = {"pending": True, "old_path": "/a", "new_path": "/b"}
        mig.get_migration_status = AsyncMock(return_value=retrodeck_status)

        result = await mig.refresh_state()

        mig.detect_retrodeck_path_change.assert_called_once_with()
        assert result == {"retrodeck": retrodeck_status}

    @pytest.mark.asyncio
    async def test_short_circuits_when_first_detect_raises(self, migration):
        mig = migration.service
        mig.detect_retrodeck_path_change = MagicMock(side_effect=RuntimeError("boom"))
        mig.get_migration_status = AsyncMock()

        with pytest.raises(RuntimeError, match="boom"):
            await mig.refresh_state()

        mig.get_migration_status.assert_not_called()


class TestBackgroundTaskTracking:
    """Coverage for the background-task tracking + ``shutdown()`` lifecycle.

    The path-change detection schedules a ``retrodeck_path_changed`` emit
    via ``loop.create_task``. Without a strong ref in ``_background_tasks``
    such a task can be collected before it runs, and without a cancellation
    hook in ``shutdown()`` it is still pending when the backend shuts down.
    These tests pin the contract.
    """

    @pytest.mark.asyncio
    async def test_spawned_task_added_to_background_set(self, migration, tmp_path):
        """``detect_retrodeck_path_change`` adds its emit task to the set."""

        old_home = str(tmp_path / "old_retrodeck")
        new_home = str(tmp_path / "new_retrodeck")
        os.makedirs(new_home, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", old_home)
        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=new_home)

        assert migration.service._background_tasks == set()

        migration.service.detect_retrodeck_path_change()

        # The spawned task must be tracked before any await yields control.
        assert len(migration.service._background_tasks) == 1
        (task,) = migration.service._background_tasks
        assert isinstance(task, asyncio.Task)

        # Drain so no pending-task warning fires at loop teardown.
        await asyncio.gather(*migration.service._background_tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_done_callback_removes_task_on_natural_completion(self, migration, tmp_path):
        """When the spawned coro completes naturally, the done-callback prunes the set."""

        old_home = str(tmp_path / "old_retrodeck")
        new_home = str(tmp_path / "new_retrodeck")
        os.makedirs(new_home, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", old_home)
        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=new_home)

        migration.service.detect_retrodeck_path_change()
        assert len(migration.service._background_tasks) == 1

        # Yield until the spawned emit coroutine finishes; the done-callback
        # then discards the task from the set.
        (task,) = migration.service._background_tasks
        await task

        assert migration.service._background_tasks == set()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_pending_tasks_and_empties_set(self, migration):
        """``shutdown()`` cancels in-flight tasks and the set is empty after."""
        loop = asyncio.get_running_loop()
        migration.service._loop = loop

        # Spawn a task that blocks forever via an unset Event.
        blocker = asyncio.Event()

        async def _block_forever() -> None:
            await blocker.wait()

        migration.service._spawn_background_task(_block_forever())
        assert len(migration.service._background_tasks) == 1
        (task,) = migration.service._background_tasks

        await migration.service.shutdown()

        assert task.cancelled()
        assert migration.service._background_tasks == set()

    @pytest.mark.asyncio
    async def test_shutdown_with_empty_set_is_noop(self, migration):
        """``shutdown()`` on an untouched service returns immediately."""
        assert migration.service._background_tasks == set()

        # Must not raise, must not block.
        await migration.service.shutdown()

        assert migration.service._background_tasks == set()


def _read_pending(uow) -> tuple[str, list[str]]:
    """Return ``(previous, hops)`` as persisted in kv_config."""
    previous = uow.kv_config.get("retrodeck_home_path_previous") or ""
    hops_raw = uow.kv_config.get("retrodeck_home_path_hops")
    return previous, (json.loads(hops_raw) if hops_raw else [])


class TestChainedPathChangeDetection:
    """Detection when the RetroDECK home changes AGAIN before migrating (#1042).

    The pending set must accumulate every left-behind home rather than
    overwriting the ``_previous`` marker, so files under an intermediate home
    are never stranded.
    """

    async def _detect_at(self, migration, home: str) -> None:
        migration.service._retrodeck_paths = FakeRetroDeckPaths(home=home)
        migration.service.detect_retrodeck_path_change()
        # Drain the spawned ``retrodeck_path_changed`` emit coroutine.
        await asyncio.sleep(0)

    async def test_chained_change_appends_hop_and_keeps_previous(self, migration, tmp_path):
        """A→B→C before migrating: previous stays A, B lands in hops, home is C."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        for d in (a, b, c):
            os.makedirs(d, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", a)

        await self._detect_at(migration, b)
        await self._detect_at(migration, c)

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") == c
            previous, hops = _read_pending(uow)
        assert previous == a
        assert hops == [b]
        # The re-emit still points the banner at the ORIGINAL home → current.
        event, payload = migration.service._emit.calls[-1]
        assert event == "retrodeck_path_changed"
        assert (payload["old_path"], payload["new_path"]) == (a, c)
        assert "cleared" not in payload

    async def test_triple_chain_accumulates_all_homes(self, migration, tmp_path):
        """A→B→C→D: previous stays A, hops = [B, C]."""

        a, b, c, d = (str(tmp_path / x) for x in ("A", "B", "C", "D"))
        for path in (a, b, c, d):
            os.makedirs(path, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", a)
        await self._detect_at(migration, b)
        await self._detect_at(migration, c)
        await self._detect_at(migration, d)

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") == d
            previous, hops = _read_pending(uow)
        assert previous == a
        assert hops == [b, c]

    async def test_simple_revert_still_auto_clears(self, migration, tmp_path):
        """A→B then back to A (no hops) still fully clears — shipped UX preserved."""

        a, b = (str(tmp_path / x) for x in ("A", "B"))
        for path in (a, b):
            os.makedirs(path, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", a)
        await self._detect_at(migration, b)
        await self._detect_at(migration, a)

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") == a
            previous, hops = _read_pending(uow)
        assert previous == ""
        assert hops == []
        assert migration.service._emit.calls[-1][1]["cleared"] is True

    async def test_chained_revert_keeps_pending(self, migration, tmp_path):
        """A→B→C then back to A while B remains a hop → NOT cleared; pending = [B, C]."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        for path in (a, b, c):
            os.makedirs(path, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", a)
        await self._detect_at(migration, b)
        await self._detect_at(migration, c)
        await self._detect_at(migration, a)

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") == a
            previous, hops = _read_pending(uow)
        assert previous == b
        assert hops == [c]
        assert "cleared" not in migration.service._emit.calls[-1][1]

    async def test_move_back_to_hop_removes_it(self, migration, tmp_path):
        """A→B→C then back to B: B leaves the pending set, pending = [A, C]."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        for path in (a, b, c):
            os.makedirs(path, exist_ok=True)

        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path", a)
        await self._detect_at(migration, b)
        await self._detect_at(migration, c)
        await self._detect_at(migration, b)

        with migration.uow as uow:
            assert uow.kv_config.get("retrodeck_home_path") == b
            previous, hops = _read_pending(uow)
        assert previous == a
        assert hops == [c]


class TestTheMigrationsConflictRules:
    """The migration checks its prune rule, and leases the relaunch items it emits inside it."""

    @staticmethod
    def _stage_one_relocation(migration, tmp_path, *, app_id: int | None = 4242) -> None:
        old_home = str(tmp_path / "old")
        new_home = str(tmp_path / "new")
        old_rom = os.path.join(old_home, "roms", "n64", "zelda.z64")
        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom data")
        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", old_home)
            uow.kv_config.set("retrodeck_home_path", new_home)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64", app_id=app_id)

    @staticmethod
    def _relaunch_emit(migration):
        return next(
            payload for event, payload in migration.service._emit.calls if event == "migration_relaunch_options"
        )

    @pytest.mark.asyncio
    async def test_the_relaunch_items_carry_a_lease_taken_inside_the_migrations_operation(
        self, migration, tmp_path, monkeypatch
    ):
        """No cleanup can start between the migration's operation and the lease its Steam writes run under."""
        self._stage_one_relocation(migration, tmp_path)
        seen = _record_operations_at_lease(migration.prune_conflicts, monkeypatch)

        assert (await migration.service.migrate_retrodeck_files())["success"] is True

        assert seen == [["migrate_retrodeck_files"]]
        token = self._relaunch_emit(migration)["prune_lease_token"]
        assert token.startswith("migration_relaunch_options:")
        assert migration.prune_conflicts.conflicting_operations == 1
        await migration.prune_conflicts.release_lease(token)
        assert migration.prune_conflicts.conflicting_operations == 0

    @pytest.mark.asyncio
    async def test_no_relaunch_items_carry_no_lease(self, migration, tmp_path):
        self._stage_one_relocation(migration, tmp_path, app_id=None)

        assert (await migration.service.migrate_retrodeck_files())["success"] is True

        assert self._relaunch_emit(migration) == {"items": []}
        assert migration.prune_conflicts.conflicting_operations == 0

    @pytest.mark.asyncio
    async def test_relaunch_items_nobody_heard_give_their_lease_back(self, migration, tmp_path):
        self._stage_one_relocation(migration, tmp_path)
        heard: list[str] = []

        async def unheard(event: str, payload: object, /) -> bool:
            heard.append(event)
            return False

        migration.service._emit = unheard

        assert (await migration.service.migrate_retrodeck_files())["success"] is True

        assert heard == ["migration_relaunch_options"]
        assert migration.prune_conflicts.conflicting_operations == 0

    @pytest.mark.asyncio
    async def test_relaunch_items_whose_emit_raises_give_their_lease_back(self, migration, tmp_path):
        self._stage_one_relocation(migration, tmp_path)

        async def rejected(event: str, payload: object, /) -> bool:
            raise RuntimeError("transport rejected event")

        migration.service._emit = rejected

        with pytest.raises(RuntimeError, match="transport rejected event"):
            await migration.service.migrate_retrodeck_files()

        assert migration.prune_conflicts.conflicting_operations == 0

    @pytest.mark.asyncio
    async def test_a_running_cleanup_refuses_the_migration_and_moves_nothing(self, migration, tmp_path):
        self._stage_one_relocation(migration, tmp_path)
        migration.prune_conflicts.register_run("held-run")

        with _refused_by_conflict_rule("prune_active"):
            await migration.service.migrate_retrodeck_files()

        assert os.path.exists(os.path.join(str(tmp_path / "old"), "roms", "n64", "zelda.z64"))
        assert migration.service._emit.calls == []
        assert migration.prune_conflicts.conflicting_operations == 0


class TestChainedMigration:
    """Migrating a chained pending set (#1042) — files drain from every home."""

    @staticmethod
    def _relaunch_emit(migration):
        for event, payload in migration.service._emit.calls:
            if event == "migration_relaunch_options":
                return payload
        return None

    @staticmethod
    def _set_pending(uow, *, previous: str, hops: list[str], home: str) -> None:
        uow.kv_config.set("retrodeck_home_path_previous", previous)
        if hops:
            uow.kv_config.set("retrodeck_home_path_hops", json.dumps(hops))
        uow.kv_config.set("retrodeck_home_path", home)

    @pytest.mark.asyncio
    async def test_rows_and_files_at_oldest_home_migrate_to_current(self, migration, tmp_path):
        """Headline #1042 fix: rows+files at A after A→B→C reach C, both keys cleared."""

        from domain.shortcut_data import build_launch_options, resolve_emulator_invocation

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        old_rom = os.path.join(a, "roms", "n64", "zelda.z64")
        new_rom = os.path.join(c, "roms", "n64", "zelda.z64")
        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom data")

        with migration.uow as uow:
            self._set_pending(uow, previous=a, hops=[b], home=c)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64", app_id=4242)

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["roms_moved"] == 1
        assert os.path.exists(new_rom)
        assert not os.path.exists(old_rom)
        with migration.uow as uow:
            assert uow.rom_installs.get(1).file_path == new_rom
            # BOTH markers gone after a clean migration.
            previous, hops = _read_pending(uow)
        assert previous == ""
        assert hops == []
        # Relaunch options rebaked at the NEW (C) path.
        payload = self._relaunch_emit(migration)
        assert payload is not None
        expected = build_launch_options(resolve_emulator_invocation({"id": 1}), new_rom)
        assert payload["items"] == [{"app_id": 4242, "launch_options": expected}]

    @pytest.mark.asyncio
    async def test_file_already_at_current_is_bookkept(self, migration, tmp_path):
        """Row says A but the file is already at C → DB path updated, counted, no move error."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        old_rom = os.path.join(a, "roms", "n64", "zelda.z64")
        new_rom = os.path.join(c, "roms", "n64", "zelda.z64")
        os.makedirs(os.path.dirname(new_rom))
        with open(new_rom, "w") as f:
            f.write("already here")

        with migration.uow as uow:
            self._set_pending(uow, previous=a, hops=[b], home=c)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64")

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["roms_moved"] == 1
        assert result["missing_count"] == 0
        with migration.uow as uow:
            assert uow.rom_installs.get(1).file_path == new_rom

    @pytest.mark.asyncio
    async def test_file_stranded_at_hop_is_found_and_moved(self, migration, tmp_path):
        """Row says A but the file physically sits at hop B → probed, moved A-mapped path to C."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        recorded_rom = os.path.join(a, "roms", "n64", "zelda.z64")  # DB path (missing on disk)
        stranded_rom = os.path.join(b, "roms", "n64", "zelda.z64")  # actual location
        new_rom = os.path.join(c, "roms", "n64", "zelda.z64")
        os.makedirs(os.path.dirname(stranded_rom))
        with open(stranded_rom, "w") as f:
            f.write("stranded data")

        with migration.uow as uow:
            self._set_pending(uow, previous=a, hops=[b], home=c)
        _seed_install(migration.uow, 1, file_path=recorded_rom, system="n64")

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["roms_moved"] == 1
        assert result["missing_count"] == 0
        assert os.path.exists(new_rom)
        assert not os.path.exists(stranded_rom)
        with open(new_rom) as f:
            assert f.read() == "stranded data"
        with migration.uow as uow:
            assert uow.rom_installs.get(1).file_path == new_rom

    @pytest.mark.asyncio
    async def test_file_missing_everywhere_is_surfaced_marker_cleared(self, migration, tmp_path):
        """Row exists but the file is at no known home → missing surfaced, marker still clears."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))

        with migration.uow as uow:
            self._set_pending(uow, previous=a, hops=[b], home=c)
        _seed_install(migration.uow, 1, file_path=os.path.join(a, "roms", "n64", "gone.z64"), system="n64")

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["roms_moved"] == 0
        assert result["missing_count"] == 1
        assert "missing" in result["message"]
        with migration.uow as uow:
            previous, hops = _read_pending(uow)
        assert previous == ""
        assert hops == []

    @pytest.mark.asyncio
    async def test_mixed_rows_at_two_homes_drained_in_one_run(self, migration, tmp_path):
        """One row under A, another under hop B → both drain to C in a single migrate."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        rom_a = os.path.join(a, "roms", "n64", "a.z64")
        rom_b = os.path.join(b, "roms", "n64", "b.z64")
        new_a = os.path.join(c, "roms", "n64", "a.z64")
        new_b = os.path.join(c, "roms", "n64", "b.z64")
        os.makedirs(os.path.dirname(rom_a))
        os.makedirs(os.path.dirname(rom_b))
        with open(rom_a, "w") as f:
            f.write("a")
        with open(rom_b, "w") as f:
            f.write("b")

        with migration.uow as uow:
            self._set_pending(uow, previous=a, hops=[b], home=c)
        _seed_install(migration.uow, 1, file_path=rom_a, system="n64")
        _seed_install(migration.uow, 2, file_path=rom_b, system="n64")

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["roms_moved"] == 2
        assert os.path.exists(new_a)
        assert os.path.exists(new_b)
        with migration.uow as uow:
            assert uow.rom_installs.get(1).file_path == new_a
            assert uow.rom_installs.get(2).file_path == new_b

    @pytest.mark.asyncio
    async def test_same_save_under_two_homes_newest_wins(self, migration, tmp_path):
        """Same save rel-path under A and B → only the newest-mtime copy migrates."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        save_a = os.path.join(a, "saves", "gba", "game.srm")
        save_b = os.path.join(b, "saves", "gba", "game.srm")
        new_save = os.path.join(c, "saves", "gba", "game.srm")
        os.makedirs(os.path.dirname(save_a))
        os.makedirs(os.path.dirname(save_b))
        with open(save_a, "w") as f:
            f.write("stale")
        with open(save_b, "w") as f:
            f.write("fresh")
        # A is older, B is newer.
        os.utime(save_a, (1_700_000_000, 1_700_000_000))
        os.utime(save_b, (1_700_000_500, 1_700_000_500))

        with migration.uow as uow:
            self._set_pending(uow, previous=a, hops=[b], home=c)
        migration.service._retrodeck_paths = FakeRetroDeckPaths(saves=os.path.join(c, "saves"))

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["saves_moved"] == 1
        with open(new_save) as f:
            assert f.read() == "fresh"

    @pytest.mark.asyncio
    async def test_untracked_bios_probed_across_homes(self, migration, tmp_path):
        """An untracked BIOS file living under a hop home is found and migrated."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        bios_b = os.path.join(b, "bios", "scph5501.bin")
        new_bios = os.path.join(c, "bios", "scph5501.bin")
        os.makedirs(os.path.dirname(bios_b))
        with open(bios_b, "w") as f:
            f.write("bios")

        # A single declared file, so the sweep has exactly one candidate to find.
        resolver = FakeFirmwareResolver()
        resolver.declare("scph5501.bin", required_by=["mednafen_psx_libretro"])
        migration.service._firmware_resolver = resolver
        migration.service._retrodeck_paths = FakeRetroDeckPaths(bios=os.path.join(c, "bios"))

        with migration.uow as uow:
            self._set_pending(uow, previous=a, hops=[b], home=c)

        result = await migration.service.migrate_retrodeck_files()

        assert result["success"] is True
        assert result["bios_moved"] == 1
        assert os.path.exists(new_bios)

    @pytest.mark.asyncio
    async def test_status_counts_across_homes(self, migration, tmp_path):
        """get_migration_status counts a row under A and a save under B; old_path = A."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        rom_a = os.path.join(a, "roms", "n64", "a.z64")
        save_b = os.path.join(b, "saves", "gba", "game.srm")
        os.makedirs(os.path.dirname(rom_a))
        os.makedirs(os.path.dirname(save_b))
        with open(rom_a, "w") as f:
            f.write("a")
        with open(save_b, "w") as f:
            f.write("s")

        with migration.uow as uow:
            self._set_pending(uow, previous=a, hops=[b], home=c)
        _seed_install(migration.uow, 1, file_path=rom_a, system="n64")
        migration.service._retrodeck_paths = FakeRetroDeckPaths(saves=os.path.join(c, "saves"))

        status = await migration.service.get_migration_status()

        assert status["pending"] is True
        assert status["old_path"] == a
        assert status["new_path"] == c
        assert status["roms_count"] == 1
        assert status["saves_count"] == 1

    def test_dismiss_clears_both_keys(self, migration):
        """Dismiss drops the previous marker AND the hops array."""
        with migration.uow as uow:
            uow.kv_config.set("retrodeck_home_path_previous", "/a")
            uow.kv_config.set("retrodeck_home_path_hops", json.dumps(["/b"]))

        result = migration.service.dismiss_retrodeck_migration()

        assert result == {"success": True}
        with migration.uow as uow:
            previous, hops = _read_pending(uow)
        assert previous == ""
        assert hops == []

    @pytest.mark.asyncio
    async def test_rerun_converges_to_no_migration_needed(self, migration, tmp_path):
        """After a clean migrate clears the markers, a second run is a no-op."""

        a, b, c = (str(tmp_path / x) for x in ("A", "B", "C"))
        old_rom = os.path.join(a, "roms", "n64", "zelda.z64")
        os.makedirs(os.path.dirname(old_rom))
        with open(old_rom, "w") as f:
            f.write("rom")

        with migration.uow as uow:
            self._set_pending(uow, previous=a, hops=[b], home=c)
        _seed_install(migration.uow, 1, file_path=old_rom, system="n64")

        first = await migration.service.migrate_retrodeck_files()
        assert first["success"] is True

        second = await migration.service.migrate_retrodeck_files()
        assert second["success"] is False
        assert second["reason"] == "no_migration_needed"
