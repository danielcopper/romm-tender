"""What the RomM library holds in firmware, and the cache that answers for it.

The one place the library's firmware is read and the one place its answer is
kept. Everything downstream — the status surfaces, the download entry points —
asks here rather than the server, so a single fetch serves a whole page and an
unreachable server degrades in one place instead of five.

The firmware comes off RomM's platform list, which carries each platform's
files, and each file belongs to the platform its ``platform_id`` names
(``domain/firmware_platforms.py``). A fresh read also moves Tender's older
entries to those platforms (:class:`~services.firmware.refiling.FirmwareRefiling`).

The cache is durable by way of the SQLite ``firmware_cache`` table, so a
restart still has a listing to answer from while the server is unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.firmware_cache import FirmwareCacheEntry
from domain.firmware_platforms import library_firmware

if TYPE_CHECKING:
    import logging

    from services.firmware.refiling import FirmwareRefiling
    from services.protocols import Clock, RommFirmwareApi, UnitOfWorkFactory

_FIRMWARE_CACHE_TTL = 3600  # 1 hour


@dataclass(frozen=True)
class FirmwareListingConfig:
    """Frozen wiring bundle handed to ``FirmwareListing.__init__``.

    Holds the RomM API adapter, the clock the TTL is measured against, the
    logger, the SQLite Unit-of-Work factory the durable half of the cache is
    read and written through, and the peer that moves older entries after a
    fresh read.
    """

    romm_api: RommFirmwareApi
    logger: logging.Logger
    clock: Clock
    uow_factory: UnitOfWorkFactory
    refiling: FirmwareRefiling


class FirmwareListing:
    """The RomM firmware listing and its cache — the subsystem's only reader of the library's firmware."""

    def __init__(self, *, config: FirmwareListingConfig) -> None:
        self._romm_api = config.romm_api
        self._logger = config.logger
        self._clock = config.clock
        self._uow_factory = config.uow_factory
        self._refiling = config.refiling
        self._firmware_cache: list[dict[str, Any]] | None = None
        self._firmware_cache_epoch: float = 0
        self._restore_firmware_cache()

    def _restore_firmware_cache(self) -> None:
        """Rebuild the in-memory firmware cache from the SQLite cache table.

        The ``firmware_cache`` table is a thin record per ADR-0003 — it carries
        each file's platform and name but not its ``md5_hash``, so a restart
        still has a listing to answer from while the server is unreachable;
        ``md5_hash`` is intentionally absent (display-only).
        """
        try:
            with self._uow_factory() as uow:
                entries = list(uow.firmware_cache.iter_all())
                epoch = uow.firmware_cache.get_cache_epoch()
        except Exception as e:
            self._logger.warning(f"Failed to load firmware cache from DB: {e}")
            return

        if not entries or epoch is None:
            return

        self._firmware_cache = [self._entry_to_firmware_dict(entry) for entry in entries]
        self._firmware_cache_epoch = epoch
        self._logger.info("Restored firmware cache from DB (%d items)", len(entries))

    @staticmethod
    def _entry_to_firmware_dict(entry: FirmwareCacheEntry) -> dict[str, Any]:
        """Reconstruct an in-memory firmware dict from a thin cache aggregate."""
        return {
            "id": entry.id,
            "file_name": entry.name,
            "platform_slug": entry.platform_slug,
            "file_size_bytes": entry.file_size_bytes,
            "md5_hash": "",
        }

    def _persist_firmware_cache(self) -> None:
        """Replace the SQLite firmware cache with the current in-memory listing.

        Maps each listing row to a thin ``FirmwareCacheEntry`` and writes them
        wholesale.
        """
        if self._firmware_cache is None:
            return
        entries = [
            FirmwareCacheEntry.cached(
                id=fw.get("id"),
                name=fw.get("file_name", ""),
                platform_slug=fw["platform_slug"],
                file_size_bytes=fw.get("file_size_bytes", 0),
                cached_at=self._firmware_cache_epoch,
            )
            for fw in self._firmware_cache
        ]
        try:
            with self._uow_factory() as uow:
                uow.firmware_cache.replace_all(entries)
        except Exception as e:
            self._logger.warning(f"Failed to persist firmware cache: {e}")

    def get_firmware_list(self) -> list[dict[str, Any]]:
        """Return the library's firmware, each row with its ``platform_slug``, using the cache while its TTL lasts.

        TTL is checked against the wall-clock cache epoch so a cache
        restored from disk after a backend restart still expires.

        On HTTP error, falls back to cached data if there is any and RAISES
        otherwise. Returning an empty list instead would be indistinguishable
        from a server that genuinely holds no firmware, and ``check_platform_bios``
        answers a confident "needs none" for that — so the raise is what lets a
        failed fetch be reported as unknown rather than as a negative (#1693).
        """
        now = self._clock.time()
        if self._firmware_cache is not None and (now - self._firmware_cache_epoch) < _FIRMWARE_CACHE_TTL:
            return self._firmware_cache

        try:
            platforms = self._romm_api.list_platforms()
            result = library_firmware(platforms)
            self._firmware_cache = result
            self._firmware_cache_epoch = self._clock.time()
            self._persist_firmware_cache()
        except Exception as e:
            self._logger.warning(f"Failed to fetch firmware list: {e}")
            if self._firmware_cache is not None:
                return self._firmware_cache
            raise
        self._refile(platforms, result)
        return result

    def _refile(self, platforms: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
        """Move Tender's older entries to their platforms; a failure costs that move, never the listing."""
        try:
            self._refiling.do_refile(platforms, rows)
        except Exception as e:
            self._logger.warning(f"Failed to move older BIOS records and picks to their platforms: {e}")

    def invalidate(self) -> None:
        """Clear cached firmware list so the next call re-fetches."""
        self._firmware_cache = None
        self._firmware_cache_epoch = 0
        try:
            with self._uow_factory() as uow:
                uow.firmware_cache.clear()
        except Exception as e:
            self._logger.warning(f"Failed to clear persisted firmware cache: {e}")
