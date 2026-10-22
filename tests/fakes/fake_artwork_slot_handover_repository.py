"""In-memory ``ArtworkSlotHandoverRepository`` implementation for service tests."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.artwork_slot import ArtworkSlot
    from domain.artwork_slot_handover import ArtworkSlotHandover


class FakeArtworkSlotHandoverRepository:
    """Dict-backed ``ArtworkSlotHandoverRepository``: per ``rom_id``, each slot's hand-over.

    Stored by ``rom_id`` first, so the fake unit of work's foreign-key check and
    cascade read it like every other per-ROM repository.
    """

    def __init__(self) -> None:
        self._handovers: dict[int, dict[ArtworkSlot, ArtworkSlotHandover]] = {}

    def get(self, rom_id: int, slot: ArtworkSlot) -> ArtworkSlotHandover | None:
        return copy.deepcopy(self._handovers.get(rom_id, {}).get(slot))

    def save(self, handover: ArtworkSlotHandover) -> None:
        self._handovers.setdefault(handover.rom_id, {})[handover.slot] = copy.deepcopy(handover)

    def delete_slot(self, rom_id: int, slot: ArtworkSlot) -> None:
        self._handovers.get(rom_id, {}).pop(slot, None)

    def delete(self, rom_id: int) -> None:
        """The cascade's entry point, as every per-ROM fake repository names it."""
        self._handovers.pop(rom_id, None)

    def _snapshot(self) -> dict[int, dict[ArtworkSlot, ArtworkSlotHandover]]:
        return copy.deepcopy(self._handovers)

    def _restore(self, state: dict[int, dict[ArtworkSlot, ArtworkSlotHandover]]) -> None:
        self._handovers = state
