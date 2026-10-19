"""What a download of a ROM becomes on disk — a file, a folder, or either.

RomM serves a ROM stored as several files as one ZIP that Tender unpacks into a
folder, and a ROM stored as one file as that file. A single ``.zip`` holding a
disc image is the case between the two: it arrives as a file and is unpacked
into a folder once the download has read its own central directory. Which sites
ask :func:`served_shapes`, and why they must all ask it, is
docs/architecture/backend-architecture.md, "DownloadService notes".

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

# A member with one of these extensions makes a single zip a disc set to unpack.
# ``.bin`` and ``.img`` are absent on purpose: a ``.bin`` is a cartridge on some
# systems and a disc track on others, and an ``.img`` is a disc only beside the
# ``.ccd`` that describes it — which is already in the set.
_UNPACK_EXTENSIONS: frozenset[str] = DISC_IMAGE_EXTENSIONS | {".m3u"}


def is_zip_name(name: str) -> bool:
    """Whether *name* is a ``.zip`` by its extension, case-folded."""
    return os.path.splitext(name)[1].casefold() == ZIP_EXT


def holds_disc_image(member_names: Iterable[str]) -> bool:
    """Whether an archive with these members is unpacked rather than kept as it is.

    True when one member is a disc image (``DISC_IMAGE_EXTENSIONS``) or a
    ``.m3u`` playlist; extensions are compared case-folded, so a ``.CUE`` is a
    disc image too. A cartridge, an arcade romset, a ``.bin`` alone and an
    ``.img`` without its ``.ccd`` all answer False.
    """
    return any(os.path.splitext(name)[1].casefold() in _UNPACK_EXTENSIONS for name in member_names)


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
    wherever RomM lists no members, so the space check stays what it was.
    """
    members = _listed_members(rom_detail)
    if members is None or not holds_disc_image(member.name for member in members):
        return 0
    return sum(member.size_bytes for member in members)
