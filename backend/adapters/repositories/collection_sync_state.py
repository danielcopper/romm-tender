"""SQLite adapter for the ``CollectionSyncState`` aggregate over ``collection_sync_state``.

One row per synced standard/smart collection, keyed by the composite
``(collection_id, collection_kind)`` — the per-collection completion stamp the
incremental-skip gate reads (ADR-0023, the collection sibling of
``platform_sync_state``). A leaf table with no cascade children, so ``save``
upserts with ``INSERT OR REPLACE``, ``delete`` drops one collection's row,
``delete_intersecting`` drops every row whose member set holds one of the given
ROMs, and ``clear`` drops the whole table (Force Full Sync). ``member_rom_ids``
is stored as a JSON array TEXT and decoded back to a tuple.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adapters.repositories._base import BaseRepository
from domain.collection_sync_state import CollectionSyncState

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Collection, Iterator

_COLUMNS = "collection_id, collection_kind, updated_at, completed_at, rom_count, member_rom_ids"


class SqliteCollectionSyncStateRepository(BaseRepository):
    """Per-collection completion stamps keyed by ``(collection_id, collection_kind)``."""

    def _row_to_state(self, row: sqlite3.Row) -> CollectionSyncState:
        return CollectionSyncState(
            collection_id=row["collection_id"],
            collection_kind=row["collection_kind"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            rom_count=row["rom_count"],
            member_rom_ids=tuple(int(rid) for rid in self._json_or_none(row["member_rom_ids"]) or []),
        )

    def get(self, collection_id: str, collection_kind: str) -> CollectionSyncState | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM collection_sync_state WHERE collection_id = ? AND collection_kind = ?",
            (collection_id, collection_kind),
        ).fetchone()
        return self._row_to_state(row) if row is not None else None

    def save(self, state: CollectionSyncState) -> None:
        self._conn.execute(
            f"INSERT OR REPLACE INTO collection_sync_state ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
            (
                state.collection_id,
                state.collection_kind,
                state.updated_at,
                state.completed_at,
                state.rom_count,
                self._json(list(state.member_rom_ids)),
            ),
        )

    def delete(self, collection_id: str, collection_kind: str) -> None:
        self._conn.execute(
            "DELETE FROM collection_sync_state WHERE collection_id = ? AND collection_kind = ?",
            (collection_id, collection_kind),
        )

    def delete_intersecting(self, rom_ids: Collection[int]) -> None:
        wanted = set(rom_ids)
        if not wanted:
            return
        holding = [
            (stamp.collection_id, stamp.collection_kind)
            for stamp in self.iter_all()
            if wanted.intersection(stamp.member_rom_ids)
        ]
        for collection_id, collection_kind in holding:
            self.delete(collection_id, collection_kind)

    def iter_all(self) -> Iterator[CollectionSyncState]:
        for row in self._conn.execute(f"SELECT {_COLUMNS} FROM collection_sync_state").fetchall():
            yield self._row_to_state(row)

    def has_any(self) -> bool:
        return self._conn.execute("SELECT 1 FROM collection_sync_state LIMIT 1").fetchone() is not None

    def clear(self) -> None:
        self._conn.execute("DELETE FROM collection_sync_state")
