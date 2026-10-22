"""The images Tender hands to Steam, and what Steam's answer makes of them (#2381 D15).

Every path that gives Steam a SteamGridDB image — Refresh Artwork, the fill,
the job, the ARTWORK tab, a change of game, a version switch — first puts the
image into the artwork cache or serves it from there. Both moments land here:
the image becomes the slot's hand-over, so a grid file holding it is Tender's
until Steam answers, however the apply ends. Steam's answer settles it: ``set``
makes the slot's outcome name the image, ``refused`` forgets it and, for an
image the user chose, the choice.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.artwork_slot import ArtworkSlot, slot_stem
from domain.artwork_slot_handover import HANDED_SLOTS, ArtworkSlotHandover
from domain.artwork_slot_outcome import ArtworkSlotOutcome, SlotOutcome
from lib.errors import SteamGridDirMissingError

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from services.protocols import Clock, SgdbArtworkCache, SteamConfigStore, UnitOfWorkFactory
    from services.steamgrid._files import CachedArtwork


@dataclass(frozen=True)
class HandedOverImagesConfig:
    """Wiring for :class:`HandedOverImages`: the database, the artwork cache, Steam's grid directory, the clock."""

    uow_factory: UnitOfWorkFactory
    cache: SgdbArtworkCache
    steam_config: SteamConfigStore
    clock: Clock
    logger: logging.Logger


class HandedOverImages:
    """Records each image handed to Steam, and settles it by Steam's answer (synchronous; callers offload it)."""

    def __init__(self, *, config: HandedOverImagesConfig) -> None:
        self._uow_factory = config.uow_factory
        self._cache = config.cache
        self._steam_config = config.steam_config
        self._clock = config.clock
        self._logger = config.logger

    def served_io(self, rom_id: int, slot: ArtworkSlot, image_id: int, data: bytes) -> None:
        """Record that *data*, SteamGridDB's image *image_id*, is handed to Steam as *rom_id*'s *slot*.

        Nothing is recorded for a ROM with no ``roms`` row.
        """
        handover = ArtworkSlotHandover.hand_over(
            rom_id=rom_id,
            slot=slot,
            image_id=image_id,
            image_sha256=hashlib.sha256(data).hexdigest(),
            handed_at=self._clock.time(),
        )
        with self._uow_factory() as uow:
            if uow.roms.get(rom_id) is not None:
                uow.artwork_slot_handovers.save(handover)

    def stored_io(self, rom_id: int, asset_type: str, previous: CachedArtwork | None, stored: CachedArtwork) -> None:
        """Note that *stored* replaced *previous* as *rom_id*'s cached *asset_type*: it is the image Steam gets next.

        Where the slot has no ``set`` outcome, the grid file Tender's cached
        copy made Tender's would stop being Tender's once that copy goes; so
        while that file holds *previous*'s bytes, *previous* is recorded as
        ``set`` first. A file that cannot be read is logged and left out.
        """
        slot = ArtworkSlot(asset_type)
        if slot not in HANDED_SLOTS:
            return
        if previous is not None and slot is not ArtworkSlot.COVER:
            self._keep_the_shown_image_io(rom_id, slot, previous)
        data = self._read_io(self._cache.read_bytes, stored.path)
        if data is not None:
            self.served_io(rom_id, slot, stored.image_id, data)

    def settle_io(
        self, rom_id: int, slot: ArtworkSlot, record: ArtworkSlotOutcome | None, image_id: int | None
    ) -> None:
        """Settle *rom_id*'s *slot* by Steam's answer for image *image_id*; *record* is that answer's outcome, if any.

        The hand-over goes once Steam answered for its image. A ``set`` is saved;
        a ``refused`` is saved unless a ``set`` is held, whose image is still
        Tender's, and an image the user chose that Steam refused discards the
        choice, so the slot is as it was before the press.
        """
        refused = record is None or record.outcome is SlotOutcome.REFUSED
        with self._uow_factory() as uow:
            if uow.roms.get(rom_id) is None:
                return
            handover = uow.artwork_slot_handovers.get(rom_id, slot)
            if handover is not None and handover.image_id == image_id:
                uow.artwork_slot_handovers.delete_slot(rom_id, slot)
            if refused:
                choice = uow.artwork_slot_choices.get(rom_id, slot)
                if choice is not None and choice.image_id == image_id:
                    uow.artwork_slot_choices.delete_slot(rom_id, slot)
            if record is None:
                return
            held = uow.artwork_slot_outcomes.get(rom_id, slot)
            if refused and held is not None and held.outcome is SlotOutcome.SET:
                return
            uow.artwork_slot_outcomes.save(record)

    def _keep_the_shown_image_io(self, rom_id: int, slot: ArtworkSlot, previous: CachedArtwork) -> None:
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
            recorded = uow.artwork_slot_outcomes.get(rom_id, slot)
        if rom is None or rom.shortcut_app_id is None:
            return
        if recorded is not None and recorded.outcome is SlotOutcome.SET:
            return
        shown = self._read_io(self._steam_config.read_grid_file, f"{slot_stem(rom.shortcut_app_id, slot)}.png")
        cached = self._read_io(self._cache.read_bytes, previous.path) if shown is not None else None
        if shown is None or cached is None or shown != cached:
            return
        record = ArtworkSlotOutcome.record_set(
            rom_id=rom_id,
            slot=slot,
            sgdb_id=None,
            image_id=previous.image_id,
            image_sha256=hashlib.sha256(cached).hexdigest(),
            recorded_at=self._clock.time(),
        )
        with self._uow_factory() as uow:
            uow.artwork_slot_outcomes.save(record)

    def _read_io(self, read: Callable[[str], bytes], path: str) -> bytes | None:
        """The bytes *read* answers for *path*; ``None`` where there are none or they cannot be read (logged)."""
        try:
            return read(path)
        except FileNotFoundError:
            return None
        except (OSError, SteamGridDirMissingError) as e:
            self._logger.warning(f"Could not read {path} for the image handed to Steam: {e}")
            return None
