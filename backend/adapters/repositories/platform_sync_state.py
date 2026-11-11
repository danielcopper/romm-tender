"""SQLite adapter for the ``PlatformSyncState`` aggregate over ``platform_sync_state``.

One row per platform, keyed by ``platform_folder`` (its ``fs_slug``) — the
per-platform completion stamp the incremental-skip gate reads (ADR-0023). A leaf table with no cascade
children, so ``save`` upserts with ``INSERT OR REPLACE``, ``delete`` drops one
platform's row (apply-start clear), ``revoke_skip`` flags one in place, and
``clear`` drops the whole table (Force Full Sync).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adapters.repositories._base import BaseRepository
from domain.platform_sync_state import PlatformSyncState

if TYPE_CHECKING:
    import sqlite3

_COLUMNS = "platform_folder, completed_at, rom_count, fetch_id, skip_revoked, fetch_started_at, rom_id_checksum"


def _row_to_state(row: sqlite3.Row) -> PlatformSyncState:
    values = dict(zip(row.keys(), row, strict=True))
    return PlatformSyncState(
        platform_folder=values["platform_folder"],
        completed_at=values["completed_at"],
        rom_count=values["rom_count"],
        fetch_id=values["fetch_id"],
        skip_revoked=bool(values["skip_revoked"]),
        fetch_started_at=values["fetch_started_at"],
        rom_id_checksum=values["rom_id_checksum"],
    )


class SqlitePlatformSyncStateRepository(BaseRepository):
    """Per-platform completion stamps keyed by ``platform_folder``."""

    def get(self, platform_folder: str) -> PlatformSyncState | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM platform_sync_state WHERE platform_folder = ?",
            (platform_folder,),
        ).fetchone()
        return _row_to_state(row) if row is not None else None

    def save(self, state: PlatformSyncState) -> None:
        self._conn.execute(
            f"INSERT OR REPLACE INTO platform_sync_state ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                state.platform_folder,
                state.completed_at,
                state.rom_count,
                state.fetch_id,
                int(state.skip_revoked),
                state.fetch_started_at,
                state.rom_id_checksum,
            ),
        )

    def delete(self, platform_folder: str) -> None:
        self._conn.execute("DELETE FROM platform_sync_state WHERE platform_folder = ?", (platform_folder,))

    def revoke_skip(self, platform_folder: str) -> None:
        self._conn.execute(
            "UPDATE platform_sync_state SET skip_revoked = 1 WHERE platform_folder = ?", (platform_folder,)
        )

    def has_any(self) -> bool:
        return (
            self._conn.execute("SELECT 1 FROM platform_sync_state WHERE skip_revoked = 0 LIMIT 1").fetchone()
            is not None
        )

    def clear(self) -> None:
        self._conn.execute("DELETE FROM platform_sync_state")
