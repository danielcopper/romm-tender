"""Tests for domain/firmware_platforms.py — where Tender's older per-platform entries belong (#2452 D6, D7, D18)."""

from __future__ import annotations

from typing import Any

from domain.bios_file import BiosFile
from domain.firmware_platforms import folders_by_slug, pick_moves, records_of_platform, refiled_record_folder
from domain.platform_system import KeptPlatform, encode_platform_ids, kept_folders


def _platform(platform_id: int, slug: str, fs_slug: str | None) -> dict[str, Any]:
    return {"id": platform_id, "slug": slug, "fs_slug": fs_slug, "firmware": []}


# Two RomM platforms share the slug ``gamecube``; ``psx`` is kept in a folder of another name.
_PLATFORMS = (
    _platform(1, "psx", "ps"),
    _platform(2, "gamecube", "gc"),
    _platform(3, "gamecube", "ngc"),
    _platform(4, "snes", "snes"),
)


class TestFoldersBySlug:
    def test_every_platform_s_folder_under_its_slug(self):
        assert folders_by_slug(_PLATFORMS) == {
            "psx": frozenset({"ps"}),
            "gamecube": frozenset({"gc", "ngc"}),
            "snes": frozenset({"snes"}),
        }

    def test_a_platform_without_a_slug_or_a_folder_is_left_out(self):
        platforms = [{"id": 1, "slug": "", "fs_slug": "x"}, {"id": 2, "slug": "y", "fs_slug": None}, {"id": 3}]

        assert folders_by_slug(platforms) == {}


class TestRefiledRecordFolder:
    _ROWS = ({"id": 10, "file_name": "scph5501.bin", "platform_folder": "ps"},)

    def _target(self, key: str, file_name: str, firmware_id: int | None) -> str | None:
        return refiled_record_folder(key, file_name, firmware_id, self._ROWS, _PLATFORMS)

    def test_a_record_under_a_slug_moves_to_the_one_folder_with_that_slug(self):
        assert self._target("psx", "scph5501.bin", None) == "ps"

    def test_a_record_moves_to_the_folder_of_the_file_its_id_names(self):
        assert self._target("bios_from_user", "scph5501.bin", 10) == "ps"

    def test_an_id_that_now_names_another_file_moves_nothing_by_it(self):
        assert self._target("bios_from_user", "other.bin", 10) is None

    def test_a_record_under_a_listed_folder_stays(self):
        assert self._target("ps", "scph5501.bin", 10) is None

    def test_a_record_under_a_slug_several_folders_share_stays(self):
        assert self._target("gamecube", "IPL.bin", None) is None

    def test_a_record_nothing_places_stays(self):
        assert self._target("bios_from_user", "gone.bin", None) is None


class TestPickMoves:
    def test_a_pick_under_a_slug_goes_to_every_folder_of_that_slug(self):
        assert pick_moves({"gamecube": {"core": "Dolphin"}}, _PLATFORMS) == {"gamecube": ("gc", "ngc")}

    def test_a_folder_with_a_pick_of_its_own_keeps_it(self):
        moves = pick_moves({"gamecube": {"core": "Dolphin"}, "ngc": {"core": "Other"}}, _PLATFORMS)

        assert moves == {"gamecube": ("gc",)}

    def test_a_pick_whose_folders_all_have_their_own_goes_to_none_and_the_key_goes(self):
        moves = pick_moves({"gamecube": {}, "gc": {}, "ngc": {}}, _PLATFORMS)

        assert moves == {"gamecube": ()}

    def test_a_pick_under_a_listed_folder_stays_though_it_is_also_a_slug(self):
        """D18: ``snes`` is both a slug and a listed folder; the pick stays where it is."""
        platforms = (*_PLATFORMS, _platform(5, "snes", "snes-hacks"))

        assert pick_moves({"snes": {"core": "Snes9x"}}, platforms) == {}

    def test_a_pick_under_a_key_no_listed_platform_has_as_slug_stays(self):
        assert pick_moves({"bios_from_user": {}}, _PLATFORMS) == {}


def _record(folder: str, file_name: str = "IPL.bin") -> BiosFile:
    return BiosFile(
        platform_folder=folder, file_name=file_name, file_path=f"/bios/{file_name}", downloaded_at="2026-01-01T00:00:00"
    )


class TestRecordsOfPlatform:
    def test_a_platform_s_own_records_are_its_own(self):
        records = [_record("gc"), _record("ngc", "other.bin")]

        assert records_of_platform(records, "gc", "gamecube", frozenset({"gc", "ngc"})) == [records[0]]

    def test_a_record_under_a_shared_slug_is_offered_on_each_of_its_platforms(self):
        records = [_record("gamecube")]
        known = frozenset({"gc", "ngc"})

        assert records_of_platform(records, "gc", "gamecube", known) == records
        assert records_of_platform(records, "ngc", "gamecube", known) == records

    def test_a_slug_that_is_also_a_known_folder_is_that_platform_s_alone(self):
        records = [_record("snes")]
        known = frozenset({"snes", "snes-hacks"})

        assert records_of_platform(records, "snes-hacks", "snes", known) == []
        assert records_of_platform(records, "snes", "snes", known) == records


class TestKeptFolders:
    def test_every_kept_platform_s_folder(self):
        kept = {
            "gc": KeptPlatform(slug="gamecube", fs_slug="gc", platform_id=2, name="GameCube"),
            "ngc": KeptPlatform(slug="gamecube", fs_slug="ngc", platform_id=3, name="GameCube"),
        }

        assert kept_folders(encode_platform_ids(kept)) == frozenset({"gc", "ngc"})

    def test_nothing_kept_is_no_folder(self):
        assert kept_folders(None) == frozenset()
        assert kept_folders("not json") == frozenset()
