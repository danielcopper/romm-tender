"""In-memory ``ArtworkSlotOutcomeRepository`` implementation for service tests."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.artwork_slot import ArtworkSlot
    from domain.artwork_slot_outcome import ArtworkSlotOutcome


class FakeArtworkSlotOutcomeRepository:
    """Dict-backed ``ArtworkSlotOutcomeRepository``: per ``rom_id``, each slot's record.

    Stored by ``rom_id`` first, so the fake unit of work's foreign-key check and
    cascade read it like every other per-ROM repository.
    """

    def __init__(self) -> None:
        self._records: dict[int, dict[ArtworkSlot, ArtworkSlotOutcome]] = {}
        self.save_count = 0

    def get(self, rom_id: int, slot: ArtworkSlot) -> ArtworkSlotOutcome | None:
        return copy.deepcopy(self._records.get(rom_id, {}).get(slot))

    def save(self, record: ArtworkSlotOutcome) -> None:
        self.save_count += 1
        self._records.setdefault(record.rom_id, {})[record.slot] = copy.deepcopy(record)

    def delete_for_rom(self, rom_id: int) -> None:
        self._records.pop(rom_id, None)

    def delete_slot(self, rom_id: int, slot: ArtworkSlot) -> None:
        self._records.get(rom_id, {}).pop(slot, None)

    def delete(self, rom_id: int) -> None:
        """The cascade's entry point, as every per-ROM fake repository names it."""
        self.delete_for_rom(rom_id)

    def _snapshot(self) -> dict[int, dict[ArtworkSlot, ArtworkSlotOutcome]]:
        return copy.deepcopy(self._records)

    def _restore(self, state: dict[int, dict[ArtworkSlot, ArtworkSlotOutcome]]) -> None:
        self._records = state
