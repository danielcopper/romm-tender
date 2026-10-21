"""When a shortcut's icon slot is filled, and the two icons that are Tender's own.

With a SteamGridDB API key, a shortcut the sync mints is given
:data:`PLACEHOLDER_ICON_PNG` at once, and the artwork job later replaces it with
SteamGridDB's icon, or with Tender's logo icon where SteamGridDB has none; why is
docs/architecture/steam-non-steam-shortcuts.md, "Shortcut artwork". Both are
stand-ins that leave the slot unfilled: whether the job asks SteamGridDB again
is the slot's recorded outcome (:func:`domain.artwork_slot_outcome.icon_waits`),
not which of the two the shortcut shows. Which one it shows decides only whether
the logo icon a recorded ``none`` stands for is still to be given
(:func:`domain.artwork_slot_outcome.logo_icon_waits`).
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

PLACEHOLDER_ICON_NAME = "tender-icon-placeholder.png"
"""The placeholder's file name in Steam's grid directory."""

LOGO_ICON_NAME = "tender-icon.png"
"""The logo icon's file name in Steam's grid directory, and in ``defaults/``, where it ships."""

PLACEHOLDER_ICON_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b4944415478016360000200000500014e0ba8660000000049454e44ae426082"
)
"""A 1x1 PNG whose one pixel is fully transparent: an empty spot in the list, not a grey box."""

_STAND_IN_NAMES = frozenset({"", PLACEHOLDER_ICON_NAME, LOGO_ICON_NAME})
_NO_ICON_NAMES = frozenset({"", PLACEHOLDER_ICON_NAME})


def is_icon_filled(icon: str, grid_dir: str, file_names: Collection[str] | None) -> bool:
    """Whether *icon*, a shortcut's icon path as ``shortcuts.vdf`` holds it, fills its icon slot.

    An empty path, and a file named :data:`PLACEHOLDER_ICON_NAME` or
    :data:`LOGO_ICON_NAME` in any directory, are stand-ins and fill nothing. A
    path in *grid_dir*, Steam's grid directory, fills the slot only while its
    file is among *file_names*, that directory's listing; where the listing is
    ``None`` it could not be taken, and the path is left alone as filled. Any
    other path fills it whether its file is there or not, so an icon on a drive
    that is not mounted is kept.
    """
    directory, _, name = icon.rpartition("/")
    if name in _STAND_IN_NAMES:
        return False
    if posixpath.normpath(directory) != posixpath.normpath(grid_dir):
        return True
    return file_names is None or name in file_names


def is_icon_set_in_steam(icon: str, app_id: int, grid_dir: str) -> bool:
    """Whether *icon*, *app_id*'s icon path in ``shortcuts.vdf``, is an icon that is not Tender's.

    Tender's are the two stand-ins, by name in any directory, and the icon it
    writes as ``<app_id>_icon.png`` in *grid_dir*; an empty path is no icon.
    Any other path was set some other way, through Steam's own properties or
    another tool.
    """
    directory, _, name = icon.rpartition("/")
    if name in _STAND_IN_NAMES:
        return False
    return not (name == f"{app_id}_icon.png" and posixpath.normpath(directory) == posixpath.normpath(grid_dir))


def shows_no_icon(icon: str) -> bool:
    """Whether *icon*, a shortcut's icon path in ``shortcuts.vdf``, is empty or the placeholder, in any directory."""
    return icon.rpartition("/")[2] in _NO_ICON_NAMES
