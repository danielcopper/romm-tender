"""Tests for domain.artwork_slot_view — a slot's state and source in the ARTWORK tab."""

from __future__ import annotations

import pytest

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_choice import ArtworkSlotChoice
from domain.artwork_slot_outcome import ArtworkSlotOutcome
from domain.artwork_slot_view import SlotSource, SlotState, SlotView, describe_slot

_SGDB_SLOTS = [ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO, ArtworkSlot.HERO, ArtworkSlot.ICON]


def _choice(slot: ArtworkSlot, image_id: int = 77) -> ArtworkSlotChoice:
    return ArtworkSlotChoice(rom_id=1, slot=slot, image_id=image_id, chosen_at=1.0)


def _set(slot: ArtworkSlot, image_id: int = 12) -> ArtworkSlotOutcome:
    return ArtworkSlotOutcome.record_set(
        rom_id=1, slot=slot, sgdb_id=7, image_id=image_id, image_sha256="ab", recorded_at=1.0
    )


def _view(
    slot: ArtworkSlot,
    *,
    set_in_steam: bool = False,
    choice: ArtworkSlotChoice | None = None,
    recorded: ArtworkSlotOutcome | None = None,
    has_game: bool = True,
    filled: bool = False,
    cached_image_id: int | None = None,
) -> SlotView:
    return describe_slot(
        slot,
        set_in_steam=set_in_steam,
        choice=choice,
        recorded=recorded,
        has_game=has_game,
        filled=filled,
        cached_image_id=cached_image_id,
    )


class TestSetInSteamOutranksEverything:
    @pytest.mark.parametrize("slot", list(ArtworkSlot))
    def test_a_chosen_slot_set_in_steam_reads_set_in_steam(self, slot):
        view = _view(slot, set_in_steam=True, choice=_choice(slot), filled=True)

        assert view == SlotView(slot, SlotState.SET_IN_STEAM, SlotSource.STEAM, None)


class TestAChoice:
    @pytest.mark.parametrize("slot", list(ArtworkSlot))
    def test_names_the_chosen_image(self, slot):
        view = _view(slot, choice=_choice(slot, 77), recorded=_set(slot, 12) if slot is not ArtworkSlot.COVER else None)

        assert view == SlotView(slot, SlotState.CHOSEN, SlotSource.CHOSEN, 77)

    def test_without_a_game_still_reads_chosen(self):
        assert _view(ArtworkSlot.LOGO, choice=_choice(ArtworkSlot.LOGO), has_game=False).state is SlotState.CHOSEN


class TestAutomatic:
    def test_the_cover_is_romms_with_or_without_a_game(self):
        for has_game in (True, False):
            view = _view(ArtworkSlot.COVER, has_game=has_game, filled=True)

            assert view == SlotView(ArtworkSlot.COVER, SlotState.AUTOMATIC, SlotSource.ROMM, None)

    @pytest.mark.parametrize("has_game", [True, False])
    def test_a_cover_tender_set_from_steamgriddb_is_tenders_pick(self, has_game):
        view = _view(ArtworkSlot.COVER, has_game=has_game, filled=True, recorded=_set(ArtworkSlot.COVER, 12))

        assert view == SlotView(ArtworkSlot.COVER, SlotState.AUTOMATIC, SlotSource.TENDER, 12)

    def test_a_steamgriddb_cover_whose_file_is_gone_is_romms_again(self):
        view = _view(ArtworkSlot.COVER, recorded=_set(ArtworkSlot.COVER, 12))

        assert view == SlotView(ArtworkSlot.COVER, SlotState.AUTOMATIC, SlotSource.ROMM, None)

    @pytest.mark.parametrize("slot", _SGDB_SLOTS)
    def test_a_steamgriddb_slot_without_a_game_needs_one(self, slot):
        assert _view(slot, has_game=False, filled=True).source is SlotSource.NEEDS_GAME

    @pytest.mark.parametrize("slot", _SGDB_SLOTS)
    def test_a_filled_slot_shows_tenders_recorded_image(self, slot):
        view = _view(slot, filled=True, recorded=_set(slot, 12), cached_image_id=99)

        assert view == SlotView(slot, SlotState.AUTOMATIC, SlotSource.TENDER, 12)

    def test_a_filled_slot_without_a_set_record_names_the_cached_image(self):
        view = _view(ArtworkSlot.HERO, filled=True, cached_image_id=99)

        assert (view.source, view.image_id) == (SlotSource.TENDER, 99)

    def test_an_empty_slot_steamgriddb_has_none_for(self):
        none = ArtworkSlotOutcome.record_none(rom_id=1, slot=ArtworkSlot.LOGO, sgdb_id=7, recorded_at=1.0)

        assert _view(ArtworkSlot.LOGO, recorded=none).source is SlotSource.NONE

    def test_an_empty_slot_without_a_record_is_not_filled_yet(self):
        assert _view(ArtworkSlot.LOGO).source is SlotSource.EMPTY

    def test_an_empty_slot_with_a_set_record_is_not_filled_yet(self):
        assert _view(ArtworkSlot.LOGO, recorded=_set(ArtworkSlot.LOGO)).source is SlotSource.EMPTY
