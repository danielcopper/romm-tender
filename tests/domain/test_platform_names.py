"""Tests for the platform-name cache decoder and the names it is written from."""

from __future__ import annotations

import pytest

from domain.platform_names import (
    decode_platform_names,
    listed_platform_folders,
    listed_platform_names,
    platform_display_name,
)


class TestDecodePlatformNames:
    def test_decodes_valid_object(self):
        raw = '{"snes": "Super Nintendo", "n64": "Nintendo 64"}'
        assert decode_platform_names(raw) == {"snes": "Super Nintendo", "n64": "Nintendo 64"}

    def test_empty_object_round_trips(self):
        assert decode_platform_names("{}") == {}

    @pytest.mark.parametrize("raw", [None, ""])
    def test_absent_value_yields_empty(self, raw):
        assert decode_platform_names(raw) == {}

    def test_invalid_json_yields_empty(self):
        assert decode_platform_names("not json at all {") == {}

    @pytest.mark.parametrize("raw", ['"a json string, not a dict"', "[1, 2, 3]", "42", "true", "null"])
    def test_non_object_json_yields_empty(self, raw):
        # A corrupt cache must degrade to the slug, never surface a non-dict.
        assert decode_platform_names(raw) == {}


class TestListedPlatformNames:
    def test_every_listed_platform_is_named_by_its_name_keyed_by_its_folder(self):
        listed = [
            {"slug": "n64", "fs_slug": "n64", "name": "Nintendo 64"},
            {"slug": "snes", "fs_slug": "snes", "name": "Super Nintendo"},
        ]
        assert listed_platform_names(listed) == {"n64": "Nintendo 64", "snes": "Super Nintendo"}

    def test_a_custom_name_set_in_romm_is_not_the_name(self):
        listed = [
            {
                "slug": "n64",
                "fs_slug": "n64",
                "name": "Nintendo 64",
                "custom_name": "N64 Custom",
                "display_name": "N64 Custom",
            }
        ]
        assert listed_platform_names(listed) == {"n64": "Nintendo 64"}

    @pytest.mark.parametrize(
        "platform",
        [
            {"slug": "n64", "name": "Nintendo 64"},
            {"slug": "n64", "fs_slug": "", "name": "Nintendo 64"},
            {"fs_slug": "n64"},
            {"fs_slug": "n64", "name": "  "},
            {"fs_slug": "n64", "name": None, "display_name": "N64 Custom"},
            {"fs_slug": 64, "name": "Nintendo 64"},
        ],
        ids=["no-folder", "empty-folder", "no-name", "blank-name", "display-name-only", "folder-not-text"],
    )
    def test_a_platform_without_a_folder_or_a_name_is_left_out(self, platform):
        assert listed_platform_names([platform]) == {}


class TestPlatformDisplayName:
    def test_a_cached_platform_is_named_by_its_cached_name(self):
        assert platform_display_name('{"dc": "Sega Dreamcast"}', "dc", "dc") == "Sega Dreamcast"

    def test_a_platform_the_cache_does_not_hold_is_named_by_its_slug(self):
        assert platform_display_name('{"dc": "Sega Dreamcast"}', "ps3", "ps3") == "ps3"

    def test_no_cache_names_it_by_its_slug(self):
        assert platform_display_name(None, "dc", "dc") == "dc"

    def test_no_slug_names_nothing(self):
        assert platform_display_name('{"dc": "Sega Dreamcast"}', None, None) == ""


class TestTwoPlatformsSharingAName:
    """#2452 D2: of two listed platforms sharing a name, one whose folder is not its slug carries its folder."""

    def test_the_folder_that_is_the_slug_keeps_the_plain_name(self):
        listed = [
            {"slug": "snes", "fs_slug": "snes", "name": "Super Nintendo"},
            {"slug": "snes", "fs_slug": "snes-hacks", "name": "Super Nintendo"},
        ]
        assert listed_platform_names(listed) == {
            "snes": "Super Nintendo",
            "snes-hacks": "Super Nintendo (snes-hacks)",
        }

    def test_where_neither_folder_is_the_slug_both_carry_their_folder(self):
        listed = [
            {"slug": "ngc", "fs_slug": "gc", "name": "Nintendo GameCube"},
            {"slug": "ngc", "fs_slug": "gamecube", "name": "Nintendo GameCube"},
        ]
        assert listed_platform_names(listed) == {
            "gc": "Nintendo GameCube (gc)",
            "gamecube": "Nintendo GameCube (gamecube)",
        }

    def test_a_folder_that_is_not_its_slug_keeps_the_plain_name_while_no_other_platform_shares_it(self):
        listed = [{"slug": "ngc", "fs_slug": "gc", "name": "Nintendo GameCube"}]
        assert listed_platform_names(listed) == {"gc": "Nintendo GameCube"}


class TestListedPlatformFolders:
    def test_every_listed_folder_and_no_platform_without_one(self):
        listed = [{"fs_slug": "gc"}, {"fs_slug": "gamecube"}, {"slug": "n64"}, {"fs_slug": ""}]
        assert listed_platform_folders(listed) == frozenset({"gc", "gamecube"})


class TestAGameWithoutAFolderIsNamedBySlug:
    def test_no_folder_names_it_by_its_slug_whatever_the_cache_holds(self):
        assert platform_display_name('{"ngc": "Folder Named ngc"}', None, "ngc") == "ngc"

    def test_a_folder_names_it_by_the_folder_s_cached_name(self):
        assert platform_display_name('{"gc": "Nintendo GameCube (gc)"}', "gc", "ngc") == "Nintendo GameCube (gc)"
