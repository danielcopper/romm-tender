"""One entry of RomM's platform listing (``/api/platforms``), read into the fields Tender takes from it.

Every module that takes a field from a listed platform takes it through
:func:`platform_entry_of`, so each field is checked one way: a text field that
is not a string, or only whitespace, is none; a number that is a boolean or not
a whole number is none, and one sent as a string of digits is that number. A
platform is named by the listing's ``name`` alone, never by RomM's
``display_name``, which prefers a ``custom_name`` set in RomM (#2564 D2).

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

# The fields of a listed platform kept as RomM sent them: a suffix rather than
# a list of fields, so that an identifier RomM adds needs no change here.
_IDENTIFIER_SUFFIXES = ("_id", "_slug")


@dataclass(frozen=True, slots=True)
class PlatformEntry:
    """What Tender reads off one listed platform; ``None`` where the listing gives no usable value.

    ``ids`` holds every field whose name ends in ``_id`` or ``_slug``, each as
    RomM sent it (#2750 D5), and ``firmware`` the entry's list of firmware
    files as sent, empty where it sends no list.
    """

    fs_slug: str | None = None
    slug: str | None = None
    platform_id: int | None = None
    name: str | None = None
    rom_count: int | None = None
    is_identified: bool | None = None
    ids: dict[str, Any] = field(default_factory=dict)
    firmware: tuple[Any, ...] = ()


def platform_entry_of(platform: Mapping[str, Any]) -> PlatformEntry:
    """*platform*, one entry of RomM's platform listing, read field by field; a field of the wrong kind is none."""
    identified = platform.get("is_identified")
    firmware = platform.get("firmware")
    return PlatformEntry(
        fs_slug=listed_text(platform.get("fs_slug")),
        slug=listed_text(platform.get("slug")),
        platform_id=listed_number(platform.get("id")),
        name=listed_text(platform.get("name")),
        rom_count=listed_number(platform.get("rom_count")),
        is_identified=identified if isinstance(identified, bool) else None,
        ids={key: value for key, value in platform.items() if key.endswith(_IDENTIFIER_SUFFIXES)},
        firmware=tuple(firmware) if isinstance(firmware, list) else (),
    )


def platform_entries(platforms: Iterable[Mapping[str, Any]]) -> list[PlatformEntry]:
    """Every entry of *platforms* read through :func:`platform_entry_of`, in the listing's order."""
    return [platform_entry_of(p) for p in platforms]


def folder_without_an_id(platforms: Iterable[object]) -> str | None:
    """The ``fs_slug`` of the first listed platform with one but no usable ``id``; ``None`` where every one has an id.

    Such an entry makes the whole listing unreadable (#2761 D4): leaving out
    only that platform would let a sync read a switched-on platform as gone
    from RomM. An item that is no object has no folder.
    """
    return next(
        (
            entry.fs_slug
            for entry in platform_entries(p for p in platforms if isinstance(p, Mapping))
            if entry.fs_slug is not None and entry.platform_id is None
        ),
        None,
    )


def listed_text(value: Any) -> str | None:
    """*value* where it is a string holding more than whitespace; ``None`` otherwise."""
    return value if isinstance(value, str) and value.strip() else None


def listed_number(value: Any) -> int | None:
    """*value* as a whole number: an ``int``, or a string of ASCII digits; ``None`` for anything else, a boolean too."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None
