"""Tests for LeftoverTmpCleanupService — the start-up removal of partial transfer files."""

import logging
import os

from fakes.fake_download_file_store import FakeDownloadFileStore
from fakes.fake_retrodeck_paths import FakeRetroDeckPaths

from adapters.download_file import DownloadFileAdapter
from services.leftover_tmp_cleanup import LeftoverTmpCleanupService, LeftoverTmpCleanupServiceConfig


def _service(logger, retrodeck_paths, file_store=None) -> LeftoverTmpCleanupService:
    return LeftoverTmpCleanupService(
        config=LeftoverTmpCleanupServiceConfig(
            logger=logger,
            download_file_store=file_store if file_store is not None else DownloadFileAdapter(),
            retrodeck_paths=retrodeck_paths,
        )
    )


def _paths_under(tmp_path) -> FakeRetroDeckPaths:
    return FakeRetroDeckPaths(
        roms=str(tmp_path / "retrodeck" / "roms"),
        bios=str(tmp_path / "retrodeck" / "bios"),
    )


class TestCleanupLeftoverTmpFiles:
    def test_removes_tmp_file(self, logger, tmp_path):
        service = _service(logger, _paths_under(tmp_path))

        system_dir = tmp_path / "retrodeck" / "roms" / "n64"
        system_dir.mkdir(parents=True)
        tmp_file = system_dir / "zelda.z64.tmp"
        tmp_file.write_text("partial download")

        service.cleanup_leftover_tmp_files()
        assert not tmp_file.exists()

    def test_removes_zip_tmp_file(self, logger, tmp_path):
        service = _service(logger, _paths_under(tmp_path))

        system_dir = tmp_path / "retrodeck" / "roms" / "psx"
        system_dir.mkdir(parents=True)
        tmp_file = system_dir / "game.zip.tmp"
        tmp_file.write_text("partial zip")

        service.cleanup_leftover_tmp_files()
        assert not tmp_file.exists()

    def test_keeps_real_rom_files(self, logger, tmp_path):
        service = _service(logger, _paths_under(tmp_path))

        system_dir = tmp_path / "retrodeck" / "roms" / "n64"
        system_dir.mkdir(parents=True)
        real_rom = system_dir / "zelda.z64"
        real_rom.write_text("real rom")
        bin_file = system_dir / "game.bin"
        bin_file.write_text("real bin")
        cue_file = system_dir / "game.cue"
        cue_file.write_text("real cue")

        service.cleanup_leftover_tmp_files()
        assert real_rom.exists()
        assert bin_file.exists()
        assert cue_file.exists()

    def test_removes_bios_tmp(self, logger, tmp_path):
        service = _service(logger, _paths_under(tmp_path))

        bios_dir = tmp_path / "retrodeck" / "bios" / "dc"
        bios_dir.mkdir(parents=True)
        tmp_file = bios_dir / "dc_boot.bin.tmp"
        tmp_file.write_text("partial bios")

        service.cleanup_leftover_tmp_files()
        assert not tmp_file.exists()

    def test_no_roms_dir_no_crash(self, logger, tmp_path):
        service = _service(logger, _paths_under(tmp_path))
        # No retrodeck/roms directory exists — should not crash
        service.cleanup_leftover_tmp_files()

    def test_handles_permission_error(self, tmp_path, caplog, logger):
        # Stage a virtual tmp file via the fake adapter so the service can
        # discover it via walk_files_matching_suffixes; the fake's
        # ``remove_failures`` set makes the subsequent remove raise OSError.
        roms_base = str(tmp_path / "retrodeck" / "roms")
        bios_base = str(tmp_path / "retrodeck" / "bios")
        tmp_file_path = os.path.join(roms_base, "n64", "zelda.z64.tmp")

        fake = FakeDownloadFileStore()
        fake.make_dirs(roms_base)
        fake.make_dirs(bios_base)
        fake.files[tmp_file_path] = b"partial"
        fake.remove_failures.add(tmp_file_path)
        service = _service(logger, _paths_under(tmp_path), fake)

        with caplog.at_level(logging.WARNING, logger=logger.name):
            service.cleanup_leftover_tmp_files()

        # Per-file warning must be emitted.
        assert any(
            "Failed to remove tmp file" in rec.message and tmp_file_path in rec.message for rec in caplog.records
        ), f"expected warning about {tmp_file_path}, got {[r.message for r in caplog.records]}"
        # File still present in fake — service swallowed the OSError.
        assert tmp_file_path in fake.files


class TestCleanupLeftoverTmpFilesNoRetrodeckPaths:
    """Tests for cleanup_leftover_tmp_files when retrodeck paths resolve to empty.

    Covers the early-return guard inside _clean_rom_tmp_files /
    _clean_bios_tmp_files when retrodeck.json is absent (roms_path()
    / bios_path() return ""). Service must not walk an empty path.
    """

    def test_empty_roms_and_bios_paths_skip_walk(self, logger):
        fake = FakeDownloadFileStore()
        # retrodeck_paths present but both helpers return empty (no
        # retrodeck.json) — service must early-return on each branch.
        service = _service(logger, FakeRetroDeckPaths(roms="", bios=""), fake)

        service.cleanup_leftover_tmp_files()

        assert fake.walk_calls == []
