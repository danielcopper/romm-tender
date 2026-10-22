"""Which covers in Steam's grid directory are set in Steam rather than Tender's.

The rule is :func:`domain.artwork_slot.is_set_in_steam`; this module reads what
it compares — the grid directory's listing, the hash of ``{app_id}p.png``, and
Tender's references: its recorded hash for the cover where a ``set`` outcome is
recorded, else its cover cache copy, and the hash of the SteamGridDB cover last
handed to Steam whose answer is outstanding (#2381 D15).
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

from domain.artwork_paths import cache_filename, final_filename
from domain.artwork_slot import ArtworkSlot, is_set_in_steam
from domain.artwork_slot_outcome import SlotOutcome

if TYPE_CHECKING:
    import logging
    from collections.abc import Collection

    from domain.artwork_slot_handover import ArtworkSlotHandover
    from domain.artwork_slot_outcome import ArtworkSlotOutcome
    from services.protocols import CoverArtFileStore, SteamConfigStore, UnitOfWorkFactory


def covers_set_in_steam(
    covers: Collection[tuple[int, int]],
    *,
    steam_config: SteamConfigStore,
    file_store: CoverArtFileStore,
    uow_factory: UnitOfWorkFactory,
    cover_cache_dir: str,
    logger: logging.Logger,
) -> set[int]:
    """The ROMs among *covers*, ``(rom_id, app_id)`` pairs, whose cover in Steam's grid directory is not Tender's.

    Where the grid directory cannot be found or listed, no cover is recognised
    as set in Steam. Reads the recorded outcomes in one short read UoW before
    any file is read.
    """
    if not covers:
        return set()
    grid = steam_config.grid_dir()
    try:
        file_names = steam_config.grid_file_names()
    except OSError as e:
        logger.warning(f"Could not list Steam's grid directory to tell covers set in Steam: {e}")
        return set()
    if not grid or file_names is None:
        return set()
    with uow_factory() as uow:
        recorded = {rom_id: uow.artwork_slot_outcomes.get(rom_id, ArtworkSlot.COVER) for rom_id, _ in covers}
        handed = {rom_id: uow.artwork_slot_handovers.get(rom_id, ArtworkSlot.COVER) for rom_id, _ in covers}
    return {
        rom_id
        for rom_id, app_id in covers
        if _is_cover_set_in_steam(
            rom_id,
            app_id,
            recorded[rom_id],
            handed[rom_id],
            grid=grid,
            file_names=file_names,
            file_store=file_store,
            cover_cache_dir=cover_cache_dir,
            logger=logger,
        )
    }


def _is_cover_set_in_steam(
    rom_id: int,
    app_id: int,
    outcome: ArtworkSlotOutcome | None,
    handover: ArtworkSlotHandover | None,
    *,
    grid: str,
    file_names: frozenset[str],
    file_store: CoverArtFileStore,
    cover_cache_dir: str,
    logger: logging.Logger,
) -> bool:
    """Whether *app_id*'s cover in *grid*, whose listing is *file_names*, is not Tender's cover for *rom_id*.

    *outcome* is the cover outcome recorded for *rom_id*, and *handover* the
    cover last handed to Steam, ``None`` where none is. A ``.png`` holding the
    handed-over cover is Tender's before any other comparison.
    """
    png = final_filename(app_id)
    if png not in file_names:
        return is_set_in_steam(file_names, app_id, ArtworkSlot.COVER, png_sha256=None, tenders_sha256=None)
    png_path = os.path.join(grid, png)
    if handover is not None:
        png_sha256 = _sha256_of(file_store, png_path, logger)
        if png_sha256 == handover.image_sha256:
            return is_set_in_steam(
                file_names,
                app_id,
                ArtworkSlot.COVER,
                png_sha256=png_sha256,
                tenders_sha256=None,
                handed_sha256=handover.image_sha256,
            )
    if outcome is not None and outcome.outcome is SlotOutcome.SET:
        png_sha256 = _sha256_of(file_store, png_path, logger)
        return is_set_in_steam(
            file_names, app_id, ArtworkSlot.COVER, png_sha256=png_sha256, tenders_sha256=outcome.image_sha256
        )
    cache_path = os.path.join(cover_cache_dir, cache_filename(rom_id))
    return _differs_from_the_cache_copy(file_names, app_id, png_path, cache_path, file_store=file_store, logger=logger)


def _differs_from_the_cache_copy(
    file_names: frozenset[str],
    app_id: int,
    png_path: str,
    cache_path: str,
    *,
    file_store: CoverArtFileStore,
    logger: logging.Logger,
) -> bool:
    """:func:`_is_cover_set_in_steam` where the cover's cache copy is Tender's reference: sizes first, then hashes."""
    sizes = _size_of(file_store, png_path, logger), _size_of(file_store, cache_path, logger)
    if None not in sizes and sizes[0] != sizes[1]:
        return True
    png_sha256 = tenders_sha256 = None
    if None not in sizes:
        png_sha256 = _sha256_of(file_store, png_path, logger)
        tenders_sha256 = _sha256_of(file_store, cache_path, logger)
    return is_set_in_steam(file_names, app_id, ArtworkSlot.COVER, png_sha256=png_sha256, tenders_sha256=tenders_sha256)


def _size_of(file_store: CoverArtFileStore, path: str, logger: logging.Logger) -> int | None:
    """The size of *path* in bytes; ``None`` where it is missing or cannot be read, and a failed read is logged.

    Files of two sizes cannot hold the same bytes, so comparing sizes first spares hashing both.
    """
    if not file_store.exists(path):
        return None
    try:
        return file_store.file_size(path)
    except OSError as e:
        logger.warning(f"Could not read the size of {path} to tell whether its cover is set in Steam: {e}")
        return None


def _sha256_of(file_store: CoverArtFileStore, path: str, logger: logging.Logger) -> str | None:
    """The SHA-256 of *path*'s bytes; ``None`` where it is missing or cannot be read, and a failed read is logged."""
    if not file_store.exists(path):
        return None
    try:
        return hashlib.sha256(file_store.read_bytes(path)).hexdigest()
    except OSError as e:
        logger.warning(f"Could not read {path} to tell whether its cover is set in Steam: {e}")
        return None
