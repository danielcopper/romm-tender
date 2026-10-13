"""The artwork slots Tender fills from SteamGridDB, and when Steam's grid directory already fills one.

Owns the slot vocabulary the per-slot fetch and its record share, and the rule
that decides a slot is taken: a slot that already has an image is left alone,
whoever set it (docs/architecture/steam-non-steam-shortcuts.md, "Game page
artwork").
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class ArtworkSlot(StrEnum):
    """A slot of a shortcut's artwork, spelled as SteamGridDB's asset type."""

    WIDE_GRID = "grid"
    LOGO = "logo"
    HERO = "hero"


# The slots whose outcome is recorded; the hero is the game page's alone.
RECORDED_SLOTS: frozenset[ArtworkSlot] = frozenset({ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO})

# The extension Steam keeps the logo's position under, beside the logo itself.
_NOT_AN_IMAGE_EXTENSION = "json"


def _slot_stem(app_id: int, slot: ArtworkSlot) -> str:
    if slot is ArtworkSlot.WIDE_GRID:
        return str(app_id)
    return f"{app_id}_{slot.value}"


def is_slot_filled(file_names: Iterable[str], app_id: int, slot: ArtworkSlot) -> bool:
    """Whether *file_names* — the regular files in Steam's grid directory — fill *app_id*'s *slot*.

    A file fills it when its name is exactly the slot's name — ``<appid>`` for
    the wide grid, ``<appid>_logo``, ``<appid>_hero`` — plus one extension, any
    but ``.json``. Which image formats Steam reads there is not documented
    beyond PNG and JPEG, and missing one set by hand would put a second image
    beside it. The cover (``<appid>p``) and a file named by the 64-bit game id
    never match.
    """
    prefix = _slot_stem(app_id, slot) + "."
    for name in file_names:
        if not name.startswith(prefix):
            continue
        extension = name[len(prefix) :]
        if extension and "." not in extension and extension.lower() != _NOT_AN_IMAGE_EXTENSION:
            return True
    return False
