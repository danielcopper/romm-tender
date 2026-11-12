"""Tests for domain/emulator_pick — a stored emulator pick and its source."""

from __future__ import annotations

import pytest

from domain.emulator_pick import StoredPick, game_platform_pick, pick_for, platform_pick, platform_pick_value


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


class TestGamePlatformPick:
    """A game's per-platform pick (#2452 D15)."""

    _BSNES = StoredPick(label="bsnes", source="retrodeck")

    def _pick(self, folder, picks, kept=(), asked=None):
        def kept_folders():
            if asked is not None:
                asked.append("kept")
            return frozenset(kept)

        return game_platform_pick(folder, "snes", picks.get, kept_folders)

    def test_a_game_with_a_folder_takes_that_platform_s_pick_alone(self):
        asked: list[str] = []

        assert self._pick("snes-hacks", {"snes": self._BSNES}, ("snes",), asked) is None
        assert asked == []

    def test_a_game_without_one_takes_the_pick_under_its_slug_without_asking_what_is_kept(self):
        asked: list[str] = []

        assert self._pick(None, {"snes": self._BSNES}, ("snes-hacks",), asked) == self._BSNES
        assert asked == []

    def test_then_the_pick_of_the_one_platform_kept_for_its_slug(self):
        assert self._pick(None, {"snes-hacks": self._BSNES}, ("snes-hacks",)) == self._BSNES

    def test_none_where_several_platforms_share_the_slug_or_none_is_kept(self):
        assert self._pick(None, {"snes-hacks": self._BSNES}, ("snes", "snes-hacks")) is None
        assert self._pick(None, {"snes-hacks": self._BSNES}, ()) is None
