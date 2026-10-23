"""Tests for domain.artwork_keep_current — the seven-day window and the slots a check covers."""

from __future__ import annotations

import pytest

from domain.artwork_keep_current import KEEP_CURRENT_SECONDS, is_check_due, slots_kept_current
from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_view import SlotSource, SlotState, SlotView

_NOW = 1_700_000_000.0


class TestIsCheckDue:
    def test_a_game_never_checked_is_due(self):
        assert is_check_due(None, _NOW) is True

    def test_a_game_checked_inside_the_seven_days_is_not_due(self):
        assert is_check_due(_NOW - KEEP_CURRENT_SECONDS + 1, _NOW) is False

    def test_a_game_checked_just_now_is_not_due(self):
        assert is_check_due(_NOW, _NOW) is False

    def test_a_game_checked_seven_days_ago_is_due(self):
        assert is_check_due(_NOW - KEEP_CURRENT_SECONDS, _NOW) is True

    def test_the_window_is_seven_days(self):
        assert KEEP_CURRENT_SECONDS == 7 * 24 * 60 * 60

    def test_a_check_recorded_in_the_future_is_due(self):
        assert is_check_due(_NOW + 60, _NOW) is True


def _view(slot: ArtworkSlot, state: SlotState, source: SlotSource, image_id: int | None = 5) -> SlotView:
    return SlotView(slot, state, source, image_id)


class TestSlotsKeptCurrent:
    @pytest.mark.parametrize("slot", list(ArtworkSlot))
    def test_an_automatic_slot_showing_tenders_image_is_checked(self, slot):
        view = _view(slot, SlotState.AUTOMATIC, SlotSource.TENDER)

        assert slots_kept_current([view]) == [view]

    def test_a_tender_image_whose_id_is_unknown_is_checked(self):
        view = _view(ArtworkSlot.HERO, SlotState.AUTOMATIC, SlotSource.TENDER, None)

        assert slots_kept_current([view]) == [view]

    @pytest.mark.parametrize(
        ("state", "source"),
        [
            (SlotState.CHOSEN, SlotSource.CHOSEN),
            (SlotState.SET_IN_STEAM, SlotSource.STEAM),
            (SlotState.AUTOMATIC, SlotSource.ROMM),
            (SlotState.AUTOMATIC, SlotSource.EMPTY),
            (SlotState.AUTOMATIC, SlotSource.NONE),
            (SlotState.AUTOMATIC, SlotSource.NEEDS_GAME),
        ],
        ids=["chosen", "set-in-steam", "romms-cover", "empty", "nothing-on-steamgriddb", "needs-a-game"],
    )
    def test_every_other_slot_is_left_alone(self, state, source):
        assert slots_kept_current([_view(ArtworkSlot.LOGO, state, source)]) == []

    def test_the_order_of_the_views_is_kept(self):
        logo = _view(ArtworkSlot.LOGO, SlotState.AUTOMATIC, SlotSource.TENDER)
        hero = _view(ArtworkSlot.HERO, SlotState.AUTOMATIC, SlotSource.TENDER)
        chosen = _view(ArtworkSlot.WIDE_GRID, SlotState.CHOSEN, SlotSource.CHOSEN)

        assert slots_kept_current([logo, chosen, hero]) == [logo, hero]
