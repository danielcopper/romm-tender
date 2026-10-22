"""What Tender adds to a BIOS row: the library file that fills it, its store copy, and what lies at its place.

A row is one emulator requirement — or, for a folder the emulator lists, one
file in that folder — and the resolver's verdict over it stands as it is
(:mod:`domain.bios_status`). Beside the verdict a row carries Tender's own
facts, each of which decides what the row offers:

- **the library file that fills it** — by content anywhere in the RomM library,
  by name only where content cannot say (:mod:`domain.firmware_match`), the
  alphabetically first where several do;
- **the store copy that fills it** — this platform's own, in its store folder;
- **what lies at its place** (:func:`domain.firmware_places.link_state`) and
  why a link may not go there (:func:`domain.firmware_places.place_refusal`);
- **whether "Replace…" is offered**: another file lies there, the resolver
  reads it as wrong, and the library copy is proven right by its checksum.

A library file of the platform that fills no requirement of any of its
emulators is listed on its own (:func:`unclaimed`), and so is a store copy
that fills none (:func:`unused`).

Pure compute — no I/O.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.firmware_match import MATCHED_CONTENT, FirmwareFile, fills, fills_any, first_filling
from domain.firmware_places import LINK_OCCUPIED, PlaceLook, PlaceRefusal, WantedLink, link_state

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from domain.firmware_store import FirmwareLink
    from domain.firmware_wants import FirmwarePlacement


@dataclass(frozen=True, slots=True)
class LibraryFirmware(FirmwareFile):
    """One firmware file the RomM library lists: its RomM id and the platform RomM files it under."""

    firmware_id: int | None = None
    platform_slug: str = ""
    size: int = 0


@dataclass(frozen=True, slots=True)
class StoreCopy(FirmwareFile):
    """One file in Tender's firmware store: the platform it lies under, its path, and whether it is on disk."""

    platform_slug: str = ""
    path: str = ""
    present: bool = True


@dataclass(frozen=True, slots=True)
class OwnedLinks:
    """What decides whose a link is: every recorded store path with its platform, the ones on disk, the records."""

    store_targets: Mapping[str, str]
    present: frozenset[str]
    records: Mapping[str, FirmwareLink]


def library_file(firmware: Mapping[str, Any]) -> LibraryFirmware:
    """One row of RomM's firmware listing as the matching knows it."""
    return LibraryFirmware(
        file_name=str(firmware.get("file_name") or ""),
        md5=str(firmware.get("md5_hash") or ""),
        firmware_id=firmware.get("id"),
        platform_slug=str(firmware.get("platform_slug") or ""),
        size=int(firmware.get("file_size_bytes") or 0),
    )


def leaves_its_folder(file_name: str) -> bool:
    """Would the server-supplied *file_name* leave the folder it is joined onto, or name the folder itself?

    Such a name can never lie in the store, so no row is made of it; a
    download of it is refused where the store path is joined.
    """
    normal = os.path.normpath(file_name) if file_name else ""
    return normal in ("", ".", "..") or normal.startswith(f"..{os.sep}") or os.path.isabs(file_name)


def library_match(placement: FirmwarePlacement, library: Iterable[LibraryFirmware]) -> dict[str, Any] | None:
    """The library file that fills *placement*, as a row carries it, or ``None``.

    ``matched`` says how (:mod:`domain.firmware_match`); ``no_checksum`` that
    RomM states none for it, so a download of it could not be checked.
    """
    file = first_filling(placement, library)
    if file is None:
        return None
    return library_payload(file, fills(placement, file))


def store_copy(placement: FirmwarePlacement, copies: Iterable[StoreCopy]) -> StoreCopy | None:
    """This platform's store copy that fills *placement*: the alphabetically first one on disk, or ``None``."""
    return first_filling(placement, (copy for copy in copies if copy.present))


def folder_entries(
    placement: FirmwarePlacement, library: Iterable[LibraryFirmware], copies: Iterable[StoreCopy]
) -> list[tuple[str, LibraryFirmware | None, StoreCopy | None]]:
    """Each file a folder declaration takes, by its name in the folder: the library file and the store copy behind it.

    Every library file the resolver recognises for the folder is one, under
    its name in RomM, and so is every store copy of this platform it recognises
    — a store copy RomM no longer lists keeps its row.
    """
    entries: dict[str, tuple[LibraryFirmware | None, StoreCopy | None]] = {}
    for file in library:
        if fills(placement, file) is not None:
            entries.setdefault(file.file_name, (file, None))
    for copy in copies:
        if copy.present and fills(placement, copy) is not None:
            known, _ = entries.get(copy.file_name, (None, None))
            entries[copy.file_name] = (known, copy)
    return [(name, file, copy) for name, (file, copy) in sorted(entries.items())]


def place_facts(
    *,
    place: str | None,
    look: PlaceLook | None,
    refusal: PlaceRefusal | None,
    library: dict[str, Any] | None,
    copy: StoreCopy | None,
    platform_slug: str,
    source: str,
    owned: OwnedLinks,
    satisfied: bool | None,
) -> dict[str, Any]:
    """Tender's own fields on one row: its place, what fills it, what lies there, and whether Replace is offered.

    *satisfied* is the resolver's verdict over what lies at the place; Replace
    is offered only where that is ``False`` and the library copy is proven
    right by its checksum — never over a folder, never at a place that may not
    be used, which is also every place of an emulator its source states is
    not installed.
    """
    link, linked_for = None, None
    if place is not None and look is not None:
        want = WantedLink(
            place=place,
            target=copy.path if copy is not None else None,
            platform_slug=platform_slug,
            file_name=copy.file_name if copy is not None else None,
            source=source,
        )
        link, linked_for = link_state(
            want, look, store_targets=owned.store_targets, present=owned.present, records=owned.records
        )
    replace = (
        link == LINK_OCCUPIED
        and refusal is None
        and satisfied is False
        and look is not None
        and not look.is_dir
        and library is not None
        and library["matched"] == MATCHED_CONTENT
    )
    return {
        "local_path": place,
        "library": library,
        "store_file": copy.file_name if copy is not None else None,
        "link": link,
        "linked_for": linked_for if linked_for != platform_slug else None,
        "place_refusal": (
            {"reason": refusal.reason, "folder": refusal.folder, "source": source} if refusal is not None else None
        ),
        "replace_offered": replace,
    }


def unclaimed(library: Iterable[LibraryFirmware], placements: Sequence[FirmwarePlacement]) -> list[LibraryFirmware]:
    """The library files that fill no requirement of *placements*, by name."""
    return sorted((file for file in library if not fills_any(placements, file)), key=lambda f: f.file_name.casefold())


def unused(copies: Iterable[StoreCopy], placements: Sequence[FirmwarePlacement]) -> list[StoreCopy]:
    """The store copies on disk that fill no requirement of *placements*, by name."""
    return sorted(
        (copy for copy in copies if copy.present and not fills_any(placements, copy)),
        key=lambda c: c.file_name.casefold(),
    )


def folder_place(placement: FirmwarePlacement, file_name: str) -> str | None:
    """Where a file named *file_name* lies in the folder *placement* declares, or ``None`` where it names no place."""
    return os.path.join(placement.place, file_name) if placement.place is not None else None


def library_payload(file: LibraryFirmware, matched: str | None) -> dict[str, Any]:
    """*file* as a row carries the library file that fills it, filled the way *matched* says."""
    return {
        "firmware_id": file.firmware_id,
        "file_name": file.file_name,
        "platform_slug": file.platform_slug,
        "matched": matched,
        "md5": file.md5,
        "size": file.size,
        "no_checksum": not file.md5,
    }


def wanted_links(
    placements: Iterable[FirmwarePlacement], platform_slug: str, source: str, copies: Sequence[StoreCopy]
) -> list[tuple[WantedLink, FirmwarePlacement]]:
    """Every place *placements* want a store file of *platform_slug* at, with the copy that fills it, if any.

    A file requirement's place is wanted with the alphabetically first copy on
    disk that fills it, or with none; a folder declaration's with each copy it
    recognises, under that copy's name. The refusal is the caller's to judge,
    since it rests on a look at the place.
    """
    wanted: list[tuple[WantedLink, FirmwarePlacement]] = []
    for placement in placements:
        if placement.declares_directory:
            for name, _file, copy in folder_entries(placement, (), copies):
                place = folder_place(placement, name)
                if place is not None and copy is not None:
                    wanted.append((_want(place, copy, platform_slug, source), placement))
        elif placement.place is not None:
            wanted.append((_want(placement.place, store_copy(placement, copies), platform_slug, source), placement))
    return wanted


def _want(place: str, copy: StoreCopy | None, platform_slug: str, source: str) -> WantedLink:
    return WantedLink(
        place=place,
        target=copy.path if copy is not None else None,
        platform_slug=platform_slug,
        file_name=copy.file_name if copy is not None else None,
        source=source,
    )
