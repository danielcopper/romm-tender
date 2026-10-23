"""What the game page's ARTWORK tab says about one artwork slot: its state and where its image comes from.

Owns the rule that turns what Tender knows about a slot — whether it is set in
Steam, the user's choice, the slot's record, whether the ROM has a SteamGridDB
game — into the slot's state and source
(docs/architecture/steam-non-steam-shortcuts.md, "Game page artwork"). Reading
those facts is the service's; wording them is the panel's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import SlotOutcome

if TYPE_CHECKING:
    from domain.artwork_slot_choice import ArtworkSlotChoice
    from domain.artwork_slot_outcome import ArtworkSlotOutcome


class SlotState(StrEnum):
    """Who decides a slot's image: Tender's rule, the user's choice, or an image set through Steam."""

    AUTOMATIC = "automatic"
    CHOSEN = "chosen"
    SET_IN_STEAM = "set_in_steam"


class SlotSource(StrEnum):
    """Where the image a slot shows comes from, or why it shows none."""

    ROMM = "romm"
    CHOSEN = "chosen"
    TENDER = "tender"
    STEAM = "steam"
    NEEDS_GAME = "needs_game"
    NONE = "none"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class SlotView:
    """One slot as the tab shows it; *image_id* is the SteamGridDB image it shows, where Tender knows it.

    *signature* changes whenever a file filling the slot in Steam's grid
    directory does, so the slot's thumbnail can be loaded anew for that slot
    alone; empty where no file fills it.
    """

    slot: ArtworkSlot
    state: SlotState
    source: SlotSource
    image_id: int | None
    signature: str = ""


def describe_slot(
    slot: ArtworkSlot,
    *,
    set_in_steam: bool,
    choice: ArtworkSlotChoice | None,
    recorded: ArtworkSlotOutcome | None,
    has_game: bool,
    filled: bool,
    cached_image_id: int | None,
) -> SlotView:
    """The tab's view of *slot*.

    A slot set in Steam is shown so whatever was chosen for it, then a chosen
    slot, then an automatic one: a *filled* cover's automatic image is RomM's,
    unless it carries a ``set`` record — Tender's pick from SteamGridDB for a
    game RomM has no cover for, the only cover image recorded without a
    choice; an empty cover is SteamGridDB's to fill (#2381 D27) and reads like
    an empty SteamGridDB slot below; a SteamGridDB slot without a game needs
    one; a *filled* slot shows Tender's image, the one its ``set`` record
    names, else *cached_image_id*; an empty one whose record is ``none`` has
    nothing on SteamGridDB, and any other is not filled yet.
    """
    if set_in_steam:
        return SlotView(slot, SlotState.SET_IN_STEAM, SlotSource.STEAM, None)
    if choice is not None:
        return SlotView(slot, SlotState.CHOSEN, SlotSource.CHOSEN, choice.image_id)
    recorded_id = recorded.image_id if recorded is not None and recorded.outcome is SlotOutcome.SET else None
    if slot is ArtworkSlot.COVER and filled:
        if recorded_id is not None:
            return SlotView(slot, SlotState.AUTOMATIC, SlotSource.TENDER, recorded_id)
        return SlotView(slot, SlotState.AUTOMATIC, SlotSource.ROMM, None)
    if not has_game:
        return SlotView(slot, SlotState.AUTOMATIC, SlotSource.NEEDS_GAME, None)
    if filled:
        return SlotView(slot, SlotState.AUTOMATIC, SlotSource.TENDER, recorded_id or cached_image_id)
    if recorded is not None and recorded.outcome is SlotOutcome.NONE:
        return SlotView(slot, SlotState.AUTOMATIC, SlotSource.NONE, None)
    return SlotView(slot, SlotState.AUTOMATIC, SlotSource.EMPTY, None)
