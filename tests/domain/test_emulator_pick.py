"""Tests for domain/emulator_pick — a stored emulator pick and its source."""

from __future__ import annotations

import pytest

from domain.emulator_pick import StoredPick, platform_pick, platform_pick_value


class TestPlatformPick:
    def test_a_stored_value_reads_back_as_its_pick(self):
        assert platform_pick(platform_pick_value("bsnes", "retrodeck")) == StoredPick("bsnes", "retrodeck")

    def test_a_pick_without_a_recorded_source_reads_with_none(self):
        assert platform_pick({"label": "bsnes", "source": None}) == StoredPick("bsnes", None)

    @pytest.mark.parametrize(
        "value",
        [None, "bsnes", {"label": ""}, {"label": 7, "source": "retrodeck"}, {"label": "bsnes", "source": 1}, []],
    )
    def test_anything_else_is_no_pick(self, value):
        assert platform_pick(value) is None
