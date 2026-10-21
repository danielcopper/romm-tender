"""Which firmware file fills which emulator requirement — by its content, and by its name only where content cannot say.

A file fills a requirement by its MD5 against the checksum the resolver's table
pins for the requirement (:attr:`FirmwarePlacement.identity_md5`). Its name
decides in two cases only: where the pinned checksum is an archive's, whose
packaging changes the checksum while its contents stay right
(:data:`MATCHED_ARCHIVE_NAME`), and where no table pins a checksum for the
requirement at all, as for Cemu's ``keys.txt`` (:data:`MATCHED_NAME`). Both
compare the declared name regardless of letter case. A folder declaration is
filled by every file whose MD5 the table files under that folder.

The same rule answers for a file in the RomM library and for a file in Tender's
firmware store, since both are known by their name and their MD5. Where several
files fill one requirement, the alphabetically first is the one a link points to
(:func:`first_filling`).

Pure compute — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.firmware_wants import IDENTITY_ARCHIVE, IDENTITY_FILE

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.firmware_wants import FirmwarePlacement

# How a file fills a requirement.
MATCHED_CONTENT = "content"
MATCHED_ARCHIVE_NAME = "archive_name"
MATCHED_NAME = "no_checksum_name"


@dataclass(frozen=True, slots=True)
class FirmwareFile:
    """One firmware file as the matching knows it: its name and its MD5, ``""`` where none is stated."""

    file_name: str
    md5: str


def fills(placement: FirmwarePlacement, file: FirmwareFile) -> str | None:
    """How *file* fills *placement*'s requirement — one of the ``MATCHED_*`` words — or ``None`` where it does not.

    A file with no MD5 fills a requirement only by name. A folder declaration
    takes the file by content alone, as an image of its own: the name a file
    lies under in a folder is the caller's.
    """
    md5 = file.md5.lower()
    if placement.declares_directory:
        return MATCHED_CONTENT if md5 and md5 in placement.folder_md5s else None
    if placement.identity_kind == IDENTITY_FILE and placement.identity_md5:
        return MATCHED_CONTENT if md5 and md5 == placement.identity_md5.lower() else None
    if file.file_name.casefold() != placement.file_name.casefold():
        return None
    return MATCHED_ARCHIVE_NAME if placement.identity_kind == IDENTITY_ARCHIVE else MATCHED_NAME


def filling[F: FirmwareFile](placement: FirmwarePlacement, files: Iterable[F]) -> tuple[F, ...]:
    """Every file of *files* that fills *placement*, alphabetically by name."""
    return tuple(sorted((file for file in files if fills(placement, file) is not None), key=_by_name))


def first_filling[F: FirmwareFile](placement: FirmwarePlacement, files: Iterable[F]) -> F | None:
    """The alphabetically first file of *files* that fills *placement*, or ``None``."""
    found = filling(placement, files)
    return found[0] if found else None


def fills_any(placements: Iterable[FirmwarePlacement], file: FirmwareFile) -> bool:
    """Does *file* fill the requirement of any of *placements*?"""
    return any(fills(placement, file) is not None for placement in placements)


def _by_name(file: FirmwareFile) -> tuple[str, str]:
    return (file.file_name.casefold(), file.file_name)
