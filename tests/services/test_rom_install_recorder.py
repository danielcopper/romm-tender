"""RomInstallRecorder — the row, the path a start is asked with, and the size it records.

The recorder is the one path both a completed download and an adoption reach an
install through, so these cases hold for both.
"""

import logging

import pytest
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
            ),
        )


@pytest.fixture
def harness():
    return _Harness()


class TestEveryInstallIsRecordedAndBaked:
    """Whether a file starts is asked when the game's page reads it, never here (#2195 D26, D28).

    So every install is recorded with its files, whatever its format.
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

    def test_a_ps3_pkg_is_recorded_with_its_files(self, harness):
        cleanup_calls = []

        file_path, error = self._record(
            harness,
            file_path="/roms/ps3/Puppeteer/Puppeteer.pkg",
            rom_dir="/roms/ps3/Puppeteer",
            system="ps3",
            cleanup=lambda: cleanup_calls.append(1),
        )

        assert error is None
        assert file_path == "/roms/ps3/Puppeteer/Puppeteer.pkg"
        assert cleanup_calls == []
        install = harness._uow.rom_installs.get(42)
        assert install is not None
        assert install.rom_dir == "/roms/ps3/Puppeteer"

    def test_a_ps3_folder_starts_with_its_directory_end_to_end(self, harness):
        # Recorded, then resolved through the real DiscLaunchResolver a start
        # uses: the folder, named as ES-DE scans it, is the path asked with.
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


class TestTheSizeAnInstallRecords:
    """An install records the size of the files it fetched (#2732 D1)."""

    @staticmethod
    def _record(harness, rom_detail):
        _seed_rom(harness._uow, 42)
        harness._install_recorder.do_record_install(
            rom_id=42,
            rom_detail={"platform_slug": "gc", **rom_detail},
            file_path="/roms/gc/Game.rvz/Game.rvz",
            rom_dir="/roms/gc/Game.rvz",
            system="gc",
            cleanup=lambda: None,
        )
        return harness._uow.rom_installs.get(42), harness._uow.roms.get(42)

    def test_a_download_that_left_a_soundtrack_out_records_the_game_s_files(self, harness):
        install, _rom = self._record(
            harness,
            {
                "fs_size_bytes": 1080,
                "files": [
                    {"id": 1, "file_name": "Game.rvz", "file_size_bytes": 1000, "category": "game"},
                    {"id": 2, "file_name": "01.flac", "file_size_bytes": 80, "category": "soundtrack"},
                ],
            },
        )

        assert install.fetched_size_bytes == 1000

    def test_a_download_of_the_whole_game_records_romm_s_size(self, harness):
        install, _rom = self._record(harness, {"fs_size_bytes": 4096, "files": [{"id": 1, "file_name": "Game.iso"}]})

        assert install.fetched_size_bytes == 4096

    def test_a_size_romm_does_not_state_records_none(self, harness):
        install, _rom = self._record(harness, {"fs_size_bytes": 0})

        assert install.fetched_size_bytes is None
