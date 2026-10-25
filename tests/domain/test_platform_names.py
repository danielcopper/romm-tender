"""Tests for the platform-name cache decoder and the names it is written from."""

from __future__ import annotations

import pytest

from domain.platform_names import decode_platform_names, listed_platform_names


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
    def test_every_listed_platform_is_named_by_its_name_keyed_by_its_slug(self):
        listed = [{"slug": "n64", "name": "Nintendo 64"}, {"slug": "snes", "name": "Super Nintendo"}]
        assert listed_platform_names(listed) == {"n64": "Nintendo 64", "snes": "Super Nintendo"}

    def test_a_custom_name_set_in_romm_is_not_the_name(self):
        listed = [{"slug": "n64", "name": "Nintendo 64", "custom_name": "N64 Custom", "display_name": "N64 Custom"}]
        assert listed_platform_names(listed) == {"n64": "Nintendo 64"}

    @pytest.mark.parametrize(
        "platform",
        [
            {"name": "Nintendo 64"},
            {"slug": "", "name": "Nintendo 64"},
            {"slug": "n64"},
            {"slug": "n64", "name": "  "},
            {"slug": "n64", "name": None, "display_name": "N64 Custom"},
            {"slug": 64, "name": "Nintendo 64"},
        ],
        ids=["no-slug", "empty-slug", "no-name", "blank-name", "display-name-only", "slug-not-text"],
    )
    def test_a_platform_without_a_slug_or_a_name_is_left_out(self, platform):
        assert listed_platform_names([platform]) == {}
