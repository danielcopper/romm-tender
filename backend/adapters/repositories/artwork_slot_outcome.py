"""SQLite adapter for the ``ArtworkSlotOutcome`` aggregate over ``artwork_slot_outcomes``.

One row per ROM and slot, keyed by ``(rom_id, slot)``. A leaf table whose rows
also go with their ``roms`` row (``ON DELETE CASCADE``); ``save`` upserts and
``delete_for_rom`` drops a ROM's rows, ``delete_slot`` one of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adapters.repositories._base import BaseRepository
from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import ArtworkSlotOutcome, SlotOutcome

if TYPE_CHECKING:
    import sqlite3

_COLUMNS = "rom_id, slot, outcome, sgdb_id, recorded_at, image_id, image_sha256"


def _row_to_outcome(row: sqlite3.Row) -> ArtworkSlotOutcome:
    return ArtworkSlotOutcome(
        rom_id=row["rom_id"],
        slot=ArtworkSlot(row["slot"]),
        outcome=SlotOutcome(row["outcome"]),
        sgdb_id=row["sgdb_id"],
        recorded_at=row["recorded_at"],
        image_id=row["image_id"],
        image_sha256=row["image_sha256"],
    )


class SqliteArtworkSlotOutcomeRepository(BaseRepository):
    """Per-ROM, per-slot artwork outcomes keyed by ``(rom_id, slot)``."""

    def get(self, rom_id: int, slot: ArtworkSlot) -> ArtworkSlotOutcome | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM artwork_slot_outcomes WHERE rom_id = ? AND slot = ?",
            (rom_id, slot.value),
        ).fetchone()
        return _row_to_outcome(row) if row is not None else None

    def save(self, record: ArtworkSlotOutcome) -> None:
        self._conn.execute(
            f"INSERT OR REPLACE INTO artwork_slot_outcomes ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.rom_id,
                record.slot.value,
                record.outcome.value,
                record.sgdb_id,
                record.recorded_at,
                record.image_id,
                record.image_sha256,
            ),
        )

    def delete_for_rom(self, rom_id: int) -> None:
        self._conn.execute("DELETE FROM artwork_slot_outcomes WHERE rom_id = ?", (rom_id,))

    def delete_slot(self, rom_id: int, slot: ArtworkSlot) -> None:
        self._conn.execute("DELETE FROM artwork_slot_outcomes WHERE rom_id = ? AND slot = ?", (rom_id, slot.value))
