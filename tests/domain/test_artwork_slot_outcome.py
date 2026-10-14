"""Tests for domain.artwork_slot_outcome — what was found for a slot, and how long it rules the slot out."""

from __future__ import annotations

import pytest

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import RULED_OUT_SECONDS, ArtworkSlotOutcome, SlotOutcome, slot_waits

NOW = 1_800_000_000.0


def _none(*, sgdb_id: int | None = 7, at: float = NOW) -> ArtworkSlotOutcome:
    return ArtworkSlotOutcome.record_none(rom_id=1, slot=ArtworkSlot.LOGO, sgdb_id=sgdb_id, recorded_at=at)


def _refused(*, sgdb_id: int | None = 7, at: float = NOW) -> ArtworkSlotOutcome:
    return ArtworkSlotOutcome.record_refused(rom_id=1, slot=ArtworkSlot.WIDE_GRID, sgdb_id=sgdb_id, recorded_at=at)


class TestTheConstructors:
    def test_a_set_outcome_carries_its_image_id_and_hash(self):
        record = ArtworkSlotOutcome.record_set(
            rom_id=1, slot=ArtworkSlot.WIDE_GRID, sgdb_id=7, image_id=99, image_sha256="ab" * 32, recorded_at=NOW
        )

        assert (record.outcome, record.image_id, record.image_sha256) == (SlotOutcome.SET, 99, "ab" * 32)

    def test_none_and_refused_carry_no_image(self):
        assert (_none().image_id, _none().image_sha256) == (None, None)
        assert (_refused().outcome, _refused().image_id) == (SlotOutcome.REFUSED, None)

    def test_a_set_outcome_without_a_hash_is_refused(self):
        with pytest.raises(ValueError, match="hash"):
            ArtworkSlotOutcome.record_set(
                rom_id=1, slot=ArtworkSlot.LOGO, sgdb_id=7, image_id=99, image_sha256="", recorded_at=NOW
            )

    def test_the_hero_has_no_outcome(self):
        with pytest.raises(ValueError, match="hero"):
            ArtworkSlotOutcome.record_none(rom_id=1, slot=ArtworkSlot.HERO, sgdb_id=7, recorded_at=NOW)


class TestNoneAndRefusedRuleTheSlotOutForAWeek:
    @pytest.mark.parametrize("record", [_none(), _refused()], ids=["none", "refused"])
    def test_within_the_week(self, record):
        assert record.rules_out(sgdb_id=7, now=NOW + RULED_OUT_SECONDS - 1)

    @pytest.mark.parametrize("record", [_none(), _refused()], ids=["none", "refused"])
    def test_not_once_the_week_is_over(self, record):
        assert not record.rules_out(sgdb_id=7, now=NOW + RULED_OUT_SECONDS)

    def test_the_week_is_seven_days(self):
        assert RULED_OUT_SECONDS == 7 * 24 * 60 * 60

    @pytest.mark.parametrize("record", [_none(), _refused()], ids=["none", "refused"])
    def test_not_once_the_roms_steamgriddb_id_changed(self, record):
        assert not record.rules_out(sgdb_id=8, now=NOW + 1)

    def test_a_none_for_a_rom_without_an_id_stops_once_it_has_one(self):
        record = _none(sgdb_id=None)

        assert record.rules_out(sgdb_id=None, now=NOW + 1)
        assert not record.rules_out(sgdb_id=7, now=NOW + 1)

    def test_a_set_outcome_never_rules_the_slot_out(self):
        record = ArtworkSlotOutcome.record_set(
            rom_id=1, slot=ArtworkSlot.LOGO, sgdb_id=7, image_id=99, image_sha256="ab" * 32, recorded_at=NOW
        )

        assert not record.rules_out(sgdb_id=7, now=NOW + 1)


class TestWhetherTheJobAsksForASlot:
    def test_an_empty_slot_with_nothing_recorded_waits(self):
        assert slot_waits(frozenset(), 100, ArtworkSlot.WIDE_GRID, None, sgdb_id=7, now=NOW)

    def test_a_filled_slot_does_not_wait(self):
        assert not slot_waits(frozenset({"100.png"}), 100, ArtworkSlot.WIDE_GRID, None, sgdb_id=7, now=NOW)

    def test_a_slot_steamgriddb_had_nothing_for_this_week_does_not_wait(self):
        assert not slot_waits(frozenset(), 100, ArtworkSlot.LOGO, _none(), sgdb_id=7, now=NOW + 1)

    def test_it_waits_again_once_the_week_is_over(self):
        assert slot_waits(frozenset(), 100, ArtworkSlot.LOGO, _none(), sgdb_id=7, now=NOW + RULED_OUT_SECONDS)

    def test_a_set_slot_whose_file_is_gone_waits(self):
        record = ArtworkSlotOutcome.record_set(
            rom_id=1, slot=ArtworkSlot.LOGO, sgdb_id=7, image_id=99, image_sha256="ab" * 32, recorded_at=NOW
        )

        assert slot_waits(frozenset(), 100, ArtworkSlot.LOGO, record, sgdb_id=7, now=NOW + 1)
