"""In-memory ``ArtworkSlotChoiceRepository`` implementation for service tests."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.artwork_slot import ArtworkSlot
    from domain.artwork_slot_choice import ArtworkSlotChoice


class FakeArtworkSlotChoiceRepository:
    """Dict-backed ``ArtworkSlotChoiceRepository``: per ``rom_id``, each slot's choice.

    Stored by ``rom_id`` first, so the fake unit of work's foreign-key check and
    cascade read it like every other per-ROM repository.
    """

    def __init__(self) -> None:
        self._choices: dict[int, dict[ArtworkSlot, ArtworkSlotChoice]] = {}
        self.save_count = 0

    def get(self, rom_id: int, slot: ArtworkSlot) -> ArtworkSlotChoice | None:
        return copy.deepcopy(self._choices.get(rom_id, {}).get(slot))

    def save(self, choice: ArtworkSlotChoice) -> None:
        self.save_count += 1
        self._choices.setdefault(choice.rom_id, {})[choice.slot] = copy.deepcopy(choice)

    def list_for_rom(self, rom_id: int) -> list[ArtworkSlotChoice]:
        return [copy.deepcopy(choice) for choice in self._choices.get(rom_id, {}).values()]

    def delete_slot(self, rom_id: int, slot: ArtworkSlot) -> None:
        self._choices.get(rom_id, {}).pop(slot, None)

    def delete_for_rom(self, rom_id: int) -> None:
        self._choices.pop(rom_id, None)

    def delete(self, rom_id: int) -> None:
        """The cascade's entry point, as every per-ROM fake repository names it."""
        self._choices.pop(rom_id, None)

    def _snapshot(self) -> dict[int, dict[ArtworkSlot, ArtworkSlotChoice]]:
        return copy.deepcopy(self._choices)

    def _restore(self, state: dict[int, dict[ArtworkSlot, ArtworkSlotChoice]]) -> None:
        self._choices = state
