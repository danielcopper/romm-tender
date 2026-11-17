"""Which platform a library firmware file belongs to, and where Tender's older entries for one belong.

RomM states each firmware file's platform as ``platform_id``, and its platform
list carries every platform's firmware beside the platform's own ``slug`` and
``fs_slug``. The folder a file lies in on the server does not decide its
platform: RomM's firmware structure may put every platform's files below one
folder (``bios_from_user/{platform}``).

Tender keeps a download record and a per-platform emulator pick under a
platform's ``fs_slug`` (#2452 D6, D7). One kept under any other key — a slug, as
both were before, or a server folder name — belongs elsewhere, and
:func:`refiled_record_folder` and :func:`pick_moves` say where. Only an entry
under a key that is no listed ``fs_slug`` is judged at all, so an entry already
under a platform is never moved, and asking again moves nothing.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.platform_listing import PlatformEntry, UnreadableFirmware, listed_number, platform_entries
from domain.platform_names import listed_platform_folders

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from domain.bios_file import BiosFile


def library_firmware(platforms: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every firmware file the platform list carries, each with its platform's folder and slug.

    The platform is the one whose ``id`` the file's ``platform_id`` names: its
    folder (``platform_folder``) is the one RomM keeps it in (``fs_slug``), and
    its slug is ``platform_slug``. A file RomM marks ``missing_from_fs`` is left
    out: the server lists it while its bytes are gone. So is a file whose
    ``platform_id`` names no platform of the list, or one with no slug or no
    folder, which has no platform to belong to. A platform whose firmware is no
    list raises :class:`~domain.platform_listing.UnreadableFirmware`.
    """
    entries = platform_entries(platforms)
    owners = _owners_by_id(entries)
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if entry.firmware is None:
            raise UnreadableFirmware(f"platform {entry.fs_slug or entry.platform_id} lists firmware that is no list")
        for firmware in entry.firmware:
            if firmware.get("missing_from_fs"):
                continue
            platform_id = listed_number(firmware.get("platform_id"))
            owner = owners.get(platform_id) if platform_id is not None else None
            if owner is not None:
                folder, slug = owner
                rows.append({**firmware, "platform_folder": folder, "platform_slug": slug})
    return rows


@dataclass(frozen=True)
class ListedPlatforms:
    """What one read of RomM's platform listing says about where an entry belongs, worked out once per read.

    ``folders`` is the ``fs_slug`` of every listed platform that has one, with
    or without a slug (:func:`~domain.platform_names.listed_platform_folders`):
    the one notion of "a key that is a listed ``fs_slug``". ``by_slug`` groups
    those with a slug under it, and ``firmware`` is the library's firmware
    (:func:`library_firmware`).
    """

    folders: frozenset[str]
    by_slug: Mapping[str, frozenset[str]]
    firmware: tuple[Mapping[str, Any], ...]


def listed_platforms(platforms: Iterable[Mapping[str, Any]]) -> ListedPlatforms:
    """The :class:`ListedPlatforms` of one read of RomM's platform listing."""
    listed = list(platforms)
    return ListedPlatforms(
        folders=listed_platform_folders(listed),
        by_slug=folders_by_slug(listed),
        firmware=tuple(library_firmware(listed)),
    )


def folders_by_slug(platforms: Iterable[Mapping[str, Any]]) -> dict[str, frozenset[str]]:
    """Every listed platform's ``fs_slug``, grouped under its slug; a platform without either is left out."""
    grouped: dict[str, set[str]] = {}
    for entry in platform_entries(platforms):
        if entry.slug is not None and entry.fs_slug is not None:
            grouped.setdefault(entry.slug, set()).add(entry.fs_slug)
    return {slug: frozenset(folders) for slug, folders in grouped.items()}


def refiled_record_folder(key: str, file_name: str, firmware_id: int | None, listed: ListedPlatforms) -> str | None:
    """The ``fs_slug`` a download record kept under *key* belongs under, or ``None`` where it stays.

    A record under a listed ``fs_slug`` stays: it is that platform's. Any other
    is moved to the platform of the library file its ``firmware_id`` names, as
    long as that file still carries the record's *file_name*: the stored id is
    the RomM database's, and once Tender talks to another server, or the
    server's database was set up anew, the same id may name another file.
    Otherwise *key* is read as a slug, and the record moves to the one listed
    ``fs_slug`` with that slug; where several share it, it stays, and each of
    their pages offers it (:func:`records_of_platform`). A key nothing places
    stays where it is.
    """
    if key in listed.folders:
        return None
    if firmware_id is not None:
        target = next(
            (
                row["platform_folder"]
                for row in listed.firmware
                if row.get("id") == firmware_id and row.get("file_name") == file_name
            ),
            None,
        )
        if target is not None:
            return target
    folders = listed.by_slug.get(key, frozenset())
    return next(iter(folders)) if len(folders) == 1 else None


def pick_moves(picks: Mapping[str, Any], listed: ListedPlatforms) -> dict[str, tuple[str, ...]]:
    """Each per-platform pick under a key that is no listed ``fs_slug``, mapped to the ``fs_slug`` values it goes to.

    The key is read as a slug: the pick goes to every listed ``fs_slug`` with
    that slug that has no pick of its own, and the key goes — with no target
    where every one of them has its own. A key that is a listed ``fs_slug``
    stays where it is (#2452 D18), and so does one no listed platform has as
    its slug, which no move could place. Empty where none moves.
    """
    return {
        key: tuple(sorted(folder for folder in listed.by_slug[key] if folder not in picks))
        for key in picks
        if key not in listed.folders and key in listed.by_slug
    }


def records_of_platform(
    records: Iterable[BiosFile], platform_folder: str, platform_slug: str, listed_folders: frozenset[str] | None
) -> list[BiosFile]:
    """The download records one platform's page offers: its own, and those it shares with platforms of its slug.

    A record under the platform's ``fs_slug`` is its own. A record kept under
    the platform's slug, where that slug is no listed ``fs_slug``
    (*listed_folders*, the folders of the listing the refiling last read), is
    one the refiling could not place — several platforms share the slug, or
    the place it would move to records another file of that name — so each
    page of that slug offers it (#2452 D6). Where no listing has been read
    yet (``None``), such a record is offered on no page: nothing says whether
    the slug is a platform's own ``fs_slug``.
    """
    shared = listed_folders is not None and platform_slug not in listed_folders
    return [
        record
        for record in records
        if record.platform_folder == platform_folder or (shared and record.platform_folder == platform_slug)
    ]


def _owners_by_id(entries: Iterable[PlatformEntry]) -> dict[int, tuple[str, str]]:
    """``(fs_slug, slug)`` of every listed platform with both, by its RomM id."""
    return {
        entry.platform_id: (entry.fs_slug, entry.slug)
        for entry in entries
        if entry.platform_id is not None and entry.fs_slug is not None and entry.slug is not None
    }
