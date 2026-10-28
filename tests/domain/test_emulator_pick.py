"""Tests for domain/emulator_pick — a stored emulator pick and its source."""

from __future__ import annotations

import pytest

from domain.emulator_pick import StoredPick, pick_for, platform_pick, platform_pick_value


class TestPlatformPick:
    def test_a_stored_value_reads_back_as_its_pick(self):
        assert platform_pick(platform_pick_value("bsnes", "retrodeck")) == StoredPick("bsnes", "retrodeck")

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "bsnes",
            {"label": ""},
            {"label": 7, "source": "retrodeck"},
            {"label": "bsnes", "source": 1},
            {"label": "bsnes", "source": None},
            [],
        ],
    )
    def test_anything_else_is_no_pick(self, value):
        assert platform_pick(value) is None


class TestPickFor:
    """A pick applies while its source is the one games start through (#2222 D5)."""

    def test_a_pick_of_the_starting_source_applies(self):
        pick = StoredPick("bsnes", "retrodeck")
        assert pick_for(pick, "retrodeck") is pick

    def test_a_retrodeck_pick_while_retrodeck_is_switched_off_does_not_apply(self):
        assert pick_for(StoredPick("bsnes", "retrodeck"), "emudeck") is None
        assert pick_for(StoredPick("bsnes", "retrodeck"), None) is None

    def test_no_pick_is_no_pick(self):
        assert pick_for(None, "retrodeck") is None
