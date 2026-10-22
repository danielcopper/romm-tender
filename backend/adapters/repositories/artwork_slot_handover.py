"""SQLite adapter for the ``ArtworkSlotHandover`` aggregate over ``artwork_slot_handovers``.

One row per ROM and slot, keyed by ``(rom_id, slot)``. A leaf table whose rows
go with their ``roms`` row (``ON DELETE CASCADE``); ``save`` upserts, and
``delete_slot`` drops a hand-over once Steam's answer is reported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adapters.repositories._base import BaseRepository
from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_handover import ArtworkSlotHandover

if TYPE_CHECKING:
    import sqlite3

_COLUMNS = "rom_id, slot, image_id, image_sha256, handed_at, previous_choice_id"


def _row_to_handover(row: sqlite3.Row) -> ArtworkSlotHandover:
    return ArtworkSlotHandover(
        rom_id=row["rom_id"],
        slot=ArtworkSlot(row["slot"]),
        image_id=row["image_id"],
        image_sha256=row["image_sha256"],
        handed_at=row["handed_at"],
        previous_choice_id=row["previous_choice_id"],
    )


class SqliteArtworkSlotHandoverRepository(BaseRepository):
    """Per-ROM, per-slot hand-overs keyed by ``(rom_id, slot)``."""

    def get(self, rom_id: int, slot: ArtworkSlot) -> ArtworkSlotHandover | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM artwork_slot_handovers WHERE rom_id = ? AND slot = ?",
            (rom_id, slot.value),
        ).fetchone()
        return _row_to_handover(row) if row is not None else None

    def save(self, handover: ArtworkSlotHandover) -> None:
        self._conn.execute(
            f"INSERT OR REPLACE INTO artwork_slot_handovers ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
            (
                handover.rom_id,
                handover.slot.value,
                handover.image_id,
                handover.image_sha256,
                handover.handed_at,
                handover.previous_choice_id,
            ),
        )

    def delete_slot(self, rom_id: int, slot: ArtworkSlot) -> None:
        self._conn.execute("DELETE FROM artwork_slot_handovers WHERE rom_id = ? AND slot = ?", (rom_id, slot.value))
