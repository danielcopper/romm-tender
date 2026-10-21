"""Which platform a library firmware file belongs to, and where Tender's older entries for one belong.

RomM states each firmware file's platform as ``platform_id``, and its platform
list carries every platform's firmware beside the platform's own ``slug``. The
folder a file lies in on the server does not decide its platform: RomM's
firmware structure may put every platform's files below one folder
(``bios_from_user/{platform}``).

Before the platform came from ``platform_id``, Tender filed a download record
and a per-platform emulator override under a folder name read out of a file's
path — the segment after ``bios/``, or the path's first segment otherwise.
:func:`refiled_record_platform` and :func:`override_moves` say where such an
entry belongs now. Only an entry filed under a name that is no listed
platform's slug is judged at all, so an entry already under a platform is never
moved, and asking again moves nothing.

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


def listed_slugs(platforms: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """Every platform slug the platform list carries."""
    slugs = (platform.get("slug") for platform in platforms)
    return frozenset(slug for slug in slugs if isinstance(slug, str) and slug)


def folder_platforms(platforms: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Each server folder name (``fs_slug``) that names exactly one platform and is no platform's slug, mapped to it.

    A folder name that is some platform's slug, or that two platforms share,
    names no one platform, so it is not in the answer.
    """
    listed = list(platforms)
    slugs = listed_slugs(listed)
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
    platforms: Iterable[Mapping[str, Any]],
) -> str | None:
    """The platform a download record filed under *platform_slug* belongs under, or ``None`` where it stays.

    A record already under a listed platform's slug stays: it was filed by its
    platform and is not judged again. Any other is moved to the platform of the
    library file its ``firmware_id`` names, as long as that file still carries
    the record's *file_name*: the stored id is the RomM database's, and once
    Tender talks to another server, or the server's database was set up anew,
    the same id may name another file. A record whose file the library no
    longer holds is moved by its folder name (:func:`folder_platforms`).
    """
    listed = list(platforms)
    if platform_slug in listed_slugs(listed):
        return None
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
        target = folder_platforms(listed).get(platform_slug)
    return target


def override_moves(overrides: Mapping[str, str], folders: Mapping[str, str]) -> dict[str, str]:
    """Each per-platform emulator override filed under a folder name, mapped to the platform slug it moves to.

    An override moves only where its platform has none of its own; otherwise
    it stays where it is, and nothing is lost. Empty where none moves.
    """
    return {folder: slug for folder, slug in folders.items() if folder in overrides and slug not in overrides}


def _slugs_by_id(platforms: list[Mapping[str, Any]]) -> dict[Any, str]:
    return {
        platform.get("id"): slug
        for platform in platforms
        if isinstance(slug := platform.get("slug"), str) and slug and platform.get("id") is not None
    }
