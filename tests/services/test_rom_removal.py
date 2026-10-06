"""Tests for RomRemovalService — ROM file deletion and ``rom_installs`` cleanup."""

import asyncio
import logging
import os
import shutil
import sqlite3
import sys
import threading

import pytest
from _factories import (
    _make_conflict_rules,
    _make_prune_conflicts,
    _record_operations_at_lease,
    _refused_by_conflict_rule,
)
from fakes.fake_download_queue_cleanup import FakeDownloadQueueCleanup
from fakes.fake_retrodeck_paths import FakeRetroDeckPaths
from fakes.fake_rom_file_store import FakeRomFileStore
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.system_time import FakeClock

sys.path.insert(0, os.path.dirname(__file__))

from models.prune import InstalledContentRemoval

from adapters.recovery_bundle import RecoveryBundleAdapter
from adapters.rom_files import RomFileAdapter
from domain.prune import BundleReadmeContext
from domain.rom import Rom
from domain.rom_install import RomInstall
from lib.errors import NotInstalled, Refused
from services.rom_removal import RomRemovalService, RomRemovalServiceConfig, UninstallIncomplete

# Synthetic roms-base path used by the fake fs throughout this module.
_ROMS_BASE = "/retrodeck/roms"


@pytest.fixture
def logger():
    return logging.getLogger("test_rom_removal")


@pytest.fixture
def queue_cleanup() -> FakeDownloadQueueCleanup:
    return FakeDownloadQueueCleanup()


@pytest.fixture
def rom_files() -> FakeRomFileStore:
    return FakeRomFileStore()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


class RecordingEmitter:
    """Records every ``(event, payload)`` a service emits."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def __call__(self, event: str, payload: object, /) -> bool:
        self.events.append((event, payload))
        return True

    def payloads(self, event: str) -> list[object]:
        return [payload for name, payload in self.events if name == event]


@pytest.fixture
def emitter() -> RecordingEmitter:
    return RecordingEmitter()


@pytest.fixture
def prune_conflicts():
    return _make_prune_conflicts()


@pytest.fixture
def service(logger, queue_cleanup, rom_files, uow, emitter, prune_conflicts):
    return RomRemovalService(
        config=RomRemovalServiceConfig(
            logger=logger,
            loop=asyncio.new_event_loop(),
            clock=FakeClock(),
            emit=emitter,
            rom_file_store=rom_files,
            retrodeck_paths=FakeRetroDeckPaths(roms=_ROMS_BASE),
            download_queue_cleanup=queue_cleanup,
            uow_factory=FakeUnitOfWorkFactory(uow),
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
        ),
    )


@pytest.fixture(autouse=True)
async def _sync_loop(service):
    """Keep service loop in sync with the running event loop."""
    service._loop = asyncio.get_running_loop()


def _make_rom(rom_id: int, *, platform_slug: str = "n64", bound: bool = True) -> Rom:
    """Build the FK-parent ``roms`` row so a child ``rom_installs`` write commits.

    Bound rows carry ``shortcut_app_id = 1000 + rom_id`` (a live Steam
    shortcut); ``bound=False`` leaves it ``None`` (no shortcut to reset).
    """
    return Rom(
        rom_id=rom_id,
        platform_slug=platform_slug,
        name=f"Game {rom_id}",
        fs_name=f"game_{rom_id}.z64",
        shortcut_app_id=(1000 + rom_id) if bound else None,
        last_synced_at="2025-01-01T00:00:00",
    )


def _make_install(rom_id: int, *, file_path: str, rom_dir: str | None = None, system: str = "n64") -> RomInstall:
    return RomInstall.mark_installed(
        rom_id=rom_id,
        file_path=file_path,
        rom_dir=rom_dir,
        platform_slug=system,
        system=system,
        installed_at="2025-01-01T00:00:00",
    )


def _installed(uow: FakeUnitOfWork, rom_id: int) -> RomInstall:
    """Read a seeded install record back, failing the test if the seed did not commit."""
    install = uow.rom_installs.get(rom_id)
    assert install is not None
    return install


def _seed_install(uow: FakeUnitOfWork, install: RomInstall, *, platform_slug: str = "n64") -> None:
    """Seed the FK-parent Rom THEN its install record, in one commit."""
    with uow:
        uow.roms.save(_make_rom(install.rom_id, platform_slug=platform_slug))
        uow.rom_installs.save(install)


class TestDeleteRomFiles:
    def test_deletes_single_file(self, service, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"\x00" * 100

        service._delete_rom_files(_make_install(1, file_path=rom_path))

        assert rom_path not in rom_files.files
        assert rom_files.remove_file_calls == [rom_path]
        # A single-file ROM has no rom_dir, so no directory tree is ever removed.
        assert rom_files.remove_tree_calls == []

    def test_deletes_rom_dir(self, service, rom_files):
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        rom_files.files[f"{rom_dir}/disc1.cue"] = b"cue"
        rom_files.files[f"{rom_dir}/disc1.bin"] = b"\x00" * 100

        service._delete_rom_files(_make_install(1, file_path=f"{rom_dir}/FF7.m3u", rom_dir=rom_dir, system="psx"))

        assert f"{rom_dir}/disc1.cue" not in rom_files.files
        assert f"{rom_dir}/disc1.bin" not in rom_files.files
        assert rom_files.remove_tree_calls == [rom_dir]

    def test_single_file_owns_no_dir_so_system_dir_not_removed(self, service, rom_files):
        """A single-file ROM (``rom_dir`` is ``None``) lives in the shared ``<roms>/<system>`` dir.

        With no ``rom_dir`` set, the directory tree is never removed — only the
        launch file is deleted. Removing the shared system dir would wipe the
        whole platform's folder.
        """
        system_dir = f"{_ROMS_BASE}/n64"
        rom_path = f"{system_dir}/game.z64"
        sibling = f"{system_dir}/other_game.z64"
        rom_files.files[rom_path] = b"\x00" * 100
        rom_files.files[sibling] = b"\x00" * 100
        rom_files.dirs.add(system_dir)

        service._delete_rom_files(_make_install(1, file_path=rom_path, rom_dir=None))

        assert rom_path not in rom_files.files
        assert sibling in rom_files.files  # the platform's other ROM survives
        assert system_dir in rom_files.dirs  # the system dir itself survives
        assert rom_files.remove_tree_calls == []

    def test_single_file_record_pointing_at_nested_directory_fails_closed(self, service, rom_files):
        nested = f"{_ROMS_BASE}/n64/shared-content"
        rom_files.dirs.add(nested)
        rom_files.files[f"{nested}/other.z64"] = b"keep"

        install = _make_install(1, file_path=nested, rom_dir=None)
        with pytest.raises(ValueError, match="Expected installed ROM file"):
            service._delete_rom_files(install)

        assert f"{nested}/other.z64" in rom_files.files
        assert rom_files.remove_tree_calls == []

    def test_filesystem_only_removal_leaves_install_and_rom_rows(self, service, uow, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(7, file_path=rom_path))

        result = service.delete_rom_files(7)

        assert result.failure is None
        assert uow.roms.get(7) is not None
        assert uow.rom_installs.get(7) is not None

    def test_a_rom_with_nothing_installed_is_a_removal_that_changed_nothing(self, service):
        assert service.delete_rom_files(7) == InstalledContentRemoval(changed=False, ambiguous=False)

    def test_a_removal_that_raised_is_ambiguous(self, service, uow, rom_files):
        """Files may already be gone when the removal stops, so its ``changed=False`` is marked ambiguous."""
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        rom_files.files[f"{rom_dir}/disc1.bin"] = b"\x00" * 100
        rom_files.remove_tree_failures.add(rom_dir)
        _seed_install(uow, _make_install(7, file_path=f"{rom_dir}/FF7.m3u", rom_dir=rom_dir, system="psx"))

        result = service.delete_rom_files(7)

        assert result == InstalledContentRemoval(
            changed=False, ambiguous=True, failure=f"simulated remove_tree failure: {rom_dir}"
        )

    def test_refuses_file_outside_roms_dir(self, service, rom_files):
        evil = "/evil/important.txt"
        rom_files.files[evil] = b"do not delete"

        install = _make_install(1, file_path=evil, rom_dir=None)
        with pytest.raises(ValueError, match="outside roms directory"):
            service._delete_rom_files(install)

        assert evil in rom_files.files
        assert rom_files.remove_file_calls == []
        assert rom_files.remove_tree_calls == []

    def test_refuses_rom_dir_outside_roms_dir(self, service, rom_files):
        evil_dir = "/evil/dir"
        rom_files.files[f"{evil_dir}/file.txt"] = b"important"

        install = _make_install(1, file_path="", rom_dir=evil_dir)
        with pytest.raises(ValueError, match="outside roms directory"):
            service._delete_rom_files(install)

        assert f"{evil_dir}/file.txt" in rom_files.files
        assert rom_files.remove_tree_calls == []

    def test_missing_file_no_crash(self, service):
        # File doesn't exist — should not raise and should not call any I/O
        service._delete_rom_files(_make_install(1, file_path=f"{_ROMS_BASE}/n64/gone.z64"))

    def test_empty_paths_no_crash(self, service):
        # No file_path, no rom_dir
        service._delete_rom_files(_make_install(1, file_path="", rom_dir=None))

    def test_sealed_file_replacement_is_retained_at_mutation_time(self, tmp_path, logger):
        roms = tmp_path / "roms"
        rom_path = roms / "n64" / "game.z64"
        rom_path.parent.mkdir(parents=True)
        rom_path.write_bytes(b"sealed")
        recovery = RecoveryBundleAdapter(
            user_home=str(tmp_path),
            package_name="romm-tender",
            version="test",
        )
        bundle = recovery.seal_bundle(
            "Game_2026-07-24_romfile",
            {"roms": [{"rom_id": 1}]},
            [{"source_path": str(rom_path), "safe_root": str(roms), "kind": "installed_rom", "rom_id": 1}],
            BundleReadmeContext(
                bundle_id="Game_2026-07-24_romfile",
                created_at="2026-07-24T12:00:00+00:00",
                games=[],
                playtime_lines=[],
            ),
            "playtime",
        )
        claims = recovery.source_claims(bundle)["claims"]
        rom_path.unlink()
        rom_path.write_bytes(b"replacement")
        uow = FakeUnitOfWork()
        _seed_install(uow, _make_install(1, file_path=str(rom_path)))
        real_service = RomRemovalService(
            config=RomRemovalServiceConfig(
                logger=logger,
                loop=asyncio.new_event_loop(),
                clock=FakeClock(),
                emit=RecordingEmitter(),
                rom_file_store=RomFileAdapter(),
                retrodeck_paths=FakeRetroDeckPaths(roms=str(roms)),
                download_queue_cleanup=None,
                uow_factory=FakeUnitOfWorkFactory(uow),
                conflict_rules=_make_conflict_rules(),
            )
        )

        result = real_service.delete_rom_files(1, claims)

        assert result.failure is not None
        assert "identity changed" in result.failure
        assert rom_path.read_bytes() == b"replacement"

    def test_preopened_rom_writer_prevents_installed_file_deletion(self, tmp_path, logger):
        roms = tmp_path / "roms"
        rom_path = roms / "n64" / "game.z64"
        rom_path.parent.mkdir(parents=True)
        rom_path.write_bytes(b"installed")
        uow = FakeUnitOfWork()
        _seed_install(uow, _make_install(1, file_path=str(rom_path)))
        real_service = RomRemovalService(
            config=RomRemovalServiceConfig(
                logger=logger,
                loop=asyncio.new_event_loop(),
                clock=FakeClock(),
                emit=RecordingEmitter(),
                rom_file_store=RomFileAdapter(),
                retrodeck_paths=FakeRetroDeckPaths(roms=str(roms)),
                download_queue_cleanup=None,
                uow_factory=FakeUnitOfWorkFactory(uow),
                conflict_rules=_make_conflict_rules(),
            )
        )
        writer = os.open(rom_path, os.O_WRONLY)
        try:
            result = real_service.delete_rom_files(1)
        finally:
            os.close(writer)

        assert result.failure is not None
        assert "active writer" in result.failure
        assert rom_path.read_bytes() == b"installed"

    def test_selected_directory_child_change_is_retained_at_mutation_time(self, tmp_path, logger):
        roms = tmp_path / "roms"
        rom_dir = roms / "psx" / "Game"
        rom_dir.mkdir(parents=True)
        child = rom_dir / "disc.bin"
        child.write_bytes(b"sealed")
        recovery = RecoveryBundleAdapter(user_home=str(tmp_path), package_name="romm-tender", version="test")
        bundle = recovery.seal_bundle(
            "Game_2026-07-24_romdir",
            {"roms": [{"rom_id": 1}]},
            [{"source_path": str(rom_dir), "safe_root": str(roms), "kind": "installed_rom", "rom_id": 1}],
            BundleReadmeContext(
                bundle_id="Game_2026-07-24_romdir",
                created_at="2026-07-24T12:00:00+00:00",
                games=[],
                playtime_lines=[],
            ),
            "playtime",
        )
        claims = recovery.source_claims(bundle)["claims"]
        child.write_bytes(b"replacement")
        uow = FakeUnitOfWork()
        _seed_install(uow, _make_install(1, file_path=str(child), rom_dir=str(rom_dir), system="psx"))
        real_service = RomRemovalService(
            config=RomRemovalServiceConfig(
                logger=logger,
                loop=asyncio.new_event_loop(),
                clock=FakeClock(),
                emit=RecordingEmitter(),
                rom_file_store=RomFileAdapter(),
                retrodeck_paths=FakeRetroDeckPaths(roms=str(roms)),
                download_queue_cleanup=None,
                uow_factory=FakeUnitOfWorkFactory(uow),
                conflict_rules=_make_conflict_rules(),
            )
        )

        result = real_service.delete_rom_files(1, claims)

        assert result.failure is not None
        assert "subtree changed" in result.failure
        assert child.read_bytes() == b"replacement"

    @pytest.mark.parametrize("claims", [None, {}], ids=["no-bundle", "bundle-without-this-source"])
    def test_directory_replacement_after_a_final_claim_is_retained(self, tmp_path, logger, monkeypatch, claims):
        """Either discipline refuses a source swapped out between its final claim and the mutation."""
        roms = tmp_path / "roms"
        rom_dir = roms / "psx" / "Game"
        rom_dir.mkdir(parents=True)
        (rom_dir / "disc.bin").write_bytes(b"original")
        replacement = roms / "psx" / "Replacement"
        replacement.mkdir()
        (replacement / "disc.bin").write_bytes(b"replacement")
        store = RomFileAdapter()
        original_claim = store.claim_source

        def claim_then_replace(path: str, safe_root: str, *, digest: bool = True):
            claim = original_claim(path, safe_root, digest=digest)
            shutil.rmtree(path)
            replacement.rename(path)
            return claim

        monkeypatch.setattr(store, "claim_source", claim_then_replace)
        uow = FakeUnitOfWork()
        _seed_install(
            uow,
            _make_install(1, file_path=str(rom_dir / "disc.bin"), rom_dir=str(rom_dir), system="psx"),
        )
        real_service = RomRemovalService(
            config=RomRemovalServiceConfig(
                logger=logger,
                loop=asyncio.new_event_loop(),
                clock=FakeClock(),
                emit=RecordingEmitter(),
                rom_file_store=store,
                retrodeck_paths=FakeRetroDeckPaths(roms=str(roms)),
                download_queue_cleanup=None,
                uow_factory=FakeUnitOfWorkFactory(uow),
                conflict_rules=_make_conflict_rules(),
            )
        )

        result = real_service.delete_rom_files(1, claims)

        assert result.failure is not None
        assert "identity changed" in result.failure
        assert (rom_dir / "disc.bin").read_bytes() == b"replacement"

    @pytest.mark.parametrize("multi_file", [False, True], ids=["single-file", "rom-dir"])
    def test_uninstalls_when_the_roms_root_is_reached_through_a_symlink(self, tmp_path, logger, multi_file):
        """#1838: the roms root and the install record spell one directory two ways.

        Image-based distributions (Bazzite, Silverblue) ship ``/home`` as a link
        to ``/var/home``. The root is handed in the spelling ``retrodeck.json``
        used — what an install row recorded before the roots were resolved is
        matched against — so the uninstall must still go through. Run against
        the real ``RomFileAdapter``, since a fake store cannot have this problem.
        """
        base = tmp_path.resolve()
        system = base / "var" / "home" / "player" / "retrodeck" / "roms" / "ps2"
        system.mkdir(parents=True)
        (base / "home").symlink_to(base / "var" / "home", target_is_directory=True)
        linked_roms = str(base / "home" / "player" / "retrodeck" / "roms")
        assert linked_roms != os.path.realpath(linked_roms)
        rom_dir = system / "Game" if multi_file else None
        if rom_dir is not None:
            rom_dir.mkdir()
        rom_path = (rom_dir or system) / "388.chd"
        rom_path.write_bytes(b"disc")
        uow = FakeUnitOfWork()
        _seed_install(
            uow,
            _make_install(1, file_path=str(rom_path), rom_dir=str(rom_dir) if rom_dir else None, system="ps2"),
            platform_slug="ps2",
        )
        real_service = RomRemovalService(
            config=RomRemovalServiceConfig(
                logger=logger,
                loop=asyncio.new_event_loop(),
                clock=FakeClock(),
                emit=RecordingEmitter(),
                rom_file_store=RomFileAdapter(),
                retrodeck_paths=FakeRetroDeckPaths(roms=linked_roms),
                download_queue_cleanup=None,
                uow_factory=FakeUnitOfWorkFactory(uow),
                conflict_rules=_make_conflict_rules(),
            )
        )

        result = real_service.delete_rom_files(1)

        assert result.failure is None, result.failure
        assert result.changed is True
        assert not rom_path.exists()
        # The shared per-system directory is never the thing removed.
        assert system.is_dir()


class TestRemoveRom:
    @pytest.mark.asyncio
    async def test_removes_file_and_clears_install_record(self, service, uow, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/zelda.z64"
        rom_files.files[rom_path] = b"\x00" * 100
        _seed_install(uow, _make_install(42, file_path=rom_path))

        result = await service.remove_rom(42)

        assert result["success"] is True
        assert rom_path not in rom_files.files
        assert uow.rom_installs.get(42) is None
        assert uow.committed is True

    @pytest.mark.asyncio
    async def test_records_empty_applied_launch_options_for_bound_rom(self, service, uow, rom_files):
        # The frontend resets the kept shortcut's launch command to "" on uninstall
        # (#1146); the backend records "" so the next sync skips it (#1383).
        rom_path = f"{_ROMS_BASE}/n64/zelda.z64"
        rom_files.files[rom_path] = b"\x00" * 100
        _seed_install(uow, _make_install(42, file_path=rom_path))
        with uow:
            uow.roms.set_applied_launch_options(42, "flatpak run net.retrodeck.retrodeck /zelda.z64")

        await service.remove_rom(42)

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.applied_launch_options == ""

    @pytest.mark.asyncio
    async def test_does_not_record_applied_for_unbound_rom(self, service, uow, rom_files):
        # An unbound ROM has no shortcut to reset — the recording is guarded on the
        # binding, so its applied state stays untouched (unknown).
        rom_path = f"{_ROMS_BASE}/n64/unbound.z64"
        rom_files.files[rom_path] = b"\x00" * 100
        with uow:
            uow.roms.save(_make_rom(50, bound=False))
            uow.rom_installs.save(_make_install(50, file_path=rom_path))

        await service.remove_rom(50)

        with uow:
            rom = uow.roms.get(50)
        assert rom is not None
        assert rom.applied_launch_options is None

    @pytest.mark.asyncio
    async def test_refuses_if_not_installed(self, service):
        coro = service.remove_rom(999)
        with pytest.raises(NotInstalled) as refused:
            await coro
        assert refused.value.message == "ROM not installed"

    @pytest.mark.asyncio
    async def test_accepts_string_rom_id(self, service, uow, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"\x00" * 100
        _seed_install(uow, _make_install(7, file_path=rom_path))

        result = await service.remove_rom("7")

        assert result["success"] is True
        assert uow.rom_installs.get(7) is None

    @pytest.mark.asyncio
    async def test_file_already_gone_still_deletes_record(self, service, uow):
        """Edge: the file is already gone on disk → the install record is still dropped."""
        _seed_install(
            uow,
            _make_install(42, file_path=f"{_ROMS_BASE}/n64/gone.z64"),
        )

        result = await service.remove_rom(42)

        assert result["success"] is True
        assert uow.rom_installs.get(42) is None

    @pytest.mark.asyncio
    async def test_retains_playtime_saves_and_roms_row(self, service, uow, rom_files):
        """RETENTION (ADR-0007 / D1): uninstall drops only files + the install record.

        Playtime, the save-sync state, and the ``roms`` identity row all survive.
        """
        from domain.playtime import Playtime
        from domain.rom_save_sync_state import RomSaveSyncState

        rom_path = f"{_ROMS_BASE}/n64/zelda.z64"
        rom_files.files[rom_path] = b"\x00" * 100

        playtime = Playtime(total_seconds=3600, session_count=2)
        save_state = RomSaveSyncState(active_slot="default", slot_confirmed=True)
        with uow:
            uow.roms.save(_make_rom(42))
            uow.rom_installs.save(_make_install(42, file_path=rom_path))
            uow.playtime.save(42, playtime)
            uow.rom_save_sync_states.save(42, save_state)

        result = await service.remove_rom(42)

        assert result["success"] is True
        # Only the install record is gone.
        assert uow.rom_installs.get(42) is None
        # Identity, playtime, and save-sync state all survive the uninstall.
        assert uow.roms.get(42) is not None
        surviving_playtime = uow.playtime.get(42)
        assert surviving_playtime is not None
        assert surviving_playtime.total_seconds == 3600
        surviving_save = uow.rom_save_sync_states.get(42)
        assert surviving_save is not None
        assert surviving_save.active_slot == "default"
        assert uow.committed is True

    @pytest.mark.asyncio
    async def test_removes_rom_dir(self, service, uow, rom_files):
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        rom_files.files[f"{rom_dir}/FF7.m3u"] = b"disc1.cue"
        rom_files.files[f"{rom_dir}/disc1.cue"] = b"cue"
        rom_files.files[f"{rom_dir}/disc1.bin"] = b"\x00" * 100
        # Mark the parent system dir as existing so we can assert it's preserved.
        rom_files.dirs.add(f"{_ROMS_BASE}/psx")
        _seed_install(
            uow,
            _make_install(42, file_path=f"{rom_dir}/FF7.m3u", rom_dir=rom_dir, system="psx"),
            platform_slug="psx",
        )

        result = await service.remove_rom(42)

        assert result["success"] is True
        # rom_dir gone
        assert all(not p.startswith(rom_dir + "/") for p in rom_files.files)
        # Parent system dir still tracked
        assert f"{_ROMS_BASE}/psx" in rom_files.dirs

    @pytest.mark.asyncio
    async def test_path_traversal_rejected_preserves_install_record(self, service, uow, rom_files):
        evil = "/etc/passwd"
        rom_files.files[evil] = b"root:x:0:0"
        _seed_install(uow, _make_install(99, file_path=evil, rom_dir=None))

        coro = service.remove_rom(99)
        with pytest.raises(Refused) as refused:
            await coro

        assert (refused.value.reason, refused.value.message) == ("uninstall_failed", "Failed to delete ROM files")
        assert evil in rom_files.files  # not deleted (outside roms dir)
        assert uow.rom_installs.get(99) is not None

    @pytest.mark.asyncio
    async def test_removes_nested_single_file_entry(self, service, uow, rom_files):
        """Nested-single-file installs (#226): the resolved filename is in file_path; rom_dir is None (no folder)."""
        system_dir = f"{_ROMS_BASE}/dc"
        rom_path = f"{system_dir}/Resident Evil.chd"
        rom_files.files[rom_path] = b"\x00" * 100
        rom_files.dirs.add(system_dir)
        _seed_install(
            uow,
            _make_install(42, file_path=rom_path, rom_dir=None, system="dc"),
            platform_slug="dc",
        )

        result = await service.remove_rom(42)

        assert result["success"] is True
        assert rom_path not in rom_files.files
        # Parent system dir still tracked
        assert system_dir in rom_files.dirs
        assert uow.rom_installs.get(42) is None


class TestUninstallAllRoms:
    @pytest.mark.asyncio
    async def test_removes_all_installed(self, service, uow, rom_files):
        file_a = f"{_ROMS_BASE}/n64/game_a.z64"
        file_b = f"{_ROMS_BASE}/n64/game_b.z64"
        rom_files.files[file_a] = b"\x00" * 100
        rom_files.files[file_b] = b"\x00" * 100
        with uow:
            uow.roms.save(_make_rom(1))
            uow.roms.save(_make_rom(2))
            uow.rom_installs.save(_make_install(1, file_path=file_a))
            uow.rom_installs.save(_make_install(2, file_path=file_b))

        result = await service.uninstall_all_roms()

        assert result["success"] is True
        assert result["removed_count"] == 2
        assert file_a not in rom_files.files
        assert file_b not in rom_files.files
        assert list(uow.rom_installs.iter_all()) == []

    @pytest.mark.asyncio
    async def test_clears_records_even_if_files_missing(self, service, uow):
        _seed_install(uow, _make_install(1, file_path=f"{_ROMS_BASE}/n64/nonexistent.z64"))

        result = await service.uninstall_all_roms()
        assert result["success"] is True
        assert list(uow.rom_installs.iter_all()) == []

    @pytest.mark.asyncio
    async def test_records_empty_applied_for_each_bound_deleted_rom(self, service, uow, rom_files):
        # The frontend resets each kept shortcut's launch command to "" for the
        # returned app_ids (#1146); the backend records "" so the next sync skips
        # each now-correct shortcut (#1383).
        file_a = f"{_ROMS_BASE}/n64/game_a.z64"
        file_b = f"{_ROMS_BASE}/n64/game_b.z64"
        rom_files.files[file_a] = b"\x00" * 100
        rom_files.files[file_b] = b"\x00" * 100
        with uow:
            uow.roms.save(_make_rom(1))
            uow.roms.save(_make_rom(2))
            uow.rom_installs.save(_make_install(1, file_path=file_a))
            uow.rom_installs.save(_make_install(2, file_path=file_b))
            uow.roms.set_applied_launch_options(1, "flatpak run … /game_a.z64")
            uow.roms.set_applied_launch_options(2, "flatpak run … /game_b.z64")

        result = await service.uninstall_all_roms()

        assert result["success"] is True
        with uow:
            assert uow.roms.get(1).applied_launch_options == ""
            assert uow.roms.get(2).applied_launch_options == ""

    @pytest.mark.asyncio
    async def test_handles_empty_state(self, service, uow):
        _ = uow
        result = await service.uninstall_all_roms()
        assert result["success"] is True
        assert result["removed_count"] == 0

    @pytest.mark.asyncio
    async def test_retains_playtime_and_roms_rows(self, service, uow, rom_files):
        """RETENTION (ADR-0007 / D1): bulk uninstall drops only files + install records.

        Identity rows and playtime survive for every ROM.
        """
        from domain.playtime import Playtime

        file_a = f"{_ROMS_BASE}/n64/game_a.z64"
        file_b = f"{_ROMS_BASE}/n64/game_b.z64"
        rom_files.files[file_a] = b"\x00" * 100
        rom_files.files[file_b] = b"\x00" * 100
        with uow:
            uow.roms.save(_make_rom(1))
            uow.roms.save(_make_rom(2))
            uow.rom_installs.save(_make_install(1, file_path=file_a))
            uow.rom_installs.save(_make_install(2, file_path=file_b))
            uow.playtime.save(1, Playtime(total_seconds=100, session_count=1))
            uow.playtime.save(2, Playtime(total_seconds=200, session_count=1))

        result = await service.uninstall_all_roms()

        assert result["success"] is True
        assert list(uow.rom_installs.iter_all()) == []
        # Identity + playtime survive the bulk uninstall.
        assert uow.roms.get(1) is not None
        assert uow.roms.get(2) is not None
        pt1 = uow.playtime.get(1)
        pt2 = uow.playtime.get(2)
        assert pt1 is not None and pt1.total_seconds == 100
        assert pt2 is not None and pt2.total_seconds == 200
        assert uow.committed is True

    @pytest.mark.asyncio
    async def test_deletes_rom_directories(self, service, uow, rom_files):
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        rom_files.files[f"{rom_dir}/disc1.bin"] = b"\x00" * 100
        _seed_install(
            uow,
            _make_install(1, file_path=f"{rom_dir}/FF7.m3u", rom_dir=rom_dir, system="psx"),
            platform_slug="psx",
        )

        result = await service.uninstall_all_roms()
        assert result["success"] is True
        assert result["removed_count"] == 1
        assert all(not p.startswith(rom_dir + "/") for p in rom_files.files)

    @pytest.mark.asyncio
    async def test_outside_roms_dir_is_partial_failure_and_preserves_record(self, service, uow, rom_files):
        good_file = f"{_ROMS_BASE}/n64/game_a.z64"
        rom_files.files[good_file] = b"\x00" * 100
        bad_file = "/outside/game_b.z64"
        rom_files.files[bad_file] = b"\x00" * 100
        with uow:
            uow.roms.save(_make_rom(1))
            uow.roms.save(_make_rom(2, platform_slug="snes"))
            uow.rom_installs.save(_make_install(1, file_path=good_file))
            uow.rom_installs.save(_make_install(2, file_path=bad_file, rom_dir=None, system="snes"))

        result = await service.uninstall_all_roms()
        assert isinstance(result, UninstallIncomplete)
        assert result.removed_count == 1
        assert len(result.errors) == 1
        assert good_file not in rom_files.files
        assert bad_file in rom_files.files  # not deleted (outside roms dir)
        assert [install.rom_id for install in uow.rom_installs.iter_all()] == [2]

    @pytest.mark.asyncio
    async def test_partial_failure_answers_uninstall_incomplete_with_its_counts(self, service, uow, rom_files):
        """Bad path: one of three deletions raises OSError → ``uninstall_incomplete``, the failing record survives."""
        file_a = f"{_ROMS_BASE}/n64/game_a.z64"
        file_b = f"{_ROMS_BASE}/n64/game_b.z64"
        file_c = f"{_ROMS_BASE}/n64/game_c.z64"
        for p in (file_a, file_b, file_c):
            rom_files.files[p] = b"\x00" * 100
        rom_files.remove_file_failures.add(file_b)
        with uow:
            uow.roms.save(_make_rom(1))
            uow.roms.save(_make_rom(2))
            uow.roms.save(_make_rom(3))
            uow.rom_installs.save(_make_install(1, file_path=file_a))
            uow.rom_installs.save(_make_install(2, file_path=file_b))
            uow.rom_installs.save(_make_install(3, file_path=file_c))

        result = await service.uninstall_all_roms()

        assert result == UninstallIncomplete(
            reason="uninstall_incomplete",
            message="1 ROM could not be uninstalled",
            removed_count=2,
            errors=[{"rom_id": "2", "error": f"simulated remove_file failure: {file_b}"}],
            app_ids=[1001, 1003],
            prune_lease_token=result.prune_lease_token,
        )
        # Records for successful deletions are cleared; the failing entry survives so the user can retry.
        assert uow.rom_installs.get(1) is None
        assert uow.rom_installs.get(2) is not None
        assert uow.rom_installs.get(3) is None

    @pytest.mark.asyncio
    async def test_all_success_returns_empty_errors(self, service, uow, rom_files):
        """Happy path: all 3 deletions succeed → ``success`` is True and ``errors`` is empty."""
        file_a = f"{_ROMS_BASE}/n64/game_a.z64"
        file_b = f"{_ROMS_BASE}/n64/game_b.z64"
        file_c = f"{_ROMS_BASE}/n64/game_c.z64"
        for p in (file_a, file_b, file_c):
            rom_files.files[p] = b"\x00" * 100
        with uow:
            for rid, fp in ((1, file_a), (2, file_b), (3, file_c)):
                uow.roms.save(_make_rom(rid))
                uow.rom_installs.save(_make_install(rid, file_path=fp))

        result = await service.uninstall_all_roms()

        assert result["success"] is True
        assert result["removed_count"] == 3
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_empty_state_returns_success_with_empty_errors(self, service, uow):
        """Edge: no installed ROMs → ``success`` is True and ``errors`` is empty."""
        _ = uow
        result = await service.uninstall_all_roms()

        assert result["success"] is True
        assert result["removed_count"] == 0
        assert result["errors"] == []


class TestUninstallAllRomsAppIds:
    """The ``app_ids`` field the frontend uses to reset kept shortcuts' launch_options (#1146)."""

    @pytest.mark.asyncio
    async def test_returns_bound_app_ids_for_deleted_roms(self, service, uow, rom_files):
        """Happy path: each deleted ROM's bound ``shortcut_app_id`` is returned so the frontend can reset it."""
        file_a = f"{_ROMS_BASE}/n64/game_a.z64"
        file_b = f"{_ROMS_BASE}/n64/game_b.z64"
        rom_files.files[file_a] = b"\x00" * 100
        rom_files.files[file_b] = b"\x00" * 100
        with uow:
            uow.roms.save(_make_rom(1))
            uow.roms.save(_make_rom(2))
            uow.rom_installs.save(_make_install(1, file_path=file_a))
            uow.rom_installs.save(_make_install(2, file_path=file_b))

        result = await service.uninstall_all_roms()

        assert result["success"] is True
        assert sorted(result["app_ids"]) == [1001, 1002]

    @pytest.mark.asyncio
    async def test_omits_app_id_for_unbound_rom(self, service, uow, rom_files):
        """Edge: a deleted ROM with no bound shortcut (``shortcut_app_id`` is None) contributes no app_id."""
        file_a = f"{_ROMS_BASE}/n64/game_a.z64"
        file_b = f"{_ROMS_BASE}/n64/game_b.z64"
        rom_files.files[file_a] = b"\x00" * 100
        rom_files.files[file_b] = b"\x00" * 100
        with uow:
            uow.roms.save(_make_rom(1))  # bound → 1001
            uow.roms.save(_make_rom(2, bound=False))  # unbound → no app_id
            uow.rom_installs.save(_make_install(1, file_path=file_a))
            uow.rom_installs.save(_make_install(2, file_path=file_b))

        result = await service.uninstall_all_roms()

        assert result["success"] is True
        assert result["app_ids"] == [1001]

    @pytest.mark.asyncio
    async def test_app_ids_only_for_successfully_deleted(self, service, uow, rom_files):
        """Bad path: a ROM whose file deletion raised contributes no app_id — only deleted ones are reset."""
        file_a = f"{_ROMS_BASE}/n64/game_a.z64"
        file_b = f"{_ROMS_BASE}/n64/game_b.z64"
        file_c = f"{_ROMS_BASE}/n64/game_c.z64"
        for p in (file_a, file_b, file_c):
            rom_files.files[p] = b"\x00" * 100
        rom_files.remove_file_failures.add(file_b)
        with uow:
            for rid, fp in ((1, file_a), (2, file_b), (3, file_c)):
                uow.roms.save(_make_rom(rid))
                uow.rom_installs.save(_make_install(rid, file_path=fp))

        result = await service.uninstall_all_roms()

        assert isinstance(result, UninstallIncomplete)
        # ROM 2's deletion raised → its app_id (1002) is absent; the deleted 1 and 3 are present.
        assert sorted(result.app_ids) == [1001, 1003]

    @pytest.mark.asyncio
    async def test_empty_state_returns_empty_app_ids(self, service, uow):
        """Edge: no installed ROMs → ``app_ids`` is empty."""
        _ = uow
        result = await service.uninstall_all_roms()

        assert result["app_ids"] == []


class TestDownloadQueueCleanup:
    """Eviction of the download queue on successful ROM removal."""

    @pytest.mark.asyncio
    async def test_remove_rom_evicts_queue_on_success(self, service, uow, queue_cleanup, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/zelda.z64"
        rom_files.files[rom_path] = b"\x00" * 100
        _seed_install(uow, _make_install(42, file_path=rom_path))

        result = await service.remove_rom(42)
        assert result["success"] is True
        assert queue_cleanup.evicted == [42]
        assert queue_cleanup.cleared == 0

    @pytest.mark.asyncio
    async def test_remove_rom_does_not_evict_when_not_installed(self, service, queue_cleanup):
        coro = service.remove_rom(999)
        with pytest.raises(NotInstalled):
            await coro
        assert queue_cleanup.evicted == []

    @pytest.mark.asyncio
    async def test_uninstall_all_roms_clears_queue(self, service, uow, queue_cleanup, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"\x00" * 100
        _seed_install(uow, _make_install(1, file_path=rom_path))

        result = await service.uninstall_all_roms()
        assert result["success"] is True
        assert queue_cleanup.cleared == 1

    @pytest.mark.asyncio
    async def test_no_cleanup_dependency_is_safe(self, logger):
        """Without a ``DownloadQueueCleanup`` wired, eviction is skipped."""
        rom_files = FakeRomFileStore()
        uow = FakeUnitOfWork()
        rom_path = f"{_ROMS_BASE}/n64/g.z64"
        rom_files.files[rom_path] = b"\x00" * 100
        _seed_install(uow, _make_install(7, file_path=rom_path))

        svc = RomRemovalService(
            config=RomRemovalServiceConfig(
                logger=logger,
                loop=asyncio.get_running_loop(),
                clock=FakeClock(),
                emit=RecordingEmitter(),
                rom_file_store=rom_files,
                retrodeck_paths=FakeRetroDeckPaths(roms=_ROMS_BASE),
                download_queue_cleanup=None,
                uow_factory=FakeUnitOfWorkFactory(uow),
                conflict_rules=_make_conflict_rules(),
            ),
        )

        result = await svc.remove_rom(7)
        assert result["success"] is True

        result2 = await svc.uninstall_all_roms()
        assert isinstance(result2, dict)
        assert result2["success"] is True


class TestBadPathRemoveRom:
    """Coverage for the ``remove_rom`` exception handler."""

    @pytest.mark.asyncio
    async def test_remove_rom_handles_filesystem_failure(self, service, uow, queue_cleanup, rom_files):
        """``remove_tree`` OSError is refused with ``uninstall_failed``; the record is NOT deleted, no eviction."""
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        rom_files.files[f"{rom_dir}/disc1.bin"] = b"\x00" * 100
        rom_files.remove_tree_failures.add(rom_dir)
        _seed_install(
            uow,
            _make_install(42, file_path=f"{rom_dir}/FF7.m3u", rom_dir=rom_dir, system="psx"),
            platform_slug="psx",
        )

        coro = service.remove_rom(42)
        with pytest.raises(Refused) as refused:
            await coro

        assert (refused.value.reason, refused.value.message) == ("uninstall_failed", "Failed to delete ROM files")
        # The install record remains because the IO helper raised before the delete UoW.
        assert uow.rom_installs.get(42) is not None
        # No queue eviction on failure.
        assert queue_cleanup.evicted == []

    @pytest.mark.asyncio
    async def test_an_exception_outside_the_three_handled_types_is_not_refused(self, service, uow, rom_files):
        """A bug stays a bug: only an ``OSError``, ``ValueError`` or ``RuntimeError`` becomes ``uninstall_failed``."""
        _seed_installed_file(uow, rom_files, 42)

        def broken(*_args):
            raise KeyError("a bug")

        service._remove_rom_io = broken
        coro = service.remove_rom_unchecked(42)
        with pytest.raises(KeyError):
            await coro

        assert service._removals_in_flight == set()


class TestClaimDiscipline:
    """Which claim discipline authorizes which removal."""

    def test_uninstall_claims_identity_only(self, service, uow, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(1, file_path=rom_path))

        service._remove_rom_io(1, _installed(uow, 1))

        assert rom_files.claim_digests == [False]

    def test_bulk_uninstall_claims_identity_only(self, service, uow, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(1, file_path=rom_path))

        service._uninstall_all_roms_io([_installed(uow, 1)])

        assert rom_files.claim_digests == [False]

    def test_a_source_a_sealed_bundle_did_not_capture_stays_content_bound(self, service, uow, rom_files):
        """The bundle exists, so the hashes still have a copy to bind this deletion to."""
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(1, file_path=rom_path))

        # A run that sealed a bundle but captured no installed ROM content.
        service.delete_rom_files(1, {})

        assert rom_files.claim_digests == [True]

    def test_a_cleanup_run_with_no_bundle_at_all_claims_identity_only(self, service, uow, rom_files):
        """Recovery off means no copy anywhere, so there is nothing for a hash to bind to."""
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(1, file_path=rom_path))

        service.delete_rom_files(1)

        assert rom_files.claim_digests == [False]

    def test_a_handed_in_claim_is_not_re_claimed(self, service, uow, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(1, file_path=rom_path))
        claim = rom_files.claim_source(rom_path, _ROMS_BASE)
        rom_files.claim_digests.clear()

        service.delete_rom_files(1, {rom_path: claim})

        assert rom_files.claim_digests == []

    def test_self_claimed_uninstall_reads_no_file_content(self, tmp_path, logger):
        """Regression seam (#1664): a self-claimed removal hashes nothing at all."""
        import adapters.descriptor_paths as descriptor_paths

        roms = tmp_path / "roms"
        rom_dir = roms / "psx" / "Game"
        (rom_dir / "sub").mkdir(parents=True)
        (rom_dir / "disc.bin").write_bytes(b"\x00" * 4096)
        (rom_dir / "sub" / "data.bin").write_bytes(b"\x01" * 4096)
        uow = FakeUnitOfWork()
        _seed_install(
            uow,
            _make_install(1, file_path=str(rom_dir / "disc.bin"), rom_dir=str(rom_dir), system="psx"),
            platform_slug="psx",
        )
        real_service = RomRemovalService(
            config=RomRemovalServiceConfig(
                logger=logger,
                loop=asyncio.new_event_loop(),
                clock=FakeClock(),
                emit=RecordingEmitter(),
                rom_file_store=RomFileAdapter(),
                retrodeck_paths=FakeRetroDeckPaths(roms=str(roms)),
                download_queue_cleanup=None,
                uow_factory=FakeUnitOfWorkFactory(uow),
                conflict_rules=_make_conflict_rules(),
            )
        )
        original = descriptor_paths._sha256_fd
        calls = []
        descriptor_paths._sha256_fd = lambda fd, should_abort=None: calls.append(fd) or original(fd, should_abort)
        try:
            real_service._remove_rom_io(1, _installed(uow, 1))
        finally:
            descriptor_paths._sha256_fd = original

        assert calls == []
        assert not rom_dir.exists()
        assert uow.rom_installs.get(1) is None


class TestInterruptedStagingRecovery:
    """A removal interrupted between the staging rename and the last unlink (#1664)."""

    @staticmethod
    def _service(tmp_path, logger, uow, roms):
        return RomRemovalService(
            config=RomRemovalServiceConfig(
                logger=logger,
                loop=asyncio.new_event_loop(),
                clock=FakeClock(),
                emit=RecordingEmitter(),
                rom_file_store=RomFileAdapter(),
                retrodeck_paths=FakeRetroDeckPaths(roms=str(roms)),
                download_queue_cleanup=None,
                uow_factory=FakeUnitOfWorkFactory(uow),
                conflict_rules=_make_conflict_rules(),
            )
        )

    def test_a_retry_over_a_staged_away_source_recovers(self, tmp_path, logger):
        roms = tmp_path / "roms"
        rom_dir = roms / "psx" / "Game"
        rom_dir.mkdir(parents=True)
        (rom_dir / "disc.bin").write_bytes(b"\x00" * 64)
        staged = rom_dir.parent / f".Game.romm-prune-{rom_dir.stat().st_ino}"
        rom_dir.rename(staged)
        uow = FakeUnitOfWork()
        _seed_install(
            uow,
            _make_install(1, file_path=str(rom_dir / "disc.bin"), rom_dir=str(rom_dir), system="psx"),
            platform_slug="psx",
        )
        service = self._service(tmp_path, logger, uow, roms)

        result = service._delete_rom_files(_installed(uow, 1))

        assert result["success"] is True
        assert result["changed"] is True
        assert not staged.exists()
        assert not rom_dir.exists()

    def test_a_retry_drops_the_install_row_and_reports_success(self, tmp_path, logger):
        roms = tmp_path / "roms"
        rom_dir = roms / "psx" / "Game"
        rom_dir.mkdir(parents=True)
        (rom_dir / "disc.bin").write_bytes(b"\x00" * 64)
        staged = rom_dir.parent / f".Game.romm-prune-{rom_dir.stat().st_ino}"
        rom_dir.rename(staged)
        uow = FakeUnitOfWork()
        _seed_install(
            uow,
            _make_install(1, file_path=str(rom_dir / "disc.bin"), rom_dir=str(rom_dir), system="psx"),
            platform_slug="psx",
        )
        service = self._service(tmp_path, logger, uow, roms)

        service._remove_rom_io(1, _installed(uow, 1))

        assert not staged.exists()
        assert uow.rom_installs.get(1) is None

    def test_a_bundle_backed_run_never_adopts_staging_debris(self, tmp_path, logger):
        """Its authority came from a seal that a partially consumed source no longer matches."""
        roms = tmp_path / "roms"
        rom_dir = roms / "psx" / "Game"
        rom_dir.mkdir(parents=True)
        (rom_dir / "disc.bin").write_bytes(b"\x00" * 64)
        staged = rom_dir.parent / f".Game.romm-prune-{rom_dir.stat().st_ino}"
        rom_dir.rename(staged)
        uow = FakeUnitOfWork()
        _seed_install(
            uow,
            _make_install(1, file_path=str(rom_dir / "disc.bin"), rom_dir=str(rom_dir), system="psx"),
            platform_slug="psx",
        )
        service = self._service(tmp_path, logger, uow, roms)

        # A run that sealed a bundle but captured no installed ROM content.
        result = service.delete_rom_files(1, {})

        assert result.failure is None
        assert result.changed is False
        assert staged.is_dir()

    def test_a_cleanup_run_with_no_bundle_adopts_debris_like_an_uninstall(self, tmp_path, logger):
        """Recovery off self-seals its claim, so the same re-seal authorizes finishing the removal."""
        roms = tmp_path / "roms"
        rom_dir = roms / "psx" / "Game"
        rom_dir.mkdir(parents=True)
        (rom_dir / "disc.bin").write_bytes(b"\x00" * 64)
        staged = rom_dir.parent / f".Game.romm-prune-{rom_dir.stat().st_ino}"
        rom_dir.rename(staged)
        uow = FakeUnitOfWork()
        _seed_install(
            uow,
            _make_install(1, file_path=str(rom_dir / "disc.bin"), rom_dir=str(rom_dir), system="psx"),
            platform_slug="psx",
        )
        service = self._service(tmp_path, logger, uow, roms)

        result = service.delete_rom_files(1)

        assert result.failure is None
        assert result.changed is True
        assert not staged.exists()


class TestBulkAndSingleExclusion:
    """A bulk uninstall owns every tree, so it and a single removal exclude each other (#1664)."""

    @pytest.mark.asyncio
    async def test_a_bulk_run_is_refused_while_a_single_removal_is_in_flight(self, service, uow, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(42, file_path=rom_path))
        bulk: list[BaseException | None] = []
        entered: list[bool] = []

        original = service._delete_rom_files

        def run_a_bulk_uninstall_mid_removal(*args, **kwargs):
            # The sentinel is set *before* the nested call, not after it: were
            # the guard to regress, the bulk run would re-enter this hook while
            # `bulk` was still empty and recurse without bound. A lost guard has
            # to fail the assertion below, not hang the suite.
            if not entered:
                entered.append(True)
                bulk.append(asyncio.run_coroutine_threadsafe(service.uninstall_all_roms(), service._loop).exception())
            return original(*args, **kwargs)

        service._delete_rom_files = run_a_bulk_uninstall_mid_removal
        result = await service.remove_rom(42)

        assert result["success"] is True
        [refused] = bulk
        assert isinstance(refused, Refused)
        # No removal payload: that absence is the frontend's refusal discriminant.
        assert (refused.reason, refused.message, refused.details) == (
            "in_progress",
            "A ROM is already being uninstalled",
            {},
        )

    @pytest.mark.asyncio
    async def test_a_single_removal_is_refused_while_a_bulk_run_holds_that_rom(self, service, uow, rom_files):
        """The bulk run claims each ROM it will remove, so the per-ROM guard is what refuses."""
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(42, file_path=rom_path))
        single: list[BaseException | None] = []
        entered: list[bool] = []

        original = service._delete_rom_files

        def press_uninstall_mid_bulk(*args, **kwargs):
            # Sentinel set before the nested call — see the sibling test.
            if not entered:
                entered.append(True)
                single.append(asyncio.run_coroutine_threadsafe(service.remove_rom(42), service._loop).exception())
            return original(*args, **kwargs)

        service._delete_rom_files = press_uninstall_mid_bulk
        result = await service.uninstall_all_roms()

        assert result["success"] is True
        [refused] = single
        assert isinstance(refused, Refused)
        assert (refused.reason, refused.message) == ("in_progress", "This ROM is already being uninstalled")

    @pytest.mark.asyncio
    async def test_both_run_again_once_the_other_has_finished(self, service, uow, rom_files):
        """Edge: the guards are released, so neither entry point stays locked out."""
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(42, file_path=rom_path))

        first = await service.remove_rom(42)
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(42, file_path=rom_path))
        second = await service.uninstall_all_roms()

        assert first["success"] is True
        assert second["success"] is True
        assert second["removed_count"] == 1


class TestConcurrentUninstall:
    """The per-ROM in-flight guard (#1664)."""

    @pytest.mark.asyncio
    async def test_a_second_press_for_the_same_rom_is_refused(self, service, uow, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(42, file_path=rom_path))
        second: list[BaseException | None] = []

        original = service._delete_rom_files

        def remove_while_a_second_press_arrives(*args, **kwargs):
            second.append(asyncio.run_coroutine_threadsafe(service.remove_rom(42), service._loop).exception())
            return original(*args, **kwargs)

        service._delete_rom_files = remove_while_a_second_press_arrives
        result = await service.remove_rom(42)

        assert result["success"] is True
        [refused] = second
        assert isinstance(refused, Refused)
        assert (refused.reason, refused.message) == ("in_progress", "This ROM is already being uninstalled")
        assert rom_path not in rom_files.files

    @pytest.mark.asyncio
    async def test_a_later_press_for_the_same_rom_is_accepted_again(self, service, uow, rom_files):
        """Edge: the guard is released, so a retry after a failure is not locked out."""
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        rom_files.files[f"{rom_dir}/disc1.bin"] = b"\x00" * 100
        rom_files.remove_tree_failures.add(rom_dir)
        _seed_install(
            uow,
            _make_install(42, file_path=f"{rom_dir}/FF7.m3u", rom_dir=rom_dir, system="psx"),
            platform_slug="psx",
        )

        first = service.remove_rom(42)
        with pytest.raises(Refused):
            await first
        rom_files.remove_tree_failures.clear()
        second = await service.remove_rom(42)

        assert second["success"] is True

    @pytest.mark.asyncio
    async def test_a_different_rom_is_not_blocked(self, service, uow, rom_files):
        path_a = f"{_ROMS_BASE}/n64/a.z64"
        path_b = f"{_ROMS_BASE}/n64/b.z64"
        rom_files.files[path_a] = b"a"
        rom_files.files[path_b] = b"b"
        _seed_install(uow, _make_install(1, file_path=path_a))
        _seed_install(uow, _make_install(2, file_path=path_b))
        other: dict[str, object] = {}
        entered: list[bool] = []

        original = service._delete_rom_files

        def remove_while_another_rom_is_pressed(*args, **kwargs):
            # Sentinel set before the nested call, so the nested removal does not press again.
            if not entered:
                entered.append(True)
                other.update(asyncio.run_coroutine_threadsafe(service.remove_rom(2), service._loop).result())
            return original(*args, **kwargs)

        service._delete_rom_files = remove_while_another_rom_is_pressed
        result = await service.remove_rom(1)

        assert result["success"] is True
        assert other["success"] is True

    @pytest.mark.asyncio
    async def test_a_cancelled_press_keeps_refusing_a_second_until_its_files_are_deleted(self, service, uow, rom_files):
        """The call goes, the thread deleting the files does not, and the claim is the thread's."""
        rom_path = _seed_installed_file(uow, rom_files, 42)
        entered = threading.Event()
        release = threading.Event()
        original = service._delete_rom_files

        def remove_once_released(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                assert release.wait(timeout=5)
            return original(*args, **kwargs)

        service._delete_rom_files = remove_once_released
        first = asyncio.ensure_future(service.remove_rom(42))
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = service.remove_rom(42)
        with pytest.raises(Refused) as refused:
            await second
        assert refused.value.reason == "in_progress"

        release.set()
        for _ in range(500):
            if 42 not in service._removals_in_flight:
                break
            await asyncio.sleep(0.01)
        assert service._removals_in_flight == set()
        assert rom_path not in rom_files.files

    @pytest.mark.asyncio
    async def test_a_cancelled_bulk_run_keeps_refusing_a_single_removal_until_its_files_are_deleted(
        self, service, uow, rom_files
    ):
        """The bulk run's claims are its thread's too, so a single press cannot work a tree it is still deleting."""
        _seed_installed_file(uow, rom_files, 1)
        rom_path = _seed_installed_file(uow, rom_files, 2)
        entered = threading.Event()
        release = threading.Event()
        original = service._delete_rom_files

        def remove_once_released(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                assert release.wait(timeout=5)
            return original(*args, **kwargs)

        service._delete_rom_files = remove_once_released
        bulk = asyncio.ensure_future(service.uninstall_all_roms())
        assert await asyncio.to_thread(entered.wait, 5)
        bulk.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bulk

        single = service.remove_rom(2)
        with pytest.raises(Refused) as refused:
            await single
        assert refused.value.reason == "in_progress"

        release.set()
        await _until(lambda: not service._removals_in_flight)
        assert rom_path not in rom_files.files

    @pytest.mark.asyncio
    async def test_a_cancelled_removal_whose_thread_then_fails_is_logged(self, service, uow, rom_files, caplog):
        """Nobody awaits the failure any more, so the log line is the only place it can show."""
        rom_path = _seed_installed_file(uow, rom_files, 42)
        rom_files.remove_file_failures.add(rom_path)
        entered = threading.Event()
        release = threading.Event()
        original = service._delete_rom_files

        def remove_once_released(*args, **kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return original(*args, **kwargs)

        service._delete_rom_files = remove_once_released
        removal = asyncio.ensure_future(service.remove_rom(42))
        assert await asyncio.to_thread(entered.wait, 5)
        removal.cancel()
        with pytest.raises(asyncio.CancelledError):
            await removal

        with caplog.at_level(logging.INFO):
            release.set()
            await _until(lambda: not service._removals_in_flight)

        assert [
            (r.levelno, r.getMessage()) for r in caplog.records if "after its call was cancelled" in r.getMessage()
        ] == [
            (
                logging.ERROR,
                f"Uninstall of rom_id=42 ended after its call was cancelled: simulated remove_file failure: {rom_path}",
            )
        ]


async def _until(condition) -> None:
    """Let the loop run until *condition* holds, failing after five seconds."""
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never held")


class TestACancelledRemovalHoldsOffACleanup:
    """A removal whose call was cancelled keeps a cleanup from starting until its files are deleted."""

    @pytest.mark.parametrize("call", ["remove_rom", "uninstall_all_roms"])
    async def test_a_cleanup_cannot_start_until_the_thread_ends(self, service, uow, rom_files, prune_conflicts, call):
        _seed_installed_file(uow, rom_files, 42)
        entered = threading.Event()
        release = threading.Event()
        original = service._delete_rom_files

        def remove_once_released(*args, **kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return original(*args, **kwargs)

        service._delete_rom_files = remove_once_released
        removal = asyncio.ensure_future(
            service.remove_rom(42) if call == "remove_rom" else service.uninstall_all_roms()
        )
        assert await asyncio.to_thread(entered.wait, 5)
        removal.cancel()
        with pytest.raises(asyncio.CancelledError):
            await removal

        assert await prune_conflicts.reserve_start("start_prune") is not None

        release.set()
        await _until(lambda: prune_conflicts.conflicting_operations == 0)
        assert await prune_conflicts.reserve_start("start_prune") is None
        prune_conflicts.release_reservation()


class TestRemovalProgressFrames:
    """``uninstall_progress`` visibility for a removal long enough to look dead (#1664)."""

    @pytest.mark.asyncio
    async def test_a_multi_file_removal_emits_a_terminal_frame(self, service, uow, rom_files, emitter):
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        rom_files.files[f"{rom_dir}/disc1.bin"] = b"\x00" * 100
        rom_files.files[f"{rom_dir}/disc2.bin"] = b"\x00" * 100
        _seed_install(
            uow,
            _make_install(42, file_path=f"{rom_dir}/FF7.m3u", rom_dir=rom_dir, system="psx"),
            platform_slug="psx",
        )

        await service.remove_rom(42)
        # The frame is marshaled from the executor thread and emitted from a task
        # of its own, so it can land a tick after the removal returns.
        async with asyncio.timeout(2):
            while not emitter.payloads("uninstall_progress"):
                await asyncio.sleep(0.001)

        assert emitter.payloads("uninstall_progress")[-1] == {
            "rom_id": 42,
            "files_removed": 2,
            "files_total": 2,
        }

    @pytest.mark.asyncio
    async def test_a_single_file_removal_emits_nothing(self, service, uow, rom_files, emitter):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(42, file_path=rom_path))

        await service.remove_rom(42)
        await asyncio.sleep(0)

        assert emitter.payloads("uninstall_progress") == []


# ── The removal leases and the unchecked twin ─────────────────────────────────


def _seed_installed_file(uow: FakeUnitOfWork, rom_files: FakeRomFileStore, rom_id: int, *, bound: bool = True) -> str:
    rom_path = f"{_ROMS_BASE}/n64/game_{rom_id}.z64"
    rom_files.files[rom_path] = b"rom"
    with uow:
        uow.roms.save(_make_rom(rom_id, bound=bound))
        uow.rom_installs.save(_make_install(rom_id, file_path=rom_path))
    return rom_path


class TestTheRemoveRomLease:
    """A removal that succeeded carries a ``rom_uninstall`` lease for the frontend's launch-options reset."""

    async def test_a_removal_that_succeeded_carries_a_lease(self, service, uow, rom_files, prune_conflicts):
        _seed_installed_file(uow, rom_files, 42)

        result = await service.remove_rom(42)

        assert result["success"] is True
        assert result["prune_lease_token"].startswith("rom_uninstall:")
        assert prune_conflicts.conflicting_operations == 1

    async def test_the_lease_is_taken_while_the_removals_operation_still_holds(
        self, service, uow, rom_files, prune_conflicts, monkeypatch
    ):
        """No cleanup can start between the removal's operation and the lease that outlasts it."""
        _seed_installed_file(uow, rom_files, 42)
        seen = _record_operations_at_lease(prune_conflicts, monkeypatch)

        await service.remove_rom(42)

        assert seen == [["remove_rom"]]

    async def test_a_rom_with_nothing_installed_takes_no_lease(self, service, prune_conflicts):
        coro = service.remove_rom(42)
        with pytest.raises(NotInstalled):
            await coro

        assert prune_conflicts.conflicting_operations == 0

    async def test_a_refused_removal_removes_nothing_and_carries_none(self, service, uow, rom_files, prune_conflicts):
        rom_path = _seed_installed_file(uow, rom_files, 42)
        prune_conflicts.register_run("held-run")

        with _refused_by_conflict_rule("prune_active"):
            await service.remove_rom(42)

        assert rom_path in rom_files.files
        assert prune_conflicts.conflicting_operations == 0


class TestRemoveRomUnchecked:
    """The download service's sibling supersede removes through the twin, which no rule refuses."""

    async def test_it_removes_while_every_rule_of_the_endpoint_holds(self, service, uow, rom_files, prune_conflicts):
        rom_path = _seed_installed_file(uow, rom_files, 42)
        prune_conflicts.register_run("held-run")
        service._rules = _make_conflict_rules(prune_conflicts=prune_conflicts, migration_pending=True)

        result = await service.remove_rom_unchecked(42)

        assert result["success"] is True
        assert rom_path not in rom_files.files

    async def test_it_takes_no_lease(self, service, uow, rom_files, prune_conflicts):
        _seed_installed_file(uow, rom_files, 42)

        result = await service.remove_rom_unchecked(42)

        assert "prune_lease_token" not in result
        assert prune_conflicts.conflicting_operations == 0


class TestTheBulkUninstallLease:
    """A bulk uninstall that ran and reports bound ``app_ids`` carries a ``bulk_uninstall`` lease.

    The condition is ``app_ids``, not ``success``: a partial failure still
    reports the ROMs whose files are gone, and the frontend resets their
    launch commands under this lease.
    """

    async def test_a_run_that_reports_bound_app_ids_carries_a_lease(self, service, uow, rom_files, prune_conflicts):
        _seed_installed_file(uow, rom_files, 1)

        result = await service.uninstall_all_roms()

        assert result["app_ids"] == [1001]
        assert result["prune_lease_token"].startswith("bulk_uninstall:")
        assert prune_conflicts.conflicting_operations == 1

    async def test_the_lease_is_taken_while_the_runs_operation_still_holds(
        self, service, uow, rom_files, prune_conflicts, monkeypatch
    ):
        """No cleanup can start between the run's operation and the lease that outlasts it."""
        _seed_installed_file(uow, rom_files, 1)
        seen = _record_operations_at_lease(prune_conflicts, monkeypatch)

        await service.uninstall_all_roms()

        assert seen == [["uninstall_all_roms"]]

    async def test_a_partial_failure_that_reports_bound_app_ids_carries_a_lease(
        self, service, uow, rom_files, prune_conflicts
    ):
        _seed_installed_file(uow, rom_files, 1)
        failing = _seed_installed_file(uow, rom_files, 2)
        rom_files.remove_file_failures.add(failing)

        result = await service.uninstall_all_roms()

        assert isinstance(result, UninstallIncomplete)
        assert result.app_ids == [1001]
        assert result.prune_lease_token is not None
        assert result.prune_lease_token.startswith("bulk_uninstall:")

    async def test_a_partial_failure_that_reports_no_bound_app_id_carries_none(
        self, service, uow, rom_files, prune_conflicts
    ):
        _seed_installed_file(uow, rom_files, 1, bound=False)
        failing = _seed_installed_file(uow, rom_files, 2)
        rom_files.remove_file_failures.add(failing)

        result = await service.uninstall_all_roms()

        assert isinstance(result, UninstallIncomplete)
        assert (result.app_ids, result.prune_lease_token) == ([], None)
        assert prune_conflicts.conflicting_operations == 0

    async def test_a_run_that_reports_no_bound_app_id_carries_none(self, service, uow, rom_files, prune_conflicts):
        _seed_installed_file(uow, rom_files, 1, bound=False)

        result = await service.uninstall_all_roms()

        assert result["success"] is True
        assert result["app_ids"] == []
        assert "prune_lease_token" not in result
        assert prune_conflicts.conflicting_operations == 0

    async def test_the_services_own_refusal_takes_no_lease(self, service, uow, rom_files, prune_conflicts):
        _seed_installed_file(uow, rom_files, 1)
        service._removals_in_flight.add(1)

        coro = service.uninstall_all_roms()
        with pytest.raises(Refused) as refused:
            await coro

        assert refused.value.reason == "in_progress"
        assert prune_conflicts.conflicting_operations == 0

    @pytest.mark.parametrize(
        ("migration_pending", "sync_in_flight", "cleanup_running", "reason"),
        [
            (True, False, False, "blocked_by_migration"),
            (False, True, False, "sync_active"),
            (False, False, True, "prune_active"),
        ],
    )
    async def test_a_refused_run_removes_nothing_and_carries_none(
        self, service, uow, rom_files, prune_conflicts, migration_pending, sync_in_flight, cleanup_running, reason
    ):
        rom_path = _seed_installed_file(uow, rom_files, 1)
        if cleanup_running:
            prune_conflicts.register_run("held-run")
        service._rules = _make_conflict_rules(
            prune_conflicts=prune_conflicts, migration_pending=migration_pending, sync_in_flight=sync_in_flight
        )

        with _refused_by_conflict_rule(reason):
            await service.uninstall_all_roms()

        assert rom_path in rom_files.files
        assert prune_conflicts.conflicting_operations == 0


def _seed_missing_download(uow: FakeUnitOfWork, rom_id: int, *, bound: bool = True) -> str:
    """Seed an install record whose file is not on disk. Returns the recorded path."""
    rom_path = f"{_ROMS_BASE}/n64/game_{rom_id}.z64"
    with uow:
        uow.roms.save(_make_rom(rom_id, bound=bound))
        uow.rom_installs.save(_make_install(rom_id, file_path=rom_path))
        uow.roms.set_applied_launch_options(rom_id, f"flatpak run net.retrodeck.retrodeck {rom_path}")
    return rom_path


class TestForgetDownload:
    """An uninstall without the deletion, for a download whose files are gone (#2188 D28)."""

    async def test_it_drops_the_record_and_records_the_uninstalled_launch_command(self, service, uow, rom_files):
        _seed_missing_download(uow, 42)

        result = await service.forget_download(42)

        assert result["success"] is True
        assert result["message"] == "Download forgotten"
        assert uow.rom_installs.get(42) is None
        rom = uow.roms.get(42)
        assert rom is not None
        assert rom.applied_launch_options == ""

    async def test_an_unbound_rom_records_no_launch_command(self, service, uow, rom_files):
        _seed_missing_download(uow, 50, bound=False)

        result = await service.forget_download(50)

        assert result["success"] is True
        assert uow.rom_installs.get(50) is None
        rom = uow.roms.get(50)
        assert rom is not None
        assert rom.applied_launch_options == f"flatpak run net.retrodeck.retrodeck {_ROMS_BASE}/n64/game_50.z64"

    async def test_it_deletes_no_file(self, service, uow, rom_files):
        rom_path = _seed_missing_download(uow, 42)
        bystander = f"{_ROMS_BASE}/n64/other.z64"
        rom_files.files[bystander] = b"rom"

        await service.forget_download(42)

        assert rom_files.files == {bystander: b"rom"}
        assert rom_files.remove_file_calls == []
        assert rom_files.remove_tree_calls == []
        assert rom_files.claim_digests == []
        assert rom_files.reclaim_calls == []
        assert rom_path not in rom_files.files

    async def test_a_file_that_is_there_refuses_it(self, service, uow, rom_files, prune_conflicts):
        rom_path = _seed_installed_file(uow, rom_files, 42)

        forget = service.forget_download(42)
        with pytest.raises(Refused) as refused:
            await forget

        assert (refused.value.reason, refused.value.message, refused.value.details) == (
            "file_present",
            f"The recorded download exists: {rom_path}",
            {"path": rom_path},
        )
        assert uow.rom_installs.get(42) is not None
        assert rom_path in rom_files.files
        assert prune_conflicts.conflicting_operations == 0

    async def test_a_folder_that_is_there_refuses_it(self, service, uow, rom_files):
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        rom_files.dirs.add(rom_dir)
        _seed_install(
            uow,
            _make_install(42, file_path=f"{rom_dir}/FF7.m3u", rom_dir=rom_dir, system="psx"),
            platform_slug="psx",
        )

        forget = service.forget_download(42)
        with pytest.raises(Refused) as refused:
            await forget

        assert refused.value.reason == "file_present"
        assert refused.value.details == {"path": rom_dir}
        assert uow.rom_installs.get(42) is not None

    async def test_a_rom_with_no_record_is_not_installed(self, service, prune_conflicts):
        forget = service.forget_download(999)
        with pytest.raises(NotInstalled) as refused:
            await forget

        assert refused.value.message == "ROM not installed"
        assert prune_conflicts.conflicting_operations == 0

    async def test_it_is_refused_while_a_removal_holds_the_rom(self, service, uow, rom_files):
        rom_path = f"{_ROMS_BASE}/n64/game.z64"
        rom_files.files[rom_path] = b"rom"
        _seed_install(uow, _make_install(42, file_path=rom_path))
        refusals: list[Refused] = []
        original = service._delete_rom_files

        def remove_while_a_forget_arrives(*args, **kwargs):
            with pytest.raises(Refused) as refused:
                asyncio.run_coroutine_threadsafe(service.forget_download(42), service._loop).result()
            refusals.append(refused.value)
            return original(*args, **kwargs)

        service._delete_rom_files = remove_while_a_forget_arrives
        result = await service.remove_rom(42)

        assert result["success"] is True
        assert [(r.reason, r.message) for r in refusals] == [
            ("in_progress", "This ROM is already being uninstalled or forgotten")
        ]

    async def test_an_uninstall_after_a_refused_forget_is_accepted(self, service, uow, rom_files):
        rom_path = _seed_installed_file(uow, rom_files, 42)

        forget = service.forget_download(42)
        with pytest.raises(Refused) as refused:
            await forget
        removed = await service.remove_rom(42)

        assert refused.value.reason == "file_present"
        assert removed["success"] is True
        assert rom_path not in rom_files.files

    async def test_a_second_forget_after_a_refused_one_is_accepted(self, service, uow, rom_files):
        rom_path = _seed_installed_file(uow, rom_files, 42)
        forget = service.forget_download(42)
        with pytest.raises(Refused) as refused:
            await forget
        assert refused.value.reason == "file_present"
        del rom_files.files[rom_path]

        result = await service.forget_download(42)

        assert result["success"] is True
        assert uow.rom_installs.get(42) is None

    async def test_a_forget_after_a_completed_one_is_admitted(self, service, uow, rom_files):
        _seed_missing_download(uow, 42)
        assert (await service.forget_download(42))["success"] is True
        _seed_missing_download(uow, 42)

        result = await service.forget_download(42)

        assert result["success"] is True

    async def test_an_uninstall_after_a_failed_forget_is_accepted(self, service, uow, rom_files, monkeypatch):
        _seed_missing_download(uow, 42)
        original = service._drop_install_record

        def fail_once(rom_id):
            monkeypatch.setattr(service, "_drop_install_record", original)
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(service, "_drop_install_record", fail_once)
        forget = service.forget_download(42)
        with pytest.raises(sqlite3.OperationalError):
            await forget
        removed = await service.remove_rom(42)

        assert removed["success"] is True
        assert uow.rom_installs.get(42) is None

    async def test_it_evicts_the_download_queue_entry(self, service, uow, rom_files, queue_cleanup):
        _seed_missing_download(uow, 42)

        await service.forget_download(42)

        assert queue_cleanup.evicted == [42]

    async def test_a_forget_that_succeeded_carries_the_uninstall_lease(self, service, uow, rom_files, prune_conflicts):
        _seed_missing_download(uow, 42)

        result = await service.forget_download(42)

        assert result["prune_lease_token"].startswith("rom_uninstall:")
        assert prune_conflicts.conflicting_operations == 1

    async def test_the_lease_is_taken_while_the_forgets_operation_still_holds(
        self, service, uow, rom_files, prune_conflicts, monkeypatch
    ):
        _seed_missing_download(uow, 42)
        seen = _record_operations_at_lease(prune_conflicts, monkeypatch)

        await service.forget_download(42)

        assert seen == [["forget_download"]]

    async def test_a_refused_forget_drops_nothing_and_carries_none(self, service, uow, rom_files, prune_conflicts):
        _seed_missing_download(uow, 42)
        prune_conflicts.register_run("held-run")

        with _refused_by_conflict_rule("prune_active"):
            await service.forget_download(42)

        assert uow.rom_installs.get(42) is not None
        assert prune_conflicts.conflicting_operations == 0

    async def test_a_failed_write_propagates_and_carries_no_lease(
        self, service, uow, rom_files, prune_conflicts, monkeypatch
    ):
        """No file is removed, so there is no failure of its own to refuse: a database error propagates."""
        _seed_missing_download(uow, 42)

        def fail(_rom_id):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(service, "_drop_install_record", fail)
        forget = service.forget_download(42)
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            await forget

        assert prune_conflicts.conflicting_operations == 0
        assert service._removals_in_flight == set()
        assert uow.rom_installs.get(42) is not None

    async def test_a_cancelled_forget_keeps_refusing_a_removal_until_its_record_is_dropped(
        self, service, uow, rom_files, prune_conflicts
    ):
        """The call goes, the thread dropping the record does not, and the claim is the thread's."""
        _seed_missing_download(uow, 42)
        entered = threading.Event()
        release = threading.Event()
        original = service._drop_install_record

        def drop_once_released(rom_id):
            entered.set()
            assert release.wait(timeout=5)
            original(rom_id)

        service._drop_install_record = drop_once_released
        first = asyncio.ensure_future(service.forget_download(42))
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = service.remove_rom(42)
        with pytest.raises(Refused) as refused:
            await second
        assert refused.value.reason == "in_progress"
        assert prune_conflicts.conflicting_operations == 1

        release.set()
        await _until(lambda: not service._removals_in_flight)
        assert uow.rom_installs.get(42) is None
