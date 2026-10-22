"""Tests for ``domain.artwork_slot_handover``: which slots carry a hand-over, and what one must hold."""

from __future__ import annotations

import pytest

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_handover import HANDED_SLOTS, ArtworkSlotHandover


class TestHandOver:
    @pytest.mark.parametrize("slot", sorted(HANDED_SLOTS))
    def test_a_slot_judged_by_its_hash_carries_one(self, slot):
        handover = ArtworkSlotHandover.hand_over(rom_id=1, slot=slot, image_id=2, image_sha256="sha", handed_at=3.0)

        assert (handover.slot, handover.image_id, handover.image_sha256) == (slot, 2, "sha")

    def test_the_icon_carries_none_its_path_is_judged(self):
        with pytest.raises(ValueError, match="icon"):
            ArtworkSlotHandover.hand_over(
                rom_id=1, slot=ArtworkSlot.ICON, image_id=2, image_sha256="sha", handed_at=3.0
            )

    def test_a_hand_over_carries_its_hash(self):
        with pytest.raises(ValueError, match="hash"):
            ArtworkSlotHandover.hand_over(rom_id=1, slot=ArtworkSlot.LOGO, image_id=2, image_sha256="", handed_at=3.0)

    def test_the_four_slots_told_apart_by_their_files_hash(self):
        assert frozenset(ArtworkSlot) - HANDED_SLOTS == {ArtworkSlot.ICON}
