"""Unit tests for the firmware store's records and its backup place."""

from __future__ import annotations

import pytest

from domain.firmware_store import FirmwareLink, StoredFirmware, backup_folder, backup_path


def _stored(**overrides) -> StoredFirmware:
    fields = {
        "platform_slug": "saturn",
        "file_name": "sega_101.bin",
        "folder": "/home/deck/Tender",
        "md5": "85EC9CA47D8F6807718151CBCCA8B964",
        "size": 524288,
        "downloaded_at": "2026-10-21T20:00:00",
        "firmware_id": 7,
    }
    return StoredFirmware.mark_downloaded(**{**fields, **overrides})


class TestAStoreFile:
    def test_keeps_the_checksum_in_lower_case(self):
        assert _stored().md5 == "85ec9ca47d8f6807718151cbcca8b964"

    @pytest.mark.parametrize("missing", ["platform_slug", "file_name", "folder"])
    def test_needs_its_platform_name_and_folder(self, missing):
        with pytest.raises(ValueError, match="required"):
            _stored(**{missing: ""})

    def test_is_never_recorded_without_the_checksum_it_was_checked_against(self):
        with pytest.raises(ValueError, match="checksum"):
            _stored(md5="")


class TestALink:
    def _link(self, **overrides) -> FirmwareLink:
        fields = {
            "place": "/home/deck/retrodeck/bios/kronos/saturn_bios.bin",
            "target": "/home/deck/Tender/bios/saturn/sega_101.bin",
            "platform_slug": "saturn",
            "file_name": "sega_101.bin",
            "source": "retrodeck",
            "placed_at": "2026-10-21T20:00:00",
        }
        return FirmwareLink.mark_placed(**{**fields, **overrides})

    @pytest.mark.parametrize("relative", ["place", "target"])
    def test_names_absolute_paths_only(self, relative):
        with pytest.raises(ValueError, match="absolute"):
            self._link(**{relative: "bios/saturn_bios.bin"})


class TestTheBackupPlace:
    def test_keeps_the_original_absolute_path_below_the_dated_folder(self):
        backup = backup_path("/data", "2026-10-21_20-00-00", "/home/deck/retrodeck/bios/scph5501.bin")

        assert backup == "/data/backups/bios/2026-10-21_20-00-00/home/deck/retrodeck/bios/scph5501.bin"

    def test_folder_is_the_dated_one(self):
        assert backup_folder("/data", "2026-10-21_20-00-00") == "/data/backups/bios/2026-10-21_20-00-00"
