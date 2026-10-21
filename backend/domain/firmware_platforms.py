"""Which platform a library firmware file belongs to, and where Tender's older records of one belong.

RomM states each firmware file's platform as ``platform_id``, and its platform
list carries every platform's firmware beside the platform's own ``slug``. A
platform's firmware folder on the server is named by its ``fs_slug``, and that
folder name says nothing Tender reads: RomM's firmware structure may put every
file under one folder for all platforms (``bios_from_user/{platform}``).

Before the platform came from ``platform_id``, Tender filed a download record
and a platform's emulator pick under the folder name a file's path started
with. :func:`refiled_record_platform` and :func:`refiled_picks` say where such
an entry belongs now; both answer nothing for an entry that is already where it
belongs, so asking again moves nothing.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def library_firmware(platforms: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every firmware file the platform list carries, each with its platform's ``platform_slug``.

    The platform is the one whose ``id`` the file's ``platform_id`` names. A
    file RomM marks ``missing_from_fs`` is left out: the server lists it while
    its bytes are gone. So is a file whose ``platform_id`` names no platform of
    the list, which has no platform to belong to.
    """
    listed = list(platforms)
    slugs = _slugs_by_id(listed)
    rows: list[dict[str, Any]] = []
    for platform in listed:
        for firmware in platform.get("firmware") or ():
            if firmware.get("missing_from_fs"):
                continue
            slug = slugs.get(firmware.get("platform_id"))
            if slug is not None:
                rows.append({**firmware, "platform_slug": slug})
    return rows


def folder_platforms(platforms: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Each server folder name that names exactly one platform and is no platform's slug, mapped to that slug.

    These are the names Tender once filed an entry under in place of the
    platform's slug. A folder name that is some platform's slug, or that two
    platforms share, names no one platform, so it is not in the answer.
    """
    listed = list(platforms)
    slugs = {slug for slug in (platform.get("slug") for platform in listed) if isinstance(slug, str) and slug}
    owners: dict[str, set[str]] = {}
    for platform in listed:
        folder, slug = platform.get("fs_slug"), platform.get("slug")
        if isinstance(folder, str) and folder and isinstance(slug, str) and slug and folder != slug:
            owners.setdefault(folder, set()).add(slug)
    return {folder: next(iter(owned)) for folder, owned in owners.items() if len(owned) == 1 and folder not in slugs}


def refiled_record_platform(
    platform_slug: str,
    file_name: str,
    firmware_id: int | None,
    rows: Iterable[Mapping[str, Any]],
    folders: Mapping[str, str],
) -> str | None:
    """The platform a download record filed under *platform_slug* belongs under, or ``None`` where it stays.

    The library file the record names by its ``firmware_id`` decides, as long
    as that file still carries the record's *file_name*: RomM gives a file a new
    id when it scans it again, so an id alone may now name another file. A
    record whose file the library no longer holds is moved by its folder name
    (*folders*, :func:`folder_platforms`).
    """
    target = None
    if firmware_id is not None:
        target = next(
            (
                row["platform_slug"]
                for row in rows
                if row.get("id") == firmware_id and row.get("file_name") == file_name
            ),
            None,
        )
    if target is None:
        target = folders.get(platform_slug)
    return target if target is not None and target != platform_slug else None


def refiled_picks(picks: Mapping[str, str], folders: Mapping[str, str]) -> dict[str, str] | None:
    """*picks* with each pick filed under a folder name moved to its platform's slug, or ``None`` where none moves.

    A pick moves only where its platform has none of its own; otherwise it
    stays where it is, and nothing is lost.
    """
    refiled = dict(picks)
    for folder, slug in folders.items():
        if folder in refiled and slug not in refiled:
            refiled[slug] = refiled.pop(folder)
    return refiled if refiled != dict(picks) else None


def _slugs_by_id(platforms: list[Mapping[str, Any]]) -> dict[Any, str]:
    return {
        platform.get("id"): slug
        for platform in platforms
        if isinstance(slug := platform.get("slug"), str) and slug and platform.get("id") is not None
    }
