"""The five slots of a shortcut's artwork, and when Steam's grid directory already fills one.

Owns the slot vocabulary the per-slot fetch, its record and a user's choices
share, and the rule that decides a wide grid, logo or hero is taken: a slot
that already has an image is left alone, whoever set it
(docs/architecture/steam-non-steam-shortcuts.md, "Game page artwork"). The
icon's rule reads ``shortcuts.vdf`` instead, and is
:func:`domain.shortcut_icon.is_icon_filled`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class ArtworkSlot(StrEnum):
    """A slot of a shortcut's artwork.

    The four SteamGridDB fills are spelled as Tender's asset-type name
    (``domain.sgdb_artwork``); the cover, which the sync takes from RomM, is
    ``cover``.
    """

    WIDE_GRID = "grid"
    LOGO = "logo"
    HERO = "hero"
    ICON = "icon"
    COVER = "cover"


# The slots whose outcome is recorded: the four Tender fills from SteamGridDB.
# The cover's record holds no outcome until a cover can be chosen from there.
RECORDED_SLOTS: frozenset[ArtworkSlot] = frozenset(
    {ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO, ArtworkSlot.HERO, ArtworkSlot.ICON}
)

# The extension Steam keeps the logo's position under, beside the logo itself.
_NOT_AN_IMAGE_EXTENSION = "json"

# The one extension Tender's own images have in the grid directory: it hands
# Steam every image as PNG (SetCustomArtworkForApp's "png", and the cover's
# grid copy).
_TENDERS_EXTENSION = "png"


def slot_stem(app_id: int, slot: ArtworkSlot) -> str:
    """The name Steam keeps *app_id*'s *slot* under in its grid directory, without the extension."""
    if slot is ArtworkSlot.WIDE_GRID:
        return str(app_id)
    if slot is ArtworkSlot.COVER:
        return f"{app_id}p"
    return f"{app_id}_{slot.value}"


def is_slot_filled(file_names: Iterable[str], app_id: int, slot: ArtworkSlot) -> bool:
    """Whether *file_names* — the regular files in Steam's grid directory — fill *app_id*'s *slot*.

    A file fills it when its name is exactly the slot's name — ``<appid>`` for
    the wide grid, ``<appid>_logo``, ``<appid>_hero`` — plus one extension, any
    but ``.json``. The cover (``<appid>p``) and a file named by the 64-bit game
    id never match. Why any extension counts is under "When a slot is filled"
    in docs/architecture/steam-non-steam-shortcuts.md.
    """
    return bool(_slot_extensions(file_names, app_id, slot))


def is_set_in_steam(
    file_names: Iterable[str],
    app_id: int,
    slot: ArtworkSlot,
    *,
    png_sha256: str | None,
    tenders_sha256: str | None,
) -> bool:
    """Whether Steam's grid directory holds an image for *app_id*'s *slot* that is not Tender's.

    *file_names* are the directory's regular files, as :func:`is_slot_filled`
    reads them. A file filling the slot under any extension but ``png`` is not
    Tender's: Steam keeps an image set through its own "Set custom artwork"
    under the source file's extension. The ``<stem>.png`` is not Tender's when
    *png_sha256*, the hash of its bytes, differs from *tenders_sha256*, the
    hash of what Tender set — the slot's recorded hash, else Tender's cached
    copy. Where either hash is ``None`` there is nothing to compare, and the
    ``.png`` is taken as Tender's. Raises ``ValueError`` for the icon, which is
    judged by its path (:func:`domain.shortcut_icon.is_icon_set_in_steam`).
    """
    if slot is ArtworkSlot.ICON:
        raise ValueError("the icon is judged by its path in shortcuts.vdf")
    extensions = _slot_extensions(file_names, app_id, slot)
    if any(extension != _TENDERS_EXTENSION for extension in extensions):
        return True
    if _TENDERS_EXTENSION not in extensions or png_sha256 is None or tenders_sha256 is None:
        return False
    return png_sha256 != tenders_sha256


def _slot_extensions(file_names: Iterable[str], app_id: int, slot: ArtworkSlot) -> set[str]:
    """The extensions of the files filling *app_id*'s *slot*, as :func:`is_slot_filled` counts them."""
    prefix = slot_stem(app_id, slot) + "."
    extensions: set[str] = set()
    for name in file_names:
        if not name.startswith(prefix):
            continue
        extension = name[len(prefix) :]
        if extension and "." not in extension and extension.lower() != _NOT_AN_IMAGE_EXTENSION:
            extensions.add(extension)
    return extensions
