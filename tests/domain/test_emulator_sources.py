"""Tests for domain/emulator_sources.py — order, switches and the answering source."""

from __future__ import annotations

import pytest

from domain.emulator_sources import (
    ALL_SOURCES_SWITCHED_OFF,
    NO_SOURCE_DETECTED,
    ArrangedSource,
    answering_source,
    arrange_sources,
    move_source,
    no_answering_source_reason,
    switch_source,
)
from domain.refusal import DomainRefused

PROBE_ORDER = ("retrodeck", "emudeck", "bare_retroarch_flatpak", "bare_retroarch_native")


def _kinds(sources: tuple[ArrangedSource, ...]) -> list[str]:
    return [source.kind for source in sources]


class TestArrangeSources:
    def test_with_nothing_stored_the_order_is_the_probe_order(self):
        sources = arrange_sources(detected=PROBE_ORDER, stored_order=(), switched_off=())
        assert _kinds(sources) == list(PROBE_ORDER)

    def test_a_stored_order_ranks_the_kinds_it_names(self):
        sources = arrange_sources(
            detected=("retrodeck", "emudeck"), stored_order=("emudeck", "retrodeck"), switched_off=()
        )
        assert _kinds(sources) == ["emudeck", "retrodeck"]

    def test_a_kind_seen_for_the_first_time_joins_at_the_end_switched_on(self):
        sources = arrange_sources(
            detected=("retrodeck", "emudeck", "bare_retroarch_native"),
            stored_order=("emudeck", "retrodeck"),
            switched_off=(),
        )
        assert _kinds(sources) == ["emudeck", "retrodeck", "bare_retroarch_native"]
        assert sources[-1].enabled is True

    def test_a_kind_unknown_to_tender_is_listed_like_any_other(self):
        sources = arrange_sources(detected=("retrodeck", "standalone_x"), stored_order=(), switched_off=())
        assert _kinds(sources) == ["retrodeck", "standalone_x"]

    def test_a_stored_kind_that_is_not_detected_is_not_listed(self):
        sources = arrange_sources(detected=("emudeck",), stored_order=("retrodeck", "emudeck"), switched_off=())
        assert _kinds(sources) == ["emudeck"]

    def test_a_duplicate_in_the_stored_order_lists_the_kind_once(self):
        sources = arrange_sources(
            detected=("retrodeck", "emudeck"), stored_order=("emudeck", "emudeck", "retrodeck"), switched_off=()
        )
        assert _kinds(sources) == ["emudeck", "retrodeck"]

    def test_a_switched_off_kind_is_listed_disabled(self):
        sources = arrange_sources(detected=("retrodeck", "emudeck"), stored_order=(), switched_off=("emudeck",))
        assert [(source.kind, source.enabled) for source in sources] == [("retrodeck", True), ("emudeck", False)]

    def test_only_retrodeck_starts_games(self):
        sources = arrange_sources(detected=PROBE_ORDER, stored_order=(), switched_off=())
        assert [source.starts_games for source in sources] == [True, False, False, False]

    def test_nothing_detected_lists_nothing(self):
        assert arrange_sources(detected=(), stored_order=("retrodeck",), switched_off=()) == ()


class TestAnsweringSource:
    def test_retrodeck_answers_even_when_another_source_is_first_in_the_order(self):
        sources = arrange_sources(
            detected=("retrodeck", "emudeck"), stored_order=("emudeck", "retrodeck"), switched_off=()
        )
        chosen = answering_source(sources)
        assert chosen is not None
        assert chosen.kind == "retrodeck"
        assert chosen.starts_games is True

    def test_without_retrodeck_the_first_enabled_source_answers_and_cannot_start_games(self):
        sources = arrange_sources(detected=("emudeck",), stored_order=(), switched_off=())
        chosen = answering_source(sources)
        assert chosen is not None
        assert chosen.kind == "emudeck"
        assert chosen.starts_games is False

    def test_retrodeck_switched_off_hands_the_answer_to_the_first_enabled_source(self):
        sources = arrange_sources(
            detected=("retrodeck", "emudeck", "bare_retroarch_native"),
            stored_order=("bare_retroarch_native", "emudeck", "retrodeck"),
            switched_off=("retrodeck",),
        )
        chosen = answering_source(sources)
        assert chosen is not None
        assert chosen.kind == "bare_retroarch_native"

    def test_a_switched_off_source_is_skipped(self):
        sources = arrange_sources(
            detected=("emudeck", "bare_retroarch_native"), stored_order=(), switched_off=("emudeck",)
        )
        chosen = answering_source(sources)
        assert chosen is not None
        assert chosen.kind == "bare_retroarch_native"

    def test_nothing_detected_answers_none_with_its_reason(self):
        assert answering_source(()) is None
        assert no_answering_source_reason(()) == NO_SOURCE_DETECTED

    def test_everything_switched_off_answers_none_with_its_reason(self):
        sources = arrange_sources(
            detected=("retrodeck", "emudeck"), stored_order=(), switched_off=("retrodeck", "emudeck")
        )
        assert answering_source(sources) is None
        assert no_answering_source_reason(sources) == ALL_SOURCES_SWITCHED_OFF


class TestMoveSource:
    def test_moving_down_swaps_with_the_next_source(self):
        order = move_source(kind="retrodeck", offset=1, detected=("retrodeck", "emudeck"), stored_order=())
        assert order == ("emudeck", "retrodeck")

    def test_moving_up_swaps_with_the_previous_source(self):
        order = move_source(kind="bare_retroarch_native", offset=-1, detected=PROBE_ORDER, stored_order=())
        assert order == ("retrodeck", "emudeck", "bare_retroarch_native", "bare_retroarch_flatpak")

    def test_a_move_skips_a_stored_kind_that_is_not_detected_and_keeps_its_place(self):
        order = move_source(
            kind="emudeck",
            offset=-1,
            detected=("retrodeck", "emudeck"),
            stored_order=("retrodeck", "bare_retroarch_native", "emudeck"),
        )
        assert order == ("emudeck", "bare_retroarch_native", "retrodeck")

    def test_a_move_past_the_top_is_refused(self):
        with pytest.raises(DomainRefused) as refusal:
            move_source(kind="retrodeck", offset=-1, detected=("retrodeck", "emudeck"), stored_order=())
        assert refusal.value.reason == "cannot_move"

    def test_a_move_past_the_bottom_is_refused(self):
        with pytest.raises(DomainRefused) as refusal:
            move_source(kind="emudeck", offset=1, detected=("retrodeck", "emudeck"), stored_order=())
        assert refusal.value.reason == "cannot_move"

    def test_moving_a_kind_that_is_not_detected_is_refused(self):
        with pytest.raises(DomainRefused) as refusal:
            move_source(kind="emudeck", offset=1, detected=("retrodeck",), stored_order=("emudeck",))
        assert refusal.value.reason == "unknown_source"

    @pytest.mark.parametrize("offset", [0, 2, -2])
    def test_an_offset_other_than_one_place_is_a_programming_error(self, offset):
        with pytest.raises(ValueError, match="one place at a time"):
            move_source(kind="retrodeck", offset=offset, detected=("retrodeck", "emudeck"), stored_order=())


class TestSwitchSource:
    def test_switching_off_adds_the_kind(self):
        assert switch_source(kind="emudeck", enabled=False, detected=("emudeck",), switched_off=()) == ("emudeck",)

    def test_switching_on_removes_the_kind(self):
        assert switch_source(
            kind="emudeck", enabled=True, detected=("emudeck",), switched_off=("retrodeck", "emudeck")
        ) == ("retrodeck",)

    def test_switching_off_twice_lists_the_kind_once(self):
        assert switch_source(kind="emudeck", enabled=False, detected=("emudeck",), switched_off=("emudeck",)) == (
            "emudeck",
        )

    def test_switching_a_kind_that_is_not_detected_is_refused(self):
        with pytest.raises(DomainRefused) as refusal:
            switch_source(kind="emudeck", enabled=False, detected=("retrodeck",), switched_off=())
        assert refusal.value.reason == "unknown_source"
