"""When a game's automatic artwork is checked against SteamGridDB again, and which slots the check covers.

Owns the seven-day window of keeping current and the rule that picks the slots
a check asks about (docs/architecture/steam-non-steam-shortcuts.md, "Keeping
automatic artwork current"). Asking SteamGridDB, recording the check and
replacing an image are the service's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.artwork_slot_view import SlotSource, SlotState

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.artwork_slot_view import SlotView

# How long a check keeps a game from being checked again; #2381 D5 weighs it
# against SteamGridDB's throttling.
KEEP_CURRENT_SECONDS = 7 * 24 * 60 * 60


def is_check_due(checked_at: float | None, now: float) -> bool:
    """Whether a game last checked at *checked_at* (epoch seconds, ``None`` for never) is checked again at *now*.

    A time after *now* — a clock that was set wrong when the check was
    recorded — counts as no check, so the game is not left unchecked until
    that time comes.
    """
    if checked_at is None:
        return True
    return not 0 <= now - checked_at < KEEP_CURRENT_SECONDS


def slots_kept_current(views: Iterable[SlotView]) -> list[SlotView]:
    """The views among *views* a check asks SteamGridDB about: automatic, and showing Tender's SteamGridDB image.

    That leaves out a slot the user chose, one set in Steam, the cover RomM's
    image fills, and an empty slot, which the game page's fill and the
    shortcut artwork job give an image.
    """
    return [view for view in views if view.state is SlotState.AUTOMATIC and view.source is SlotSource.TENDER]
