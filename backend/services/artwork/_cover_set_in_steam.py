"""Which covers in Steam's grid directory are set in Steam rather than Tender's.

The rule is :func:`domain.artwork_slot.is_set_in_steam`; this module reads what
it compares — the grid directory's listing, the hash of ``{app_id}p.png``, and
Tender's reference: its recorded hash for the cover where a ``set`` outcome is
recorded, else its cover cache copy.
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
    set_in_steam: set[int] = set()
    for rom_id, app_id in covers:
        png = final_filename(app_id)
        png_sha256 = _sha256_of(file_store, os.path.join(grid, png), logger) if png in file_names else None
        outcome = recorded[rom_id]
        if outcome is not None and outcome.outcome is SlotOutcome.SET:
            tenders_sha256 = outcome.image_sha256
        elif png_sha256 is not None:
            tenders_sha256 = _sha256_of(file_store, os.path.join(cover_cache_dir, cache_filename(rom_id)), logger)
        else:
            tenders_sha256 = None
        if is_set_in_steam(file_names, app_id, ArtworkSlot.COVER, png_sha256=png_sha256, tenders_sha256=tenders_sha256):
            set_in_steam.add(rom_id)
    return set_in_steam


def _sha256_of(file_store: CoverArtFileStore, path: str, logger: logging.Logger) -> str | None:
    """The SHA-256 of *path*'s bytes; ``None`` where it is missing or cannot be read, and a failed read is logged."""
    if not file_store.exists(path):
        return None
    try:
        return hashlib.sha256(file_store.read_bytes(path)).hexdigest()
    except OSError as e:
        logger.warning(f"Could not read {path} to tell whether its cover is set in Steam: {e}")
        return None
