"""SQLite adapter for the ``AnsweredSaveDirectory`` aggregate over ``answered_save_directories``.

One row per ROM, keyed by ``rom_id``. A leaf table whose rows also go with their
``roms`` row (``ON DELETE CASCADE``); ``save`` upserts and ``delete`` drops one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adapters.repositories._base import BaseRepository
from domain.answered_save_directory import AnsweredSaveDirectory

if TYPE_CHECKING:
    import sqlite3


def _row_to_record(row: sqlite3.Row) -> AnsweredSaveDirectory:
    return AnsweredSaveDirectory(rom_id=row["rom_id"], directory=row["directory"])


class SqliteAnsweredSaveDirectoryRepository(BaseRepository):
    """Per-ROM answered save directories keyed by ``rom_id``."""

    def get(self, rom_id: int) -> AnsweredSaveDirectory | None:
        row = self._conn.execute(
            "SELECT rom_id, directory FROM answered_save_directories WHERE rom_id = ?",
            (rom_id,),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def save(self, record: AnsweredSaveDirectory) -> None:
        self._conn.execute(
            "INSERT INTO answered_save_directories (rom_id, directory) VALUES (?, ?) "
            "ON CONFLICT(rom_id) DO UPDATE SET directory = excluded.directory",
            (record.rom_id, record.directory),
        )

    def delete(self, rom_id: int) -> None:
        self._conn.execute("DELETE FROM answered_save_directories WHERE rom_id = ?", (rom_id,))
