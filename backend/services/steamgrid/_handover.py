"""The images Tender hands to Steam, and what Steam's answer makes of them (#2381 D15).

Every path that gives Steam a SteamGridDB image — Refresh Artwork, the fill,
the job, the ARTWORK tab, a change of game, a version switch — first puts the
image into the artwork cache or serves it from there. Both moments land here:
the image becomes the slot's hand-over, so a grid file holding it is Tender's
until Steam answers, however the apply ends. Steam's answer settles it: ``set``
makes the slot's outcome name the image, ``refused`` forgets it and, for an
image the user chose, gives back the choice it replaced.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.artwork_slot import ArtworkSlot, slot_stem
from domain.artwork_slot_choice import ArtworkSlotChoice
from domain.artwork_slot_handover import HANDED_SLOTS, ArtworkSlotHandover
from domain.artwork_slot_outcome import ArtworkSlotOutcome, SlotOutcome
from lib.errors import SteamGridDirMissingError

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from services.protocols import Clock, SgdbArtworkCache, SteamConfigStore, UnitOfWork, UnitOfWorkFactory
    from services.steamgrid._files import CachedArtwork


@dataclass(frozen=True)
class PreviousChoice:
    """The image the user had chosen for a slot before a press, read before it; ``None`` for no choice."""

    image_id: int | None


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

    def served_io(
        self, rom_id: int, slot: ArtworkSlot, image_id: int, data: bytes, previous: PreviousChoice | None = None
    ) -> None:
        """Record that *data*, SteamGridDB's image *image_id*, is handed to Steam as *rom_id*'s *slot*.

        The hand-over keeps the image the user had chosen for the slot at the
        press: *previous*, which the ARTWORK tab reads before its choice is
        saved, else the slot's choice now. Nothing is recorded for a ROM with
        no ``roms`` row.
        """
        sha256 = hashlib.sha256(data).hexdigest()
        with self._uow_factory() as uow:
            if uow.roms.get(rom_id) is None:
                return
            if previous is None:
                choice = uow.artwork_slot_choices.get(rom_id, slot)
                previous = PreviousChoice(choice.image_id if choice is not None else None)
            uow.artwork_slot_handovers.save(
                ArtworkSlotHandover.hand_over(
                    rom_id=rom_id,
                    slot=slot,
                    image_id=image_id,
                    image_sha256=sha256,
                    handed_at=self._clock.time(),
                    previous_choice_id=previous.image_id,
                )
            )

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
    ) -> int | None:
        """Settle *rom_id*'s *slot* by Steam's answer for image *image_id*; *record* is that answer's outcome, if any.

        The hand-over goes once Steam answered for its image. A ``set`` is
        saved; a ``refused`` is saved unless a ``set`` is held, whose image is
        still Tender's. Where Steam refused the handed-over image and it is the
        slot's choice, the choice the hand-over kept comes back — none, if there
        was none — so the slot is as it was before the press (D20). Answers the
        refused image's id where that happened, for the caller to drop that
        image from the cache; ``None`` otherwise, also where the choice kept is
        the refused image itself, which stays chosen.
        """
        refused = record is None or record.outcome is SlotOutcome.REFUSED
        with self._uow_factory() as uow:
            if uow.roms.get(rom_id) is None:
                return None
            handover = uow.artwork_slot_handovers.get(rom_id, slot)
            handed = handover is not None and handover.image_id == image_id
            if handed:
                uow.artwork_slot_handovers.delete_slot(rom_id, slot)
            given_back = refused and handover is not None and handed and self._give_choice_back(uow, handover)
            held = uow.artwork_slot_outcomes.get(rom_id, slot)
            if record is not None and not (refused and held is not None and held.outcome is SlotOutcome.SET):
                uow.artwork_slot_outcomes.save(record)
        return image_id if given_back else None

    def _give_choice_back(self, uow: UnitOfWork, handover: ArtworkSlotHandover) -> bool:
        """Put back the choice *handover* kept, where its refused image is the slot's choice; answers whether it did."""
        choice = uow.artwork_slot_choices.get(handover.rom_id, handover.slot)
        if choice is None or choice.image_id != handover.image_id or handover.previous_choice_id == choice.image_id:
            return False
        if handover.previous_choice_id is None:
            uow.artwork_slot_choices.delete_slot(handover.rom_id, handover.slot)
        else:
            uow.artwork_slot_choices.save(
                ArtworkSlotChoice(
                    rom_id=handover.rom_id,
                    slot=handover.slot,
                    image_id=handover.previous_choice_id,
                    chosen_at=self._clock.time(),
                )
            )
        return True

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
