"""Removing the BIOS files Tender itself put on the machine — and only those.

Two kinds of file are Tender's to remove. A file in its firmware store is
removed with every link of Tender's to it (:meth:`FirmwareStore.do_remove`). A
file Tender downloaded into an emulator source's folder before the store
existed is removed at the path its ``downloaded_bios`` record holds, and only
there: having written the file is the authority, and the record is the only
evidence of it, so nothing here consults a status listing, a file's existence
elsewhere, or what the library currently offers.

A row's Delete and the platform-wide Delete share the ONE removal below, and
differ only in which names they select: a second copy of the loop is what the
register's BIOS-delete rule warns about, since the copies would drift silently.

A Delete is never refused because a place cannot be reached. A link under a
root the resolver only assumed — or under none it could judge — stays where it
is, keeps its record, and the answer names it. An older download is removed
only inside the BIOS folder RetroDECK names now; one whose file is gone loses
its record without a word. Where RetroDECK's settings could not be read,
RetroDECK is not installed, or it names no BIOS folder, the file stays with its
record, so its Delete stays offered; where it lies outside that folder, the
file stays and its record goes. Each kept entry names its ``kind``, its place,
the folder it was kept in where one is known, and the emulator source the
reason is about; an older download also names its file and ``why`` it was
kept (``settings_unreadable``, ``not_installed``, ``no_bios_folder`` or
``outside_bios_folder``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.emulator_sources import RETRODECK
from domain.retrodeck_folders import NO_BIOS_ROOT, FolderRefused
from lib.partial_failure import PartialFailure
from lib.path_safety import is_inside_folder

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.bios_file import BiosFile
    from services.firmware.listing import FirmwareListing
    from services.firmware.store import FirmwareStore
    from services.protocols import FirmwareFileStore, RetroDeckFolders, UnitOfWorkFactory


KEPT_SETTINGS_UNREADABLE = "settings_unreadable"
KEPT_NOT_INSTALLED = "not_installed"
KEPT_NO_BIOS_FOLDER = "no_bios_folder"
KEPT_OUTSIDE_BIOS_FOLDER = "outside_bios_folder"


def _why_kept(bound: str | FolderRefused | None, path: str) -> str | None:
    """Why an older download at *path* may not be removed under *bound*; ``None`` where it may."""
    if isinstance(bound, FolderRefused):
        return KEPT_NO_BIOS_FOLDER if bound.reason == NO_BIOS_ROOT else KEPT_SETTINGS_UNREADABLE
    if bound is None:
        return KEPT_NOT_INSTALLED
    return None if is_inside_folder(path, bound) else KEPT_OUTSIDE_BIOS_FOLDER


@dataclass(frozen=True)
class PlatformBiosDeleterConfig:
    """Frozen wiring bundle handed to ``PlatformBiosDeleter.__init__``.

    Holds the listing peer whose cache a removal invalidates, the store peer
    that removes store files and their links, the file store an older download
    is removed through, RetroDECK's folders, whose BIOS folder bounds the
    removal of an older download, the Unit-of-Work factory the older records
    are read and pruned through, and runtime infrastructure.
    """

    listing: FirmwareListing
    store: FirmwareStore
    firmware_file_store: FirmwareFileStore
    retrodeck_folders: RetroDeckFolders
    uow_factory: UnitOfWorkFactory
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


@dataclass(frozen=True)
class FirmwareDeletionIncomplete(PartialFailure):
    """A BIOS delete that did not remove every file its records name."""

    deleted_count: int


class PlatformBiosDeleter:
    """Tender's own BIOS files for one platform — the store's and the older downloads — and their removal."""

    def __init__(self, *, config: PlatformBiosDeleterConfig) -> None:
        self._listing = config.listing
        self._store = config.store
        self._files = config.firmware_file_store
        self._retrodeck_folders = config.retrodeck_folders
        self._uow_factory = config.uow_factory
        self._loop = config.loop
        self._logger = config.logger

    def _delete_io(
        self, platform_slug: str, names: frozenset[str] | None
    ) -> tuple[int, list[dict[str, str | None]], list[str]]:
        """Remove *platform_slug*'s store files and older downloads named in *names* — every one for ``None``.

        The one removal, deliberately: both buttons reach it, and they differ
        only in which names they select. Runs in an executor: the store's files
        and links go first, then the older records are read in one short UoW
        and the rows they leave behind dropped in one after their files go
        (ADR-0006). An older download whose file is already gone is not a
        deletion and not an error — its record is dropped and nothing is
        counted or said, wherever it lay.

        Answers what was removed, what stays and why, and what could not be
        removed.
        """
        outcome = self._store.do_remove(platform_slug, names)
        deleted, kept, errors = outcome.deleted, list(outcome.kept), list(outcome.errors)
        bound = self._retrodeck_folders.bios_folder()
        pruned: list[tuple[str, str]] = []
        for record in self._older_downloads(platform_slug):
            if names is not None and record.file_name not in names:
                continue
            if not self._files.exists(record.file_path):
                pruned.append((record.platform_slug, record.file_name))
                continue
            why = _why_kept(bound, record.file_path)
            if why is not None:
                kept.append(
                    {
                        "kind": "file",
                        "place": record.file_path,
                        "folder": os.path.dirname(record.file_path),
                        "source": RETRODECK,
                        "file_name": record.file_name,
                        "why": why,
                    }
                )
                if why == KEPT_OUTSIDE_BIOS_FOLDER:
                    pruned.append((record.platform_slug, record.file_name))
                continue
            try:
                self._files.remove_file(record.file_path)
            except OSError as e:
                self._logger.warning(f"Failed to remove BIOS file {record.file_name}: {e}")
                errors.append(f"{record.file_name}: {e}")
                continue
            deleted += 1
            pruned.append((record.platform_slug, record.file_name))
        if pruned:
            with self._uow_factory() as uow:
                for slug, file_name in pruned:
                    uow.bios_files.delete(slug, file_name)
        return deleted, kept, errors

    def _older_downloads(self, platform_slug: str) -> list[BiosFile]:
        """The download records of files Tender wrote into a source's folder before the store, for *platform_slug*."""
        with self._uow_factory() as uow:
            return list(uow.bios_files.iter_by_platform(platform_slug))

    async def delete_bios_file(self, platform_slug, file_name) -> dict[str, Any] | FirmwareDeletionIncomplete:
        """Delete one BIOS file of Tender's — the store file, or an older download — by its name in RomM.

        A name Tender holds nothing under removes nothing and says so; a caller
        cannot widen this into a presence-based delete by passing another name.
        """
        deleted, kept, errors = await self._loop.run_in_executor(
            None, self._delete_io, platform_slug, frozenset({file_name})
        )
        self._listing.invalidate()
        if errors:
            return FirmwareDeletionIncomplete(
                reason="delete_incomplete", message=f"Could not delete {errors[0]}", deleted_count=deleted
            )
        if deleted:
            message = f"Deleted {file_name}"
        elif kept:
            message = f"Left {file_name} where it is"
        else:
            message = f"Nothing to delete for {file_name}"
        return {"success": True, "deleted_count": deleted, "message": message, "kept": kept}

    async def delete_platform_bios(self, platform_slug) -> dict[str, Any] | FirmwareDeletionIncomplete:
        """Delete every BIOS file of Tender's for a platform, and every link of Tender's to them."""
        deleted, kept, errors = await self._loop.run_in_executor(None, self._delete_io, platform_slug, None)
        self._listing.invalidate()
        if errors:
            return FirmwareDeletionIncomplete(
                reason="delete_incomplete",
                message=f"Deleted {deleted} file(s), {len(errors)} error(s)",
                deleted_count=deleted,
            )
        message = f"Deleted {deleted} BIOS file(s)" if deleted or kept else "No BIOS files for this platform"
        return {"success": True, "deleted_count": deleted, "message": message, "kept": kept}
