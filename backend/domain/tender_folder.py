"""Tender's folder — where it lies, when it counts as unreachable, and which places a user may choose for it.

Tender keeps its own files in one folder named :data:`FOLDER_NAME`, put inside
the place the user chose (GLOSSARY.md, "Tender's folder"). The place is a
setting: no choice means the internal default, the home folder; a choice
holds the place and the mount point it lay on when it was chosen, so a drive
that is gone is told by its mount point rather than by a missing folder.

Each store answers for itself what it has recorded under a folder; this
module only judges the answers. The folder is unreachable only while
something is recorded under it — with nothing recorded, a folder that is
missing is created again by the next write.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.refusal import DomainRefused

if TYPE_CHECKING:
    from collections.abc import Iterable

# The folder's own name, inside every place: the visible library folder takes
# the display name, unlike the hidden folders that take the identifier.
FOLDER_NAME = "Tender"

# The file Tender writes into every folder it creates, holding the id it
# records beside it, so a folder can be told apart as Tender's own.
FOLDER_ID_FILENAME = ".tender-folder"

# Where the place is stored in ``settings.json``.
PLACE_SETTING = "tender_folder"

# Why a folder cannot be reached, in the order they are asked.
DRIVE_NOT_MOUNTED = "drive_not_mounted"
FOLDER_MISSING = "folder_missing"
FOLDER_NOT_WRITABLE = "folder_not_writable"

_STATE_FRAGMENTS = {
    DRIVE_NOT_MOUNTED: "the drive is not connected.",
    FOLDER_MISSING: "the folder is missing.",
    FOLDER_NOT_WRITABLE: "the folder cannot be written to.",
}

# The reasons a choice of place is refused with.
PLACE_IN_ROM_FOLDER = "place_in_rom_folder"
PLACE_ABOVE_ROM_FOLDER = "place_above_rom_folder"
PLACE_NOT_WRITABLE = "place_not_writable"
PLACE_NOT_ABSOLUTE = "place_not_absolute"
PLACE_DRIVE_NOT_MOUNTED = "place_drive_not_mounted"
SOURCES_UNREADABLE = "sources_unreadable"


@dataclass(frozen=True, slots=True)
class TenderPlace:
    """Where Tender's folder lies: the place, the mount point it lay on when chosen, and whether it is the default.

    ``mount_point`` is ``None`` for the default, which lies on the internal
    drive and has no drive to be gone.
    """

    place: str
    mount_point: str | None
    default: bool

    @property
    def folder(self) -> str:
        """The folder itself, :data:`FOLDER_NAME` inside the place."""
        return os.path.join(self.place, FOLDER_NAME)


@dataclass(frozen=True, slots=True)
class Drive:
    """A writable drive a place may be offered on: its folder name, its mount point and its free bytes."""

    name: str
    mount_point: str
    free_bytes: int


@dataclass(frozen=True, slots=True)
class SourceRomFolders:
    """One detected emulator source's ROM folder and the folder of each system it names, all symlink-resolved.

    ``rom_root`` is ``None`` where the resolver states none for the source.
    ``system_folders`` pairs a system with its folder.
    """

    kind: str
    rom_root: str | None
    system_folders: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PlaceAsks:
    """What the user is asked before a typed place is chosen.

    ``tender_parent`` is the folder above a typed place whose last folder is
    named like :data:`FOLDER_NAME`, offered instead of it, and ``None``
    otherwise. ``existing`` is the deepest folder of the place that exists,
    and ``missing`` the part below it still to be created, ``None`` where the
    whole place exists.
    """

    place: str
    tender_parent: str | None
    existing: str
    missing: str | None


def read_place(value: object, home: str) -> TenderPlace:
    """The place the stored *value* names; the default, inside *home*, where it names no valid choice.

    A choice is an object with an absolute ``place`` and ``mount_point``;
    anything else — no choice, or one a hand edit broke — is the default.
    """
    if isinstance(value, dict):
        place, mount_point = value.get("place"), value.get("mount_point")
        if (
            isinstance(place, str)
            and isinstance(mount_point, str)
            and os.path.isabs(place)
            and os.path.isabs(mount_point)
        ):
            return TenderPlace(place=os.path.normpath(place), mount_point=os.path.normpath(mount_point), default=False)
    return TenderPlace(place=os.path.normpath(home), mount_point=None, default=True)


def place_setting(place: str, mount_point: str, home: str) -> dict[str, str] | None:
    """The value stored for a choice of *place* on *mount_point*: none for the default place, so a reset restores it."""
    if os.path.normpath(place) == os.path.normpath(home):
        return None
    return {"place": os.path.normpath(place), "mount_point": os.path.normpath(mount_point)}


def typed_place(text: str, home: str) -> str:
    """The absolute place a typed *text* names: ``~`` stands for *home*, anything else starts with ``/``.

    Refuses :data:`PLACE_NOT_ABSOLUTE` otherwise, a ``~user`` included.
    """
    stripped = text.strip()
    if stripped == "~" or stripped.startswith("~/"):
        return os.path.normpath(os.path.join(home, stripped[2:]))
    if stripped.startswith("/"):
        return os.path.normpath(stripped)
    raise DomainRefused(PLACE_NOT_ABSOLUTE, "Enter a full path starting with / or ~.")


def tender_parent(place: str) -> str | None:
    """The folder above *place* where its last folder is named like :data:`FOLDER_NAME` in any case, else ``None``."""
    if os.path.basename(place).casefold() != FOLDER_NAME.casefold():
        return None
    return os.path.dirname(place)


def place_asks(place: str, existing: str) -> PlaceAsks:
    """What to ask about *place*, whose deepest existing folder is *existing*."""
    missing = os.path.relpath(place, existing)
    return PlaceAsks(
        place=place,
        tender_parent=tender_parent(place),
        existing=existing,
        missing=None if missing == os.curdir else missing,
    )


def drive_below(place: str, drives_root: str) -> str | None:
    """The name of the drive folder directly under *drives_root* that *place* lies in or is, else ``None``."""
    root = os.path.normpath(drives_root)
    if not inside(place, root) or place == root:
        return None
    return os.path.relpath(place, root).split(os.sep)[0]


def drive_not_mounted(name: str) -> DomainRefused:
    """The refusal of a place on the drive *name*, whose folder under the drives root is not a mount."""
    return DomainRefused(PLACE_DRIVE_NOT_MOUNTED, f"The drive {name} is not connected.", drive=name)


def not_writable(place: str) -> DomainRefused:
    """The refusal of a *place* the folder could not be created or written in."""
    return DomainRefused(PLACE_NOT_WRITABLE, f"{place} cannot be written to.", path=place)


def sources_unreadable() -> DomainRefused:
    """The refusal of a place while the emulator sources could not be read."""
    return DomainRefused(
        SOURCES_UNREADABLE, "Tender could not read the emulator sources, so it cannot check this place."
    )


def shown_place(text: str) -> str:
    """The place as the user typed it, without a trailing slash, for a sentence."""
    stripped = text.strip()
    return stripped.rstrip("/") or "/"


def shown_folder(text: str) -> str:
    """Tender's folder at the place the user typed, as a sentence names it: the place as typed, then ``/Tender``."""
    place = shown_place(text)
    return f"{place.rstrip('/')}/{FOLDER_NAME}"


def rom_folder_refusal(folder: str, sources: Iterable[SourceRomFolders], shown: str) -> DomainRefused | None:
    """Why *folder* may not be Tender's folder because of an emulator source's ROM folders, or ``None``.

    *folder* and every source folder are symlink-resolved; *shown* is the
    folder as the sentence names it, the place as typed with ``/Tender``. A
    folder that would lie inside a source's ROM folder, or hold one, is
    refused, and so is one inside a folder the source names for a system,
    which may be linked to another drive. The message names the source by
    its kind; the panel words it with the source's name.
    """
    for source in sources:
        root = source.rom_root
        if root is not None and inside(folder, root):
            return DomainRefused(
                PLACE_IN_ROM_FOLDER,
                f"{shown} lies inside {source.kind}'s ROM folder ({root}). Choose a place outside it.",
                path=shown,
                source=source.kind,
                folder=root,
                system=None,
            )
        if root is not None and inside(root, folder):
            return DomainRefused(
                PLACE_ABOVE_ROM_FOLDER,
                f"{source.kind}'s ROM folder ({root}) lies inside {shown}. Choose another place.",
                path=shown,
                source=source.kind,
                folder=root,
            )
        for system, system_folder in source.system_folders:
            if inside(folder, system_folder):
                return DomainRefused(
                    PLACE_IN_ROM_FOLDER,
                    f"{shown} lies inside {source.kind}'s folder for {system} ({system_folder}). "
                    "Choose a place outside it.",
                    path=shown,
                    source=source.kind,
                    folder=system_folder,
                    system=system,
                )
    return None


def unreachable_state(folder: str, recorded: Iterable[str], condition: str | None) -> str | None:
    """Why *folder* is unreachable, or ``None``: its *condition*, only while something is *recorded* under it."""
    return condition if folder in set(recorded) else None


def recorded_elsewhere(folder: str, recorded: Iterable[str]) -> tuple[str, ...]:
    """The folders something is *recorded* under other than *folder*, sorted."""
    return tuple(sorted(set(recorded) - {folder}))


def unreachable_message(folder: str, state: str) -> str:
    """The sentence an action refused because *folder* cannot be reached for *state* is answered with."""
    return f"Tender's folder at {folder} cannot be reached: {_STATE_FRAGMENTS[state]}"


def inside(path: str, root: str) -> bool:
    """Whether *path* is *root* or lies below it; both normalized the same way."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)
