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
import re
from typing import TYPE_CHECKING, Any

from domain.disc_formats import DISC_IMAGE_EXTENSIONS
from domain.rom_adoption import ServerFile, server_manifest
from domain.rom_candidates import DIR, FILE, Kind
from domain.rom_files import is_multi_file_download, resolve_local_file_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.rom_adoption import ServerMember

ZIP_EXT = ".zip"

# A zip written on Windows can separate a member's folders with ``\`` rather
# than the ``/`` the ZIP format specifies, and on Linux Python's ``zipfile``
# keeps such a name as written.
_SEPARATORS = frozenset("/\\")
_FIRST_FOLDER = re.compile(r"[^/\\]+[/\\]")

# macOS Finder's Compress (Archive Utility, which zips as ``ditto -c -k
# --sequesterRsrc`` does — see ``man ditto``) stores each file's resource fork
# and extended attributes as an AppleDouble ``._<name>`` file under a top-level
# ``__MACOSX/`` folder. No emulator reads them, so they are neither counted nor
# unpacked from a single zip.
IGNORED_TOP_FOLDERS: frozenset[str] = frozenset({"__MACOSX"})


def _top_folder(name: str) -> str:
    return re.split(r"[/\\]", name, maxsplit=1)[0]


def _counted(member_names: Iterable[str]) -> list[str]:
    """The member names that say something about the content.

    A directory-only entry (a name ending in a separator) is left out: RomM's
    listing may carry one where the local central-directory read skips it, and
    both must find the same wrapper. So is everything under
    ``IGNORED_TOP_FOLDERS``.
    """
    return [
        name
        for name in member_names
        if name and name[-1] not in _SEPARATORS and _top_folder(name) not in IGNORED_TOP_FOLDERS
    ]


def unpacked_name(name: str, prefix: str, *, single_zip: bool = False) -> str:
    """Where member *name* lands in the folder its zip unpacks into, or ``""`` for nowhere.

    *prefix* is the wrapper folder (:func:`wrapper_prefix`): it and any
    separator left after it come off, so ``Game//Game.bin`` lands as
    ``Game.bin`` and the wrapper's own entry lands nowhere. Read as a user's
    single zip (*single_zip*), a member under ``IGNORED_TOP_FOLDERS`` lands
    nowhere, and so does a directory-only entry named with a ``\\``, which
    ``zipfile`` reads as an empty file; one named with ``/`` stays a folder,
    wrapped or not. With neither — a multi-file ROM's zip — every name lands
    as it is.
    """
    if single_zip and (_top_folder(name) in IGNORED_TOP_FOLDERS or name.endswith("\\")):
        return ""
    return name.removeprefix(prefix).lstrip("/\\") if prefix else name


def is_zip_name(name: str) -> bool:
    """Whether *name* is a ``.zip`` by its extension, case-folded."""
    return os.path.splitext(name)[1].casefold() == ZIP_EXT


def wrapper_prefix(member_names: Iterable[str]) -> str:
    """The one folder an archive wraps its whole content in, with its separator, or ``""``.

    A zip made by compressing a folder holds that folder and nothing beside it
    (``Game/Game.cue``, ``Game/Game.bin``); its content is read, and unpacked,
    as if the folder were not there. Directory-only entries and macOS Finder's
    ``__MACOSX/`` folder do not count (:func:`_counted`). ``""`` where a counted
    member sits at the top level, where they start in different folders, and
    for none.
    """
    prefixes = set()
    for name in _counted(member_names):
        match = _FIRST_FOLDER.match(name)
        if match is None:
            return ""
        prefixes.add(match.group(0))
    return prefixes.pop() if len(prefixes) == 1 else ""


def holds_disc_image(member_names: Iterable[str]) -> bool:
    """Whether an archive with these members is unpacked rather than kept as it is.

    True when a member at the archive's top level is a disc image
    (``DISC_IMAGE_EXTENSIONS``), its extension compared case-folded; for an
    archive wrapped in one folder (:func:`wrapper_prefix`) the top level is that
    folder's, and macOS Finder's ``__MACOSX/`` folder is ignored. ``.bin`` and
    ``.img`` are no disc images: a ``.bin`` is a cartridge on some systems and a
    disc track on others, and an ``.img`` is a disc only beside the ``.ccd``
    that describes it.

    Two roads not taken. An ``.m3u`` does not count, because floppy systems
    (C64, MSX, Amiga, Atari ST, PC-98) list their disks in one, and those
    emulators read the zip as it is. A disc image in a subfolder does not count
    either: an eXoDOS-style DOS game packed with its CD image under ``cd/``
    starts from the zip, which DOSBox Pure reads whole, while an unpacked copy
    would launch from the CD image, and DOSBox Pure would mount only the CD.
    """
    names = _counted(member_names)
    prefix = wrapper_prefix(names)
    return any(
        not _SEPARATORS & set(name) and os.path.splitext(name)[1].casefold() in DISC_IMAGE_EXTENSIONS
        for name in (unpacked_name(full, prefix, single_zip=True) for full in names)
    )


def unpacked_manifest(manifest: tuple[ServerFile, ...]) -> tuple[ServerFile, ...]:
    """The manifest a folder unpacked from a single zip is held to: each listed member as a loose file.

    A member lands where :func:`unpacked_name` places it — its path inside the
    zip, less the folder the zip may wrap everything in — so that path is where
    it belongs in the folder, and its size and digests are the member's. A zip whose members
    RomM does not list contributes nothing: without the listing nothing says
    which file in the folder its one digest belongs to, nor whether it covers
    one member or all of them — an empty manifest is the caller's "cannot be
    confirmed".
    """
    unpacked: list[ServerFile] = []
    for entry in manifest:
        prefix = wrapper_prefix(member.name for member in entry.members)
        counted = set(_counted(member.name for member in entry.members))
        for member in entry.members:
            rel_path = unpacked_name(member.name, prefix, single_zip=True)
            if member.name not in counted or not rel_path:
                continue
            unpacked.append(
                ServerFile(
                    name=rel_path.rsplit("/", 1)[-1],
                    size_bytes=member.size_bytes,
                    algorithm=member.algorithm,
                    digest=member.digest,
                    rel_path=rel_path,
                    crc32=member.crc32,
                )
            )
    return tuple(unpacked)


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
