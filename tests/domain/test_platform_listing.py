"""Tests for domain/platform_listing.py — one entry of RomM's platform listing, read field by field."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _romm_platforms import LISTED_GAMECUBE

from domain.platform_listing import (
    PlatformEntry,
    folder_without_an_id,
    listed_number,
    listed_text,
    platform_entries,
    platform_entry_of,
)

_FIRMWARE = {"id": 7, "file_name": "IPL.bin", "platform_id": 12}


class TestRommsEntry:
    def test_every_field_of_rom_m_5_4_1_s_entry(self):
        entry = platform_entry_of({**LISTED_GAMECUBE, "firmware": [_FIRMWARE]})

        assert entry == PlatformEntry(
            fs_slug="gc",
            slug="ngc",
            platform_id=12,
            name="Nintendo GameCube",
            rom_count=40,
            is_identified=True,
            ids={
                "fs_slug": "gc",
                "igdb_slug": "ngc",
                "moby_slug": "gamecube",
                "hltb_slug": None,
                "libretro_slug": "Nintendo - GameCube",
                "igdb_id": 21,
                "sgdb_id": None,
                "moby_id": 14,
                "launchbox_id": 30,
                "ss_id": 13,
                "ra_id": 16,
                "hasheous_id": None,
                "tgdb_id": 2,
                "flashpoint_id": None,
                "family_slug": "nintendo",
            },
            firmware=(_FIRMWARE,),
        )

    def test_an_entry_without_an_fs_slug_has_no_folder_and_keeps_every_other_field(self):
        entry = platform_entry_of({k: v for k, v in LISTED_GAMECUBE.items() if k != "fs_slug"})

        assert entry.fs_slug is None
        assert (entry.slug, entry.platform_id, entry.name, entry.rom_count) == ("ngc", 12, "Nintendo GameCube", 40)

    @pytest.mark.parametrize("name", ["", "   ", None, 7], ids=["empty", "blank", "null", "not-text"])
    def test_a_blank_name_is_none(self, name):
        assert platform_entry_of({**LISTED_GAMECUBE, "name": name}).name is None

    def test_a_display_name_without_a_name_names_nothing(self):
        platform = {k: v for k, v in LISTED_GAMECUBE.items() if k != "name"}

        assert platform["display_name"] == "GameCube"
        assert platform_entry_of(platform).name is None

    def test_a_custom_name_is_not_the_name(self):
        assert platform_entry_of(LISTED_GAMECUBE).name == "Nintendo GameCube"

    def test_a_slug_that_is_only_whitespace_is_no_slug(self):
        assert platform_entry_of({**LISTED_GAMECUBE, "slug": "  "}).slug is None

    def test_a_numeric_id_sent_as_a_string_is_that_number(self):
        assert platform_entry_of({**LISTED_GAMECUBE, "id": "12"}).platform_id == 12

    @pytest.mark.parametrize("platform_id", [None, True, "abc", "-4", 1.5, "12.0"])
    def test_an_id_that_is_no_whole_number_is_none(self, platform_id):
        assert platform_entry_of({**LISTED_GAMECUBE, "id": platform_id}).platform_id is None

    @pytest.mark.parametrize("rom_count", [None, "many", 4.5, False, [40], -1, "-1"])
    def test_a_rom_count_that_is_not_a_number_is_unknown(self, rom_count):
        assert platform_entry_of({**LISTED_GAMECUBE, "rom_count": rom_count}).rom_count is None

    def test_an_absent_rom_count_is_unknown(self):
        platform = {k: v for k, v in LISTED_GAMECUBE.items() if k != "rom_count"}

        assert platform_entry_of(platform).rom_count is None

    @pytest.mark.parametrize("identified", [None, "yes", 1])
    def test_an_is_identified_that_is_not_a_boolean_is_none(self, identified):
        assert platform_entry_of({**LISTED_GAMECUBE, "is_identified": identified}).is_identified is None

    @pytest.mark.parametrize(
        ("firmware", "read"),
        [(None, ()), ([], ()), ([_FIRMWARE, "IPL.bin"], (_FIRMWARE, "IPL.bin"))],
        ids=["null", "empty", "list"],
    )
    def test_firmware_is_the_list_as_sent(self, firmware, read):
        assert platform_entry_of({**LISTED_GAMECUBE, "firmware": firmware}).firmware == read

    def test_no_firmware_sent_is_none_listed(self):
        platform = {k: v for k, v in LISTED_GAMECUBE.items() if k != "firmware"}

        assert platform_entry_of(platform).firmware == ()

    @pytest.mark.parametrize("firmware", ["IPL.bin", {"id": 7}, 7, ""], ids=["text", "object", "number", "empty-text"])
    def test_firmware_that_is_no_list_is_unreadable_not_empty(self, firmware):
        """Read as empty it would say the platform needs no firmware (#1693)."""
        assert platform_entry_of({**LISTED_GAMECUBE, "firmware": firmware}).firmware is None


class TestPlatformEntries:
    def test_every_entry_in_the_listing_s_order(self):
        listed = [{"fs_slug": "gc", "id": 12}, {"slug": "n64", "id": "4"}]

        assert [(e.fs_slug, e.slug, e.platform_id) for e in platform_entries(listed)] == [
            ("gc", None, 12),
            (None, "n64", 4),
        ]


class TestFolderWithoutAnId:
    """#2761 D4: a listed entry with a folder but no usable id makes the listing unreadable."""

    @pytest.mark.parametrize("platform_id", [None, True, "abc", 1.5])
    def test_the_folder_of_an_entry_without_a_usable_id(self, platform_id):
        listed = [LISTED_GAMECUBE, {"fs_slug": "n64", "id": platform_id}]

        assert folder_without_an_id(listed) == "n64"

    def test_an_entry_without_a_folder_or_an_id_before_it_is_passed_over(self):
        assert folder_without_an_id([{"slug": "x"}, {"fs_slug": "n64"}]) == "n64"

    def test_an_entry_missing_its_id_names_its_folder(self):
        assert folder_without_an_id([{"fs_slug": "n64", "name": "Nintendo 64"}]) == "n64"

    def test_an_entry_without_a_folder_needs_no_id(self):
        assert folder_without_an_id([LISTED_GAMECUBE, {"slug": "n64"}, {"fs_slug": "  "}]) is None

    def test_an_item_that_is_no_object_has_no_folder(self):
        assert folder_without_an_id(["n64", None, 7]) is None

    def test_a_listing_whose_every_folder_has_an_id_has_none(self):
        assert folder_without_an_id([LISTED_GAMECUBE, {"fs_slug": "n64", "id": "4"}]) is None
        assert folder_without_an_id([]) is None


class TestFieldChecks:
    @pytest.mark.parametrize(("value", "read"), [("gc", "gc"), (" gc ", " gc "), ("", None), ("\t", None), (7, None)])
    def test_listed_text(self, value, read):
        assert listed_text(value) == read

    @pytest.mark.parametrize(
        ("value", "read"),
        [(12, 12), (0, 0), ("12", 12), ("", None), ("\u0661\u0662", None), (True, None), (12.0, None)],
    )
    def test_listed_number(self, value, read):
        assert listed_number(value) == read


_BACKEND = Path(__file__).resolve().parents[2] / "backend"
_DISPLAY_NAMES = frozenset({"display_name", "platform_display_name"})


def _display_name_literals(source: str) -> list[int]:
    """The line of every string literal in *source* that is one of RomM's display-name fields."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _DISPLAY_NAMES
    ]


class TestNoModuleReadsRommsDisplayName:
    """#2564 D2: no backend module takes RomM's ``display_name`` or ``platform_display_name`` for a platform's name.

    Read from the syntax tree, so a docstring or comment naming the field is
    prose and passes; a key spelled as a string literal anywhere is a read.
    """

    def test_no_backend_module_spells_either_field(self):
        found = [
            f"{path.relative_to(_BACKEND)}:{line}"
            for path in sorted(_BACKEND.rglob("*.py"))
            if not path.is_relative_to(_BACKEND / "_vendor")
            for line in _display_name_literals(path.read_text(encoding="utf-8"))
        ]

        assert found == []

    @pytest.mark.parametrize(
        "source",
        ['name = rom.get("platform_display_name")\n', 'name = p["display_name"]\n'],
        ids=["a get", "a subscript"],
    )
    def test_a_read_is_found(self, source):
        assert _display_name_literals(source) == [1]

    def test_a_docstring_naming_the_field_is_no_read(self):
        assert _display_name_literals('"""Never RomM\'s ``display_name``."""\n') == []
