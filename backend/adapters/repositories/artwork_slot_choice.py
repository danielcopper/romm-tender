"""SQLite adapter for the ``ArtworkSlotChoice`` aggregate over ``artwork_slot_choices``.

One row per ROM and slot, keyed by ``(rom_id, slot)``. A leaf table whose rows
go with their ``roms`` row (``ON DELETE CASCADE``); ``save`` upserts, and
``delete_slot`` sets a slot back to automatic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adapters.repositories._base import BaseRepository
from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_choice import ArtworkSlotChoice

if TYPE_CHECKING:
    import sqlite3

_COLUMNS = "rom_id, slot, image_id, chosen_at"


def _row_to_choice(row: sqlite3.Row) -> ArtworkSlotChoice:
    return ArtworkSlotChoice(
        rom_id=row["rom_id"],
        slot=ArtworkSlot(row["slot"]),
        image_id=row["image_id"],
        chosen_at=row["chosen_at"],
    )


class SqliteArtworkSlotChoiceRepository(BaseRepository):
    """Per-ROM, per-slot artwork choices keyed by ``(rom_id, slot)``."""

    def get(self, rom_id: int, slot: ArtworkSlot) -> ArtworkSlotChoice | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM artwork_slot_choices WHERE rom_id = ? AND slot = ?",
            (rom_id, slot.value),
        ).fetchone()
        return _row_to_choice(row) if row is not None else None

    def save(self, choice: ArtworkSlotChoice) -> None:
        self._conn.execute(
            f"INSERT OR REPLACE INTO artwork_slot_choices ({_COLUMNS}) VALUES (?, ?, ?, ?)",
            (choice.rom_id, choice.slot.value, choice.image_id, choice.chosen_at),
        )

    def get_all_for_rom(self, rom_id: int) -> list[ArtworkSlotChoice]:
        rows = self._conn.execute(f"SELECT {_COLUMNS} FROM artwork_slot_choices WHERE rom_id = ?", (rom_id,))
        return [_row_to_choice(row) for row in rows.fetchall()]

    def delete_slot(self, rom_id: int, slot: ArtworkSlot) -> None:
        self._conn.execute("DELETE FROM artwork_slot_choices WHERE rom_id = ? AND slot = ?", (rom_id, slot.value))

    def delete_for_rom(self, rom_id: int) -> None:
        self._conn.execute("DELETE FROM artwork_slot_choices WHERE rom_id = ?", (rom_id,))
