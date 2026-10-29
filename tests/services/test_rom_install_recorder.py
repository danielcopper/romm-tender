"""RomInstallRecorder — the row, the bake behind it, and the size write-back.

The recorder is the one path both a completed download and an adoption reach an
install through, so these cases hold for both.
"""

import logging

import pytest
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.system_time import FakeClock

from domain.rom import Rom
from services.rom_install_recorder import RomInstallRecorder, RomInstallRecorderConfig


def _seed_rom(uow: FakeUnitOfWork, rom_id: int, *, platform_slug: str = "n64") -> None:
    """Seed a synced ``Rom`` so a ``RomInstall`` save passes the FK check at commit."""
    uow.roms.save(
        Rom.synced(
            rom_id=rom_id,
            platform_slug=platform_slug,
            name=f"Game {rom_id}",
            fs_name=f"game_{rom_id}.z64",
            shortcut_app_id=1000 + rom_id,
            synced_at="2026-01-01T00:00:00+00:00",
        )
    )


class _Harness:
    """The recorder plus the state its tests inspect afterwards."""

    def __init__(self) -> None:
        self._uow = FakeUnitOfWork()
        self._install_recorder = RomInstallRecorder(
            config=RomInstallRecorderConfig(
                clock=FakeClock(),
                uow_factory=FakeUnitOfWorkFactory(self._uow),
                active_core=FakeActiveCoreResolver(default=(None, None)),
                disc_resolver=FakeDiscResolver(),
            ),
        )


@pytest.fixture
def harness():
    return _Harness()


class TestEveryInstallIsRecordedAndBaked:
    """Whether a file starts is asked when the game's page reads it, never here (#2195 D26, D28).

    So every install is recorded with its files, and gets its launch command,
    whatever its format.
    """

    def _record(self, harness, *, file_path, rom_dir, system, cleanup=lambda: None):
        _seed_rom(harness._uow, 42)
        return harness._install_recorder.do_record_install(
            rom_id=42,
            rom_detail={"platform_slug": system},
            file_path=file_path,
            rom_dir=rom_dir,
            system=system,
            cleanup=cleanup,
        )

    def test_a_ps3_pkg_is_recorded_with_its_files_and_gets_a_launch_command(self, harness):
        cleanup_calls = []

        file_path, error = self._record(
            harness,
            file_path="/roms/ps3/Puppeteer/Puppeteer.pkg",
            rom_dir="/roms/ps3/Puppeteer",
            system="ps3",
            cleanup=lambda: cleanup_calls.append(1),
        )
        _app_id, launch_options = harness._install_recorder.do_resolve_launch_bake(
            42, {"platform_slug": "ps3"}, "/roms/ps3/Puppeteer/Puppeteer.pkg"
        )

        assert error is None
        assert file_path == "/roms/ps3/Puppeteer/Puppeteer.pkg"
        assert cleanup_calls == []
        install = harness._uow.rom_installs.get(42)
        assert install is not None
        assert install.rom_dir == "/roms/ps3/Puppeteer"
        assert "'/roms/ps3/Puppeteer/Puppeteer.pkg'" in launch_options

    def test_a_ps3_folder_bakes_its_directory_end_to_end(self, harness):
        # Recorded, then resolved through the real DiscLaunchResolver the bake
        # sites use: the folder, named as ES-DE scans it, is the bake path.
        from services.disc_launch_resolver import DiscLaunchResolver, DiscLaunchResolverConfig

        rom_dir = "/roms/ps3/MyGame.ps3"
        eboot = f"{rom_dir}/PS3_GAME/USRDIR/EBOOT.BIN"

        self._record(harness, file_path=eboot, rom_dir=rom_dir, system="ps3")

        install = harness._uow.rom_installs.get(42)
        assert install is not None
        resolver = DiscLaunchResolver(
            config=DiscLaunchResolverConfig(
                list_files=lambda directory: [eboot] if directory == rom_dir else [],
                system_extensions=lambda system_name, reading=None: frozenset({".iso", ".ps3", ".ps3dir"}),
                logger=logging.getLogger("test_rom_install_recorder"),
            ),
        )
        assert resolver.resolve_for_install(install, None) == rom_dir


class TestRecordInstallFsSizeWriteBack:
    """A completed install tops up ``roms.fs_size_bytes`` from the ROM detail (#1395).

    The between-syncs freshness write-back: guarded on truthiness so a
    missing/zero server size never clobbers a good persisted value.
    """

    def test_successful_install_stamps_server_size(self, harness):
        uow = harness._uow
        _seed_rom(uow, 42)

        file_path, error = harness._install_recorder.do_record_install(
            rom_id=42,
            rom_detail={"platform_slug": "n64", "fs_size_bytes": 3_145_728},
            file_path="/roms/n64/game_42.z64",
            rom_dir=None,
            system="n64",
            cleanup=lambda: None,
        )

        assert error is None
        assert file_path == "/roms/n64/game_42.z64"
        rom = uow.roms.get(42)
        assert rom is not None
        assert rom.fs_size_bytes == 3_145_728
        # The install itself still persisted in the same UoW.
        assert uow.rom_installs.get(42) is not None

    def test_missing_fs_size_bytes_does_not_overwrite(self, harness):
        # The guard protects a good persisted value when the detail omits the size.
        uow = harness._uow
        seeded = Rom.synced(
            rom_id=42,
            platform_slug="n64",
            name="Game 42",
            fs_name="game_42.z64",
            shortcut_app_id=1042,
            synced_at="2026-01-01T00:00:00+00:00",
            fs_size_bytes=999_000,
        )
        uow.roms.save(seeded)

        _, error = harness._install_recorder.do_record_install(
            rom_id=42,
            rom_detail={"platform_slug": "n64"},  # no fs_size_bytes key
            file_path="/roms/n64/game_42.z64",
            rom_dir=None,
            system="n64",
            cleanup=lambda: None,
        )

        assert error is None
        rom = uow.roms.get(42)
        assert rom is not None
        assert rom.fs_size_bytes == 999_000

    def test_zero_fs_size_bytes_does_not_overwrite(self, harness):
        # A zero size is falsy — the guard skips the write, preserving the value.
        uow = harness._uow
        seeded = Rom.synced(
            rom_id=42,
            platform_slug="n64",
            name="Game 42",
            fs_name="game_42.z64",
            shortcut_app_id=1042,
            synced_at="2026-01-01T00:00:00+00:00",
            fs_size_bytes=999_000,
        )
        uow.roms.save(seeded)

        _, error = harness._install_recorder.do_record_install(
            rom_id=42,
            rom_detail={"platform_slug": "n64", "fs_size_bytes": 0},
            file_path="/roms/n64/game_42.z64",
            rom_dir=None,
            system="n64",
            cleanup=lambda: None,
        )

        assert error is None
        rom = uow.roms.get(42)
        assert rom is not None
        assert rom.fs_size_bytes == 999_000

    def test_negative_fs_size_bytes_does_not_overwrite(self, harness):
        # Rom.record_fs_size_bytes refuses it, so the guard keeps it out of the install.
        uow = harness._uow
        seeded = Rom.synced(
            rom_id=42,
            platform_slug="n64",
            name="Game 42",
            fs_name="game_42.z64",
            shortcut_app_id=1042,
            synced_at="2026-01-01T00:00:00+00:00",
            fs_size_bytes=999_000,
        )
        uow.roms.save(seeded)

        _, error = harness._install_recorder.do_record_install(
            rom_id=42,
            rom_detail={"platform_slug": "n64", "fs_size_bytes": -1},
            file_path="/roms/n64/game_42.z64",
            rom_dir=None,
            system="n64",
            cleanup=lambda: None,
        )

        assert error is None
        rom = uow.roms.get(42)
        assert rom is not None
        assert rom.fs_size_bytes == 999_000
