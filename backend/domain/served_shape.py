"""What a download of a ROM becomes on disk — a file, a folder, or both.

RomM serves a ROM stored as several files as one ZIP that Tender unpacks into a
folder, and a ROM stored as one file as that file. A single ``.zip`` holding a
disc image is the case between the two: it arrives as a file and is unpacked
into a folder once the download has read its own central directory. Which sites
ask :func:`served_shapes` and :func:`replace_covers`, and why they must all ask
them, is docs/architecture/backend-architecture.md, "DownloadService notes".

Pure: RomM's payload and an archive's member names arrive as values.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from domain.disc_formats import DISC_IMAGE_EXTENSIONS
from domain.rom_adoption import server_manifest
from domain.rom_candidates import DIR, FILE, Kind
from domain.rom_files import is_multi_file_download, resolve_local_file_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.rom_adoption import ServerMember

ZIP_EXT = ".zip"


def is_zip_name(name: str) -> bool:
    """Whether *name* is a ``.zip`` by its extension, case-folded."""
    return os.path.splitext(name)[1].casefold() == ZIP_EXT


def holds_disc_image(member_names: Iterable[str]) -> bool:
    """Whether an archive with these members is unpacked rather than kept as it is.

    True when a member at the archive's top level is a disc image
    (``DISC_IMAGE_EXTENSIONS``), its extension compared case-folded.
    ``.bin`` and ``.img`` are no disc images: a ``.bin`` is a cartridge on some
    systems and a disc track on others, and an ``.img`` is a disc only beside
    the ``.ccd`` that describes it.

    Two roads not taken. An ``.m3u`` does not count, because floppy systems
    (C64, MSX, Amiga, Atari ST, PC-98) list their disks in one, and those
    emulators read the zip as it is. A disc image in a subfolder does not count
    either: an eXoDOS-style DOS game packed with its CD image under ``cd/``
    starts from the zip, which DOSBox Pure reads whole, while an unpacked copy
    would launch from the CD image, and DOSBox Pure would mount only the CD.
    """
    return any(
        "/" not in name and os.path.splitext(name)[1].casefold() in DISC_IMAGE_EXTENSIONS for name in member_names
    )


def _listed_members(rom_detail: dict[str, Any]) -> tuple[ServerMember, ...] | None:
    """The members RomM lists inside a single zip, or ``None`` where it lists none.

    ``None`` both for a payload without ``archive_members`` (a library not
    rescanned since RomM 4.9.0) and for a ROM that is no single zip at all.
    """
    if is_multi_file_download(rom_detail) or not is_zip_name(resolve_local_file_name(rom_detail)[0]):
        return None
    manifest = server_manifest(rom_detail)
    if len(manifest) != 1 or not manifest[0].members:
        return None
    return manifest[0].members


def served_shapes(rom_detail: dict[str, Any]) -> frozenset[Kind]:
    """The shapes this ROM's download may take on disk: ``{FILE}``, ``{DIR}`` or both.

    A multi-file ROM is a folder and any other single file a file. A single
    ``.zip`` is both — the file it arrives as and the folder it is unpacked into
    — unless RomM lists its members and none of them is a disc image
    (:func:`holds_disc_image`); then it is kept as the file it is. Where RomM
    lists nothing the zip may still unpack, so both shapes stand.
    """
    if is_multi_file_download(rom_detail):
        return frozenset({DIR})
    if not is_zip_name(resolve_local_file_name(rom_detail)[0]):
        return frozenset({FILE})
    members = _listed_members(rom_detail)
    if members is not None and not holds_disc_image(member.name for member in members):
        return frozenset({FILE})
    return frozenset({FILE, DIR})


def listed_unpacked_bytes(rom_detail: dict[str, Any]) -> int:
    """What a single zip RomM lists as a disc set takes once unpacked, or 0.

    The sum of the member sizes RomM states, counted only where they name a disc
    image — the zip that is kept as it is needs nothing beyond itself — and 0
    wherever RomM lists no members, which leaves the zip alone to be counted.
    """
    members = _listed_members(rom_detail)
    if members is None or not holds_disc_image(member.name for member in members):
        return 0
    return sum(member.size_bytes for member in members)


def replace_covers(
    occupied: Iterable[str],
    *,
    replace: bool,
    resume: bool,
    folder_path: str | None,
    confirmed_paths: Iterable[str] | None,
) -> frozenset[str]:
    """The occupied paths a download's replace answer may remove.

    A download that claims one path asks about it alone, so *replace* covers
    it. One that claims two — a single zip at its own path and the folder it
    would become, *folder_path* — shows each occupied path in a dialog of its
    own, and a path is covered only where the user chose to replace exactly it:
    listed in *confirmed_paths*, or, for an answer that names none, the one path
    occupied. The caller removes nothing unless every occupied path is covered.

    On a resume *confirmed_paths* is what the start confirmed, and the folder is
    never covered: a replaced folder was removed before the transfer, so
    whatever sits there now is content nobody was shown. The zip's answer is
    still live, because a file is removed only once its replacement is
    installed, but only where the start confirmed it — a zip that appeared
    during the pause was never shown.
    """
    paths = frozenset(occupied)
    if not replace:
        return frozenset()
    if folder_path is None:
        return paths
    confirmed = None if confirmed_paths is None else frozenset(os.path.normpath(str(p)) for p in confirmed_paths)
    if resume:
        return (paths & (confirmed or frozenset())) - {folder_path}
    if confirmed is not None:
        return paths & confirmed
    return paths if len(paths) == 1 else frozenset()
