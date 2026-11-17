"""One entry of RomM's platform listing (``/api/platforms``), read into the fields Tender takes from it.

Every module that takes a field from a listed platform takes it through
:func:`platform_entry_of` — or, for an identifier kept as RomM sent it,
through the same :func:`listed_text` and :func:`listed_number` — so each field
is checked one way: a text field that is not a string, or only whitespace, is
none; a number that is a boolean or not a whole number is none, and one sent as
a string of digits is that number. A
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


class UnreadableFirmware(Exception):
    """A listed platform's firmware is no list, so what firmware the library holds cannot be read.

    Read as an empty list it would say the platform needs none (#1693). The
    firmware read answers it as an unreadable platform listing; a sync never
    reads the firmware, so it never stops over one.
    """


@dataclass(frozen=True, slots=True)
class PlatformEntry:
    """What Tender reads off one listed platform; ``None`` where the listing gives no usable value.

    ``ids`` holds the identifier fields as RomM sent them (#2750 D5).
    ``firmware`` is the entry's list of firmware files as sent: empty where the
    entry sends none, ``None`` where it sends something that is no list, which
    says nothing about the platform's firmware. ``rom_count`` is ``None`` where
    the count is unknown (#2761 D5): not a whole number, or below zero.
    """

    fs_slug: str | None = None
    slug: str | None = None
    platform_id: int | None = None
    name: str | None = None
    rom_count: int | None = None
    is_identified: bool | None = None
    ids: Mapping[str, Any] = field(default_factory=dict)
    firmware: tuple[Any, ...] | None = ()


def platform_entry_of(platform: Mapping[str, Any]) -> PlatformEntry:
    """*platform*, one entry of RomM's platform listing, read field by field; a field of the wrong kind is none."""
    identified = platform.get("is_identified")
    firmware = platform.get("firmware")
    rom_count = listed_number(platform.get("rom_count"))
    return PlatformEntry(
        fs_slug=listed_text(platform.get("fs_slug")),
        slug=listed_text(platform.get("slug")),
        platform_id=listed_number(platform.get("id")),
        name=listed_text(platform.get("name")),
        rom_count=rom_count if rom_count is not None and rom_count >= 0 else None,
        is_identified=identified if isinstance(identified, bool) else None,
        ids={key: value for key, value in platform.items() if key.endswith(_IDENTIFIER_SUFFIXES)},
        firmware=() if firmware is None else tuple(firmware) if isinstance(firmware, list) else None,
    )


def platform_entries(platforms: Iterable[Mapping[str, Any]]) -> list[PlatformEntry]:
    """Every entry of *platforms* read through :func:`platform_entry_of`, in the listing's order."""
    return [platform_entry_of(p) for p in platforms]


def folder_without_an_id(platforms: Iterable[object]) -> str | None:
    """The ``fs_slug`` of the first listed platform with one but no usable ``id``; ``None`` where every one has an id.

    Such an entry makes the whole listing unreadable (#2761 D4). An item that
    is no object has no folder.
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
