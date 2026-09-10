"""Tests for domain.version."""

from __future__ import annotations

from typing import Any

import pytest

from domain.version import is_newer_version, meets_min_version

MIN = (4, 8, 1)


class TestMeetsMinVersion:
    def test_exact_minimum(self):
        assert meets_min_version("4.8.1", MIN) is True

    def test_patch_above(self):
        assert meets_min_version("4.8.2", MIN) is True

    def test_minor_above(self):
        assert meets_min_version("4.9.0", MIN) is True

    def test_major_above(self):
        assert meets_min_version("5.0.0", MIN) is True

    def test_patch_below(self):
        assert meets_min_version("4.8.0", MIN) is False

    def test_minor_below(self):
        assert meets_min_version("4.7.0", MIN) is False

    def test_major_below(self):
        assert meets_min_version("3.99.99", MIN) is False

    def test_partial_two_part_version_below(self):
        # (4, 8) < (4, 8, 1) in tuple comparison
        assert meets_min_version("4.8", MIN) is False

    def test_four_part_version_above(self):
        # (4, 8, 1, 1) >= (4, 8, 1)
        assert meets_min_version("4.8.1.1", MIN) is True

    def test_garbage_returns_false(self):
        assert meets_min_version("abc", MIN) is False

    def test_empty_string_returns_false(self):
        assert meets_min_version("", MIN) is False

    def test_none_returns_false(self):
        assert meets_min_version(None, MIN) is False

    @pytest.mark.parametrize("bad_value", [4.9, 5, True, [4, 9, 0], {"version": "4.9.0"}])
    def test_non_string_input_returns_false(self, bad_value: Any):
        # SYSTEM.VERSION is server-controlled: a truthy non-str (e.g. numeric 4.9)
        # must be rejected by the isinstance guard, never raise TypeError.
        assert meets_min_version(bad_value, MIN) is False

    def test_development_returns_false(self):
        assert meets_min_version("development", MIN) is False

    def test_partly_numeric_returns_false(self):
        assert meets_min_version("4.8.x", MIN) is False

    def test_alpha_with_number_above_minimum(self):
        assert meets_min_version("5.0.0-alpha.1", MIN) is True

    def test_alpha_without_number_above_minimum(self):
        assert meets_min_version("5.0.0-alpha", MIN) is True

    def test_beta_at_exact_floor_rejected(self):
        assert meets_min_version("4.8.1-beta.3", MIN) is False

    def test_alpha_at_exact_floor_rejected(self):
        assert meets_min_version("4.8.1-alpha.1", MIN) is False

    def test_beta_without_number_at_exact_floor_rejected(self):
        assert meets_min_version("4.8.1-beta", MIN) is False

    def test_higher_core_prerelease_passes(self):
        assert meets_min_version("4.8.2-beta", MIN) is True

    def test_prerelease_tag_case_insensitive(self):
        assert meets_min_version("4.8.2-BETA", MIN) is True
        assert meets_min_version("5.0.0-ALPHA.1", MIN) is True

    def test_alpha_below_minimum_fails(self):
        assert meets_min_version("4.8.0-alpha.99", MIN) is False

    def test_prerelease_missing_tag_number_only(self):
        assert meets_min_version("5.0.0-alpha.", MIN) is False


class TestIsNewerVersion:
    def test_a_later_version_is_newer(self):
        assert is_newer_version("0.33.0", "0.32.0") is True

    def test_the_running_version_is_not_newer_than_itself(self):
        """The comparison is strictly "later than" — equality never offers an update."""
        assert is_newer_version("0.32.0", "0.32.0") is False

    def test_an_earlier_version_is_not_newer(self):
        assert is_newer_version("0.31.1", "0.32.0") is False

    def test_a_later_major_is_newer(self):
        assert is_newer_version("1.0.0", "0.32.0") is True

    def test_a_prerelease_of_the_running_version_is_not_newer(self):
        assert is_newer_version("0.32.0-beta", "0.32.0") is False

    def test_a_prerelease_of_a_later_version_is_newer(self):
        assert is_newer_version("0.33.0-beta", "0.32.0") is True

    def test_the_release_of_the_running_prerelease_is_newer(self):
        assert is_newer_version("0.32.0", "0.32.0-beta") is True

    @pytest.mark.parametrize(
        ("candidate", "current"),
        [
            (None, "0.32.0"),
            ("0.33.0", None),
            ("", "0.32.0"),
            ("tender-v0.33.0", "0.32.0"),
            ("development", "0.32.0"),
            ("0.33.0", "development"),
        ],
    )
    def test_an_unreadable_version_says_nothing(self, candidate: Any, current: Any):
        """False is the silent direction: the caller offers an update on a True."""
        assert is_newer_version(candidate, current) is False
