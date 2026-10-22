"""One platform's BIOS rows: the resolver's requirements, what fills each, and what lies at each place.

A row is one requirement of the shown emulator — or, for a folder it lists, one
file in that folder — and a library file of the platform that fills no
requirement, or a store copy that fills none, is a row of the library's own
section. The resolver's verdict is each row's as it stands; what this module
adds is Tender's own: the library file that fills the row by its content, this
platform's store copy, what lies at the place and whether "Replace…" is offered
(:mod:`domain.firmware_rows`).

The functions here take a look at every place they name, so they run off the
event loop, inside the worker hop that read the catalogue.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from domain.bios_status import BiosFileEntry, build_file_entry
from domain.firmware_groups import fetched_as_required
from domain.firmware_match import MATCHED_CONTENT
from domain.firmware_places import REFUSED_NOT_INSTALLED, PlaceRefusal, place_refusal
from domain.firmware_rows import (
    LibraryFirmware,
    OwnedLinks,
    StoreCopy,
    folder_entries,
    folder_place,
    library_match,
    library_payload,
    place_facts,
    store_copy,
    unclaimed,
    unused,
)
from domain.firmware_wants import CHECKED_UNKNOWN, CHECKED_UNRECOGNISED, CHECKED_VERIFIED, DECLARED_FILE

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.firmware_places import PlaceLook
    from domain.firmware_wants import FirmwareCatalogue, FirmwarePlacement, FolderFile
    from services.protocols import FirmwareFileStore


@dataclass
class RowInputs:
    """Everything one platform's rows are built from, read once for the whole page.

    ``library`` is every file the RomM library lists, whichever platform it is
    filed under — a file fills a requirement wherever it lies — and
    ``copies`` this platform's store files. ``looks`` keeps one look per place,
    so a place two rows name is looked at once.
    """

    catalogue: FirmwareCatalogue
    platform_slug: str
    library: tuple[LibraryFirmware, ...]
    copies: tuple[StoreCopy, ...]
    owned: OwnedLinks
    files: FirmwareFileStore
    looks: dict[str, PlaceLook] = field(default_factory=dict)

    def look(self, place: str) -> PlaceLook:
        if place not in self.looks:
            self.looks[place] = self.files.look(place)
        return self.looks[place]


def requirement_rows(
    inputs: RowInputs, placements: Iterable[FirmwarePlacement], emulator: str | None
) -> list[BiosFileEntry]:
    """The rows for *placements* as the emulator *emulator* reads them, a folder as one row per file in it."""
    complete = inputs.catalogue.reading_complete_for(emulator)
    rows: list[BiosFileEntry] = []
    for placement in placements:
        if placement.declares_directory:
            rows.extend(_folder_rows(inputs, placement, emulator, complete))
        else:
            rows.append(_file_row(inputs, placement, emulator, complete))
    return rows


def library_rows(inputs: RowInputs, emulator: str | None) -> list[BiosFileEntry]:
    """The library section: this platform's library files and store copies that fill no requirement here.

    A library file whose store copy lies in the store is one row; a store copy
    RomM no longer lists is a row of its own.
    """
    placements = inputs.catalogue.placements
    complete = inputs.catalogue.reading_complete_for(emulator)
    mine = [file for file in inputs.library if file.platform_slug == inputs.platform_slug]
    spare = {copy.file_name: copy for copy in unused(inputs.copies, placements)}
    rows = []
    for file in unclaimed(mine, placements):
        copy = spare.pop(file.file_name, None)
        entry = build_file_entry(file.file_name, copy is not None, None, None, complete, emulator)
        rows.append(
            entry.with_changes(
                library=library_payload(file, None), store_file=copy.file_name if copy is not None else None
            )
        )
    for copy in spare.values():
        entry = build_file_entry(copy.file_name, True, None, None, complete, emulator, on_server=False)
        rows.append(entry.with_changes(store_file=copy.file_name))
    return rows


def required_folders(catalogue: FirmwareCatalogue, emulator: str | None) -> tuple[bool | None, ...]:
    """The verdicts over the folders *emulator* requires and lists, each one requirement in the counts."""
    if emulator is None:
        return ()
    return tuple(
        placement.satisfied
        for placement in catalogue.placements
        if placement.emulator == emulator
        and placement.declares_directory
        and any(want.emulator == emulator and want.required for want in placement.wants)
    )


def _file_row(inputs: RowInputs, placement: FirmwarePlacement, emulator: str | None, complete: bool) -> BiosFileEntry:
    catalogue = inputs.catalogue
    place = placement.place
    look = inputs.look(place) if place is not None else None
    library = library_match(placement, inputs.library)
    entry = build_file_entry(
        placement.file_name,
        placement.present is True,
        place,
        placement,
        complete,
        emulator,
        on_server=library is not None,
        groups=catalogue.groups,
    )
    facts = place_facts(
        place=place,
        look=look,
        refusal=_refusal(catalogue, placement, place, look),
        library=library,
        copy=store_copy(placement, inputs.copies),
        platform_slug=inputs.platform_slug,
        source=catalogue.source or "",
        owned=inputs.owned,
        satisfied=placement.satisfied,
    )
    return entry.with_changes(**facts)


def _refusal(
    catalogue: FirmwareCatalogue, placement: FirmwarePlacement, place: str | None, look: PlaceLook | None
) -> PlaceRefusal | None:
    """Why no link goes to *place*: first that the source states the emulator is not installed, then the place's own."""
    if placement.emulator in catalogue.not_installed:
        return PlaceRefusal(REFUSED_NOT_INSTALLED)
    return place_refusal(place, look, catalogue.roots, unreadable=placement.place_unreadable)


def _folder_rows(
    inputs: RowInputs, placement: FirmwarePlacement, emulator: str | None, complete: bool
) -> list[BiosFileEntry]:
    """One row per file the folder declaration takes: each recognised library file or store copy, and each other file.

    Where that leaves no row, one row stands for the folder itself
    (:func:`_empty_folder_row`).

    Each row's verdict is the resolver's over that file. A recognised file not
    in the folder yet is not in place; one that is but drew no statement is not
    established. "Download required" fetches the alphabetically first
    recognised library file while the folder's own verdict is not met — one
    image is all the emulator opens. Every other file the resolver stated
    something about is a row too, with its verdict and nothing to offer.
    """
    named: set[str] = set()
    rows = _recognised_folder_rows(inputs, placement, emulator, complete, named)
    rows += _stated_folder_rows(inputs, placement, emulator, complete, named)
    if not rows:
        rows.append(_empty_folder_row(inputs, placement, emulator, complete))
    return rows


def _recognised_folder_rows(
    inputs: RowInputs, placement: FirmwarePlacement, emulator: str | None, complete: bool, named: set[str]
) -> list[BiosFileEntry]:
    """The rows of the library files and store copies recognised for the folder; their places go into *named*."""
    catalogue = inputs.catalogue
    statements = {file.path: file for file in placement.folder_files}
    wanted = fetched_as_required(placement, emulator, ()) and placement.satisfied is not True
    rows: list[BiosFileEntry] = []
    for name, file, copy in folder_entries(placement, inputs.library, inputs.copies):
        place = folder_place(placement, name)
        if place is None:
            continue
        named.add(place)
        look = inputs.look(place)
        statement = statements.get(place)
        satisfied = _folder_file_verdict(statement, look)
        library = library_payload(file, MATCHED_CONTENT) if file is not None else None
        entry = _in_folder(
            build_file_entry(
                name, look.lexists, place, placement, complete, emulator, on_server=file is not None, groups=()
            ),
            placement,
            name,
            satisfied,
            statement.description if statement is not None else None,
            checked=_folder_file_checked(satisfied) if statement is not None or look.lexists else None,
            fetch=wanted and file is not None,
        )
        wanted = wanted and file is None
        facts = place_facts(
            place=place,
            look=look,
            refusal=_refusal(catalogue, placement, place, look),
            library=library,
            copy=copy,
            platform_slug=inputs.platform_slug,
            source=catalogue.source or "",
            owned=inputs.owned,
            satisfied=satisfied,
        )
        rows.append(entry.with_changes(**facts))
    return rows


def _folder_file_checked(satisfied: bool | None) -> str:
    """What a file in a declared folder's contents read as, in the ``checked`` words its Contents cell is worded by.

    The resolver states a verdict per file and no check state of its own:
    identified by its contents is the right file, a file the read found no
    image among is not recognised, and anything else is not established.
    """
    if satisfied is True:
        return CHECKED_VERIFIED
    if satisfied is False:
        return CHECKED_UNRECOGNISED
    return CHECKED_UNKNOWN


def _folder_file_verdict(statement: FolderFile | None, look: PlaceLook) -> bool | None:
    """A recognised file's verdict in the folder: the resolver's, else not established where it lies there."""
    if statement is not None:
        return statement.satisfied
    if look.lexists:
        return None
    return False


def _stated_folder_rows(
    inputs: RowInputs, placement: FirmwarePlacement, emulator: str | None, complete: bool, named: set[str]
) -> list[BiosFileEntry]:
    """The rows of every other file in the folder the resolver stated something about, none of *named*."""
    rows: list[BiosFileEntry] = []
    for statement in placement.folder_files:
        if statement.path in named or (statement.target or "") in inputs.owned.store_targets:
            continue
        name = os.path.basename(statement.path)
        entry = _in_folder(
            build_file_entry(name, True, statement.path, placement, complete, emulator, on_server=False, groups=()),
            placement,
            name,
            statement.satisfied,
            statement.description,
            checked=_folder_file_checked(statement.satisfied),
            fetch=False,
        )
        facts = place_facts(
            place=statement.path,
            look=inputs.look(statement.path),
            refusal=None,
            library=None,
            copy=None,
            platform_slug=inputs.platform_slug,
            source=inputs.catalogue.source or "",
            owned=inputs.owned,
            satisfied=statement.satisfied,
        )
        rows.append(entry.with_changes(**facts))
    return rows


def _empty_folder_row(
    inputs: RowInputs, placement: FirmwarePlacement, emulator: str | None, complete: bool
) -> BiosFileEntry:
    """The one row standing for a folder declaration nothing lies in and no library file is recognised for.

    It carries the resolver's verdict over the folder and the codes it states
    about it; it offers nothing to fetch or delete, and goes as soon as a file
    of the folder has a row of its own. The folder counts as one requirement
    through :func:`required_folders`, so the row adds none.
    """
    entry = build_file_entry(
        placement.file_name,
        placement.present is True,
        placement.place,
        placement,
        complete,
        emulator,
        on_server=False,
        groups=inputs.catalogue.groups,
    )
    return entry.with_changes(satisfied=placement.satisfied, required_by_active=False, fetch_for_required=False)


def _in_folder(
    entry: BiosFileEntry,
    placement: FirmwarePlacement,
    name: str,
    satisfied: bool | None,
    description: str | None,
    *,
    checked: str | None,
    fetch: bool,
) -> BiosFileEntry:
    """*entry* as one file in the folder *placement* declares: a file of its own, required by nothing alone.

    Its ``checked`` is the file's own, never the folder's.
    """
    return entry.with_changes(
        declared_path=os.path.join(placement.destination, name),
        declared_kind=DECLARED_FILE,
        required_by_active=False,
        satisfied=satisfied,
        checked=checked,
        images=(description,) if description else (),
        caveats=(),
        fetch_for_required=fetch,
        in_folder=placement.destination,
    )
