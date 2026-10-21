"""Tests for domain/firmware_platforms.py — a firmware file's platform, and where an older entry belongs."""

from __future__ import annotations

from typing import Any

from domain.firmware_platforms import (
    folder_platforms,
    library_firmware,
    listed_slugs,
    override_moves,
    refiled_record_platform,
)


def _platform(platform_id: int, slug: str, fs_slug: str | None = None, *firmware: dict[str, Any]) -> dict[str, Any]:
    return {"id": platform_id, "slug": slug, "fs_slug": fs_slug or slug, "firmware": list(firmware)}


def _file(firmware_id: int, name: str, platform_id: int, **fields: Any) -> dict[str, Any]:
    return {"id": firmware_id, "file_name": name, "platform_id": platform_id, **fields}


class TestLibraryFirmware:
    def test_every_file_belongs_to_the_platform_its_platform_id_names(self):
        platforms = [
            _platform(1, "psx", "ps", _file(10, "scph5501.bin", 1)),
            _platform(2, "saturn", None, _file(20, "saturn_bios.bin", 2)),
        ]

        rows = library_firmware(platforms)

        assert [(row["file_name"], row["platform_slug"]) for row in rows] == [
            ("scph5501.bin", "psx"),
            ("saturn_bios.bin", "saturn"),
        ]

    def test_a_shared_server_folder_does_not_decide_the_platform(self):
        """RomM's structure ``bios_from_user/{platform}`` files every platform under one folder."""
        platforms = [
            _platform(1, "psx", None, _file(10, "scph5501.bin", 1, file_path="bios_from_user/psx")),
            _platform(2, "gba", None, _file(20, "gba_bios.bin", 2, file_path="bios_from_user/gba")),
        ]

        assert {row["file_name"]: row["platform_slug"] for row in library_firmware(platforms)} == {
            "scph5501.bin": "psx",
            "gba_bios.bin": "gba",
        }

    def test_the_platform_id_decides_even_where_another_platform_carries_the_file(self):
        platforms = [_platform(1, "psx", None, _file(10, "scph5501.bin", 2)), _platform(2, "ps2", None)]

        assert [row["platform_slug"] for row in library_firmware(platforms)] == ["ps2"]

    def test_a_file_missing_from_the_server_s_file_system_is_left_out(self):
        platforms = [
            _platform(1, "psx", None, _file(10, "gone.bin", 1, missing_from_fs=True), _file(11, "here.bin", 1)),
        ]

        assert [row["file_name"] for row in library_firmware(platforms)] == ["here.bin"]

    def test_a_file_whose_platform_id_names_no_listed_platform_is_left_out(self):
        assert library_firmware([_platform(1, "psx", None, _file(10, "orphan.bin", 99))]) == []

    def test_a_platform_without_firmware_or_slug_contributes_nothing(self):
        platforms = [
            {"id": 1, "slug": "psx", "fs_slug": "ps", "firmware": None},
            {"id": 2, "slug": "", "firmware": [_file(10, "x.bin", 2)]},
            {"id": 3, "fs_slug": "nes"},
        ]

        assert library_firmware(platforms) == []

    def test_the_server_s_fields_are_kept(self):
        rows = library_firmware([_platform(1, "psx", None, _file(10, "a.bin", 1, md5_hash="abc", file_size_bytes=3))])

        assert rows == [
            {
                "id": 10,
                "file_name": "a.bin",
                "platform_id": 1,
                "md5_hash": "abc",
                "file_size_bytes": 3,
                "platform_slug": "psx",
            }
        ]


class TestFolderPlatforms:
    def test_a_folder_named_otherwise_than_its_platform_maps_to_the_platform(self):
        assert folder_platforms([_platform(1, "psx", "ps"), _platform(2, "gba", "gba")]) == {"ps": "psx"}

    def test_a_folder_that_is_another_platform_s_slug_names_no_one_platform(self):
        assert folder_platforms([_platform(1, "psx", "ps"), _platform(2, "ps", "ps")]) == {}

    def test_a_folder_two_platforms_share_names_no_one_platform(self):
        assert folder_platforms([_platform(1, "neogeoaes", "neogeo"), _platform(2, "neogeomvs", "neogeo")]) == {}

    def test_a_platform_without_a_folder_name_maps_nothing(self):
        assert folder_platforms([{"id": 1, "slug": "psx"}, {"id": 2, "slug": "gba", "fs_slug": ""}]) == {}


class TestListedSlugs:
    def test_every_platform_s_slug_and_no_empty_one(self):
        assert listed_slugs([_platform(1, "psx", "ps"), {"id": 2, "slug": ""}, {"id": 3}]) == {"psx"}


class TestRefiledRecordPlatform:
    _ROWS = ({"id": 10, "file_name": "scph5501.bin", "platform_slug": "psx"},)
    _PLATFORMS = (_platform(1, "psx", "ps"), _platform(2, "arcade"), _platform(3, "neogeo"))

    def _target(self, platform_slug: str, file_name: str, firmware_id: int | None, rows=_ROWS) -> str | None:
        return refiled_record_platform(platform_slug, file_name, firmware_id, rows, self._PLATFORMS)

    def test_a_record_moves_to_the_platform_of_the_file_its_id_names(self):
        assert self._target("bios_from_user", "scph5501.bin", 10) == "psx"

    def test_an_id_that_now_names_another_file_does_not_move_the_record(self):
        assert self._target("bios_from_user", "other.bin", 10) is None

    def test_a_record_whose_file_the_library_no_longer_holds_moves_by_its_folder_name(self):
        assert self._target("ps", "scph5501.bin", 99) == "psx"

    def test_a_record_with_no_firmware_id_moves_by_its_folder_name(self):
        assert self._target("ps", "scph5501.bin", None) == "psx"

    def test_a_record_already_under_its_platform_stays(self):
        assert self._target("psx", "scph5501.bin", 10) is None

    def test_a_record_under_a_listed_platform_is_never_judged_again(self):
        """Its id now names a file of that name on another platform; it was filed by its platform all the same."""
        rows = ({"id": 7, "file_name": "neogeo.zip", "platform_slug": "neogeo"},)

        assert self._target("arcade", "neogeo.zip", 7, rows) is None

    def test_a_record_nothing_places_stays(self):
        assert self._target("bios_from_user", "gone.bin", None) is None


class TestOverrideMoves:
    def test_an_override_under_a_folder_name_moves_to_its_platform(self):
        assert override_moves({"ps": "SwanStation", "gba": "mGBA"}, {"ps": "psx"}) == {"ps": "psx"}

    def test_an_override_stays_where_its_platform_has_one_of_its_own(self):
        assert override_moves({"ps": "SwanStation", "psx": "Beetle PSX"}, {"ps": "psx"}) == {}

    def test_nothing_to_move_answers_nothing(self):
        assert override_moves({"psx": "SwanStation"}, {"ps": "psx"}) == {}

    def test_an_override_under_a_folder_no_one_platform_owns_stays(self):
        assert override_moves({"bios_from_user": "SwanStation"}, {}) == {}
