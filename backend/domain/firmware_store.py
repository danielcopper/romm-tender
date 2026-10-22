"""Tender's firmware store: the BIOS files it downloads from RomM, and the links that reach them.

A file lies in the store under the platform it was downloaded for and under the
name RomM gives it, inside Tender's folder (``<folder>/bios/<platform>/<name>``):
every file is kept as it comes, a file two platforms need lies in each of their
folders, and two files with the same bytes both lie there. An emulator reaches a
store file through an absolute symlink at the place the resolver names for it.

Two records, two aggregates. :class:`StoredFirmware` is one store file, and it
is what Tender's folder counts as recorded under it. :class:`FirmwareLink` is
one link Tender placed, keyed by its place, and it names the store path it
points at rather than the store record: a link whose store file was deleted
while its place could not be reached keeps its record until the link itself is
gone (``docs/architecture/backend-architecture.md``, "The firmware store").

The backup a replaced file goes to is decided here too
(:func:`backup_path`), since it is a rule about places and nothing else.

Pure compute — no I/O, no state mutation outside the aggregates' own verbs.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from domain._aggregate import cosmic_aggregate

if TYPE_CHECKING:
    from datetime import datetime

# The store's folder inside Tender's folder, and the backups' inside Tender's data folder.
STORE_FOLDER = "bios"
BACKUP_FOLDER = ("backups", "bios")

# How a backup folder's date and time are written, so the folders sort by when they were made.
BACKUP_STAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"


@cosmic_aggregate
class StoredFirmware:
    """One file in the firmware store, keyed by the platform it was downloaded for and its name in RomM.

    ``folder`` is Tender's folder the file was written under, as the folder
    service gave it; the store path is that folder joined with
    :data:`STORE_FOLDER`, the platform and the name. ``md5`` is the checksum
    RomM states and the file was checked against, ``size`` its size in bytes,
    and ``firmware_id`` RomM's id for it, which is metadata and not identity.
    """

    platform_slug: str
    file_name: str
    folder: str
    md5: str
    size: int
    downloaded_at: str
    firmware_id: int | None = None

    @classmethod
    def mark_downloaded(
        cls,
        *,
        platform_slug: str,
        file_name: str,
        folder: str,
        md5: str,
        size: int,
        downloaded_at: str,
        firmware_id: int | None = None,
    ) -> StoredFirmware:
        """Record a file that reached the store at ISO timestamp *downloaded_at* with the checksum RomM states."""
        if not platform_slug or not file_name or not folder:
            raise ValueError("platform_slug, file_name and folder are required")
        if not md5:
            raise ValueError("a store file is recorded only with the checksum it was checked against")
        return cls(
            platform_slug=platform_slug,
            file_name=file_name,
            folder=folder,
            md5=md5.lower(),
            size=size,
            downloaded_at=downloaded_at,
            firmware_id=firmware_id,
        )


@cosmic_aggregate
class FirmwareLink:
    """One link Tender placed: at *place*, pointing at the store path *target*, in the emulator source *source*.

    ``platform_slug`` and ``file_name`` name the store file the link was
    placed for. The record outlives that store file only where the link could
    not be removed with it.
    """

    place: str
    target: str
    platform_slug: str
    file_name: str
    source: str
    placed_at: str

    @classmethod
    def mark_placed(
        cls,
        *,
        place: str,
        target: str,
        platform_slug: str,
        file_name: str,
        source: str,
        placed_at: str,
    ) -> FirmwareLink:
        """Record a link placed at *place* at ISO timestamp *placed_at*."""
        if not os.path.isabs(place) or not os.path.isabs(target):
            raise ValueError("a link's place and its target are absolute paths")
        return cls(
            place=place,
            target=target,
            platform_slug=platform_slug,
            file_name=file_name,
            source=source,
            placed_at=placed_at,
        )


def backup_stamp(moment: datetime) -> str:
    """*moment* as a backup folder's name: the date and time on this machine's own clock.

    The machine's zone rather than UTC, because the folder is opened by a
    person who just looked at that clock.
    """
    return moment.astimezone().strftime(BACKUP_STAMP_FORMAT)


def backup_path(data_dir: str, stamp: str, original: str) -> str:
    """Where a replaced file at the absolute path *original* goes: below the backup folder, under its whole path."""
    return os.path.join(data_dir, *BACKUP_FOLDER, stamp, original.lstrip("/"))


def backup_folder(data_dir: str, stamp: str) -> str:
    """The backup folder one replacement made at *stamp* fills."""
    return os.path.join(data_dir, *BACKUP_FOLDER, stamp)
