"""The automatic cover Tender picked from SteamGridDB for a game RomM had no cover for, as the cover's owner reads it.

Such a cover is the one cover recorded ``set`` without a choice
(``domain.artwork_slot_view``). RomM's cover takes its place once RomM has
one (#2381 D24), and from then on the record would make RomM's cover read as
set in Steam, so it goes with the image it names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import SlotOutcome
from domain.cover_refresh import fresh_cover_source

if TYPE_CHECKING:
    from collections.abc import Collection

    from services.protocols import UnitOfWork, UnitOfWorkFactory


def steamgriddb_covers_io(uow_factory: UnitOfWorkFactory, rom_ids: Collection[int]) -> set[int]:
    """The ROMs among *rom_ids* whose automatic cover is Tender's SteamGridDB pick, in one short read UoW."""
    if not rom_ids:
        return set()
    with uow_factory() as uow:
        return {rom_id for rom_id in rom_ids if _is_steamgriddb_cover(uow, rom_id)}


def steamgriddb_covers_among_io(
    uow_factory: UnitOfWorkFactory, all_roms: list[dict[str, Any]], registry: dict[str, Any]
) -> set[int]:
    """:func:`steamgriddb_covers_io` over the ROMs among *all_roms* RomM has a cover for that *registry* holds bound."""
    covered = {int(rom["id"]) for rom in all_roms if "id" in rom and fresh_cover_source(rom)}
    return steamgriddb_covers_io(uow_factory, [rom_id for rom_id in covered if str(rom_id) in registry])


def forget_steamgriddb_cover_io(uow_factory: UnitOfWorkFactory, rom_id: int) -> None:
    """Drop the record and hand-over of *rom_id*'s SteamGridDB cover once RomM's cover is in its place.

    A cover the user chose keeps both: RomM's cover never takes its place.
    """
    with uow_factory() as uow:
        if uow.artwork_slot_choices.get(rom_id, ArtworkSlot.COVER) is not None:
            return
        uow.artwork_slot_outcomes.delete_slot(rom_id, ArtworkSlot.COVER)
        uow.artwork_slot_handovers.delete_slot(rom_id, ArtworkSlot.COVER)


def _is_steamgriddb_cover(uow: UnitOfWork, rom_id: int) -> bool:
    recorded = uow.artwork_slot_outcomes.get(rom_id, ArtworkSlot.COVER)
    if recorded is None or recorded.outcome is not SlotOutcome.SET:
        return False
    return uow.artwork_slot_choices.get(rom_id, ArtworkSlot.COVER) is None
