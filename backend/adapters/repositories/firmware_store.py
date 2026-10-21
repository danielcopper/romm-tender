"""SQLite adapters for the firmware store: ``StoredFirmware`` and ``FirmwareLink`` over their two tables.

A store file is keyed by ``(platform_slug, file_name)``, a link by its place.
Neither table references the other: a link record outlives its store file where
the link could not be removed with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adapters.repositories._base import BaseRepository
from domain.firmware_store import FirmwareLink, StoredFirmware

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator

_STORE_COLUMNS = "platform_slug, file_name, folder, md5, size, downloaded_at, firmware_id"
_LINK_COLUMNS = "place, target, platform_slug, file_name, source, placed_at"


def _row_to_stored(row: sqlite3.Row) -> StoredFirmware:
    return StoredFirmware(
        platform_slug=row["platform_slug"],
        file_name=row["file_name"],
        folder=row["folder"],
        md5=row["md5"],
        size=row["size"],
        downloaded_at=row["downloaded_at"],
        firmware_id=row["firmware_id"],
    )


def _row_to_link(row: sqlite3.Row) -> FirmwareLink:
    return FirmwareLink(
        place=row["place"],
        target=row["target"],
        platform_slug=row["platform_slug"],
        file_name=row["file_name"],
        source=row["source"],
        placed_at=row["placed_at"],
    )


class SqliteFirmwareStoreRepository(BaseRepository):
    """The firmware store's files, keyed by ``(platform_slug, file_name)``."""

    def get(self, platform_slug: str, file_name: str) -> StoredFirmware | None:
        row = self._conn.execute(
            f"SELECT {_STORE_COLUMNS} FROM firmware_store WHERE platform_slug = ? AND file_name = ?",
            (platform_slug, file_name),
        ).fetchone()
        return _row_to_stored(row) if row is not None else None

    def save(self, stored: StoredFirmware) -> None:
        self._conn.execute(
            f"INSERT OR REPLACE INTO firmware_store ({_STORE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                stored.platform_slug,
                stored.file_name,
                stored.folder,
                stored.md5,
                stored.size,
                stored.downloaded_at,
                stored.firmware_id,
            ),
        )

    def delete(self, platform_slug: str, file_name: str) -> None:
        self._conn.execute(
            "DELETE FROM firmware_store WHERE platform_slug = ? AND file_name = ?",
            (platform_slug, file_name),
        )

    def iter_all(self) -> Iterator[StoredFirmware]:
        for row in self._conn.execute(f"SELECT {_STORE_COLUMNS} FROM firmware_store"):
            yield _row_to_stored(row)

    def get_folders(self) -> frozenset[str]:
        return frozenset(row["folder"] for row in self._conn.execute("SELECT DISTINCT folder FROM firmware_store"))


class SqliteFirmwareLinkRepository(BaseRepository):
    """The links Tender placed into emulator sources, keyed by place."""

    def get(self, place: str) -> FirmwareLink | None:
        row = self._conn.execute(f"SELECT {_LINK_COLUMNS} FROM firmware_links WHERE place = ?", (place,)).fetchone()
        return _row_to_link(row) if row is not None else None

    def save(self, link: FirmwareLink) -> None:
        self._conn.execute(
            f"INSERT OR REPLACE INTO firmware_links ({_LINK_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
            (link.place, link.target, link.platform_slug, link.file_name, link.source, link.placed_at),
        )

    def delete(self, place: str) -> None:
        self._conn.execute("DELETE FROM firmware_links WHERE place = ?", (place,))

    def iter_all(self) -> Iterator[FirmwareLink]:
        for row in self._conn.execute(f"SELECT {_LINK_COLUMNS} FROM firmware_links"):
            yield _row_to_link(row)
