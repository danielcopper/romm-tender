"""Which shortcuts the icon job works on, and the two icons that are Tender's own.

A shortcut the sync creates is given :data:`PLACEHOLDER_ICON_PNG` at once, and
the job later replaces it with SteamGridDB's icon, or with Tender's logo where
SteamGridDB has none; why is docs/architecture/steam-non-steam-shortcuts.md,
"Shortcut icons". The placeholder's fixed path is how the job tells a shortcut
still waiting from one whose icon somebody set; the logo, like that icon, is
left alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

PLACEHOLDER_ICON_NAME = "tender-icon-placeholder.png"
"""The placeholder's file name in Steam's grid directory."""

LOGO_ICON_NAME = "tender-icon.png"
"""The logo's file name in Steam's grid directory, and in ``defaults/``, where it ships."""

PLACEHOLDER_ICON_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b4944415478016360000200000500014e0ba8660000000049454e44ae426082"
)
"""A 1x1 PNG whose one pixel is fully transparent: an empty spot in the list, not a grey box."""


def icon_worklist(
    bound: Mapping[int, int],
    icons: Mapping[int, str],
    placeholder_path: str,
) -> list[tuple[int, int]]:
    """The ``(rom_id, app_id)`` pairs whose shortcut still waits for an icon.

    *bound* maps a bound shortcut's app id to its ROM, *icons* each shortcut's
    icon path as ``shortcuts.vdf`` holds it. A shortcut waits while its icon is
    empty or the placeholder. The logo means SteamGridDB had none and is not
    asked again here; any other path is an icon somebody set, left alone. A
    shortcut the file does not hold yet is left for the next run.
    """
    waiting = ("", placeholder_path)
    return sorted((rom_id, app_id) for app_id, rom_id in bound.items() if app_id in icons and icons[app_id] in waiting)
