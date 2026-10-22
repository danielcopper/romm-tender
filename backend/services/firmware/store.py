"""Tender's firmware store: fetching a file into it, the links into emulator sources, and removing both.

The only writer of a store record and of a link record, and the only code that
places or removes a link of Tender's. A file is fetched into the store first and
kept only on the checksum RomM states for it; links follow from the store, and
bringing them up to date (:meth:`FirmwareStore.do_update_links`) is the one way a
link is placed, put back, rewritten or removed — after a download, after a
replacement, and whenever the panel asks. Which link goes where is
:mod:`domain.firmware_places` and :mod:`domain.firmware_rows`; this module looks
at the places and carries the plan out.

Every method is blocking and is run off the event loop by its caller. Nothing
here checks a conflict rule: the use cases in
:class:`services.firmware.FirmwareService` do. A write into the store goes
through Tender's folder made ready for it, which refuses where the folder cannot
be reached.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from domain.firmware_places import place_refusal, plan_link_update, root_over
from domain.firmware_rows import OwnedLinks, StoreCopy, wanted_links
from domain.firmware_store import (
    BACKUP_FOLDER,
    BACKUP_STAMP_FORMAT,
    STORE_FOLDER,
    FirmwareLink,
    StoredFirmware,
    backup_path,
)
from domain.rom_files import TMP_EXT
from lib.errors import Refused, with_system_text
from lib.path_safety import PathTraversalError, safe_join

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Iterable

    from domain.firmware_places import LinkUpdatePlan, PlaceLook, WantedLink
    from domain.source_roots import SourceRoots
    from services.protocols import (
        Clock,
        FirmwareFileStore,
        FirmwarePlatformResolver,
        PrepareTenderFolderFn,
        RommFirmwareApi,
        UnitOfWorkFactory,
    )

# The reason a download whose bytes do not match RomM's checksum is refused with.
DOWNLOAD_DAMAGED = "bios_download_damaged"


@dataclass(frozen=True)
class FirmwareStoreConfig:
    """Frozen wiring bundle handed to ``FirmwareStore.__init__``.

    Holds the RomM API the bytes come from, the file store every look and
    write goes through, the resolver seam that answers each enabled source's
    demand and roots, Tender's folder made ready for a write, the
    Unit-of-Work factory the records are kept through, the clock a record and
    a backup are stamped by, Tender's data folder the backups go under, and the
    logger.
    """

    romm_api: RommFirmwareApi
    firmware_file_store: FirmwareFileStore
    platform_firmware_resolver: FirmwarePlatformResolver
    prepare_folder: PrepareTenderFolderFn
    uow_factory: UnitOfWorkFactory
    clock: Clock
    data_dir: str
    logger: logging.Logger


@dataclass(frozen=True)
class StoreRecords:
    """Every store record and every link record, read in one short UoW."""

    stored: tuple[StoredFirmware, ...]
    links: dict[str, FirmwareLink]


@dataclass(frozen=True)
class FetchOutcome:
    """What fetching one file into the store did: whether it was downloaded, and where a file in the way went."""

    file_name: str
    downloaded: bool
    backup: str | None = None


@dataclass
class LinkOutcome:
    """What one update of a platform's links did, over every enabled source."""

    placed: int = 0
    left_in_place: int = 0
    removed: int = 0
    failed: list[dict[str, str | None]] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "placed": self.placed,
            "left_in_place": self.left_in_place,
            "removed": self.removed,
            "link_failures": list(self.failed),
        }


@dataclass
class RemovalOutcome:
    """What removing store files did: how many went, the links kept and why, and what could not be removed."""

    deleted: int = 0
    kept: list[dict[str, str | None]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class FirmwareStore:
    """Tender's firmware store and the links into the emulator sources."""

    def __init__(self, *, config: FirmwareStoreConfig) -> None:
        self._romm_api = config.romm_api
        self._files = config.firmware_file_store
        self._resolver = config.platform_firmware_resolver
        self._prepare_folder = config.prepare_folder
        self._uow_factory = config.uow_factory
        self._clock = config.clock
        self._data_dir = config.data_dir
        self._logger = config.logger

    def backup_root(self) -> str:
        """The folder every replaced file's backup goes under, each in a folder of its own date and time."""
        return os.path.join(self._data_dir, *BACKUP_FOLDER)

    # ── Records ──────────────────────────────────────────────

    def do_read_records(self) -> StoreRecords:
        """Every store and link record, in one short read UoW."""
        with self._uow_factory() as uow:
            stored = tuple(uow.firmware_store.iter_all())
            links = {link.place: link for link in uow.firmware_links.iter_all()}
        return StoreRecords(stored=stored, links=links)

    def store_path(self, stored: StoredFirmware) -> str:
        """Where *stored* lies: below its Tender folder, its platform and its name in RomM."""
        return _in_platform_folder(stored.folder, stored.platform_slug, stored.file_name)

    def do_copies(self, records: StoreRecords, platform_slug: str) -> list[StoreCopy]:
        """*platform_slug*'s store files, each with whether it is on disk."""
        copies = []
        for stored in records.stored:
            if stored.platform_slug != platform_slug:
                continue
            path = self._path_or_none(stored)
            if path is not None:
                copies.append(
                    StoreCopy(
                        file_name=stored.file_name,
                        md5=stored.md5,
                        platform_slug=stored.platform_slug,
                        path=path,
                        present=self._files.exists(path),
                    )
                )
        return copies

    def do_owned(self, records: StoreRecords) -> OwnedLinks:
        """What decides whose a link is, over every platform's store files."""
        targets: dict[str, str] = {}
        present: set[str] = set()
        for stored in records.stored:
            path = self._path_or_none(stored)
            if path is None:
                continue
            targets[path] = stored.platform_slug
            if self._files.exists(path):
                present.add(path)
        return OwnedLinks(store_targets=targets, present=frozenset(present), records=records.links)

    def _path_or_none(self, stored: StoredFirmware) -> str | None:
        try:
            return self.store_path(stored)
        except PathTraversalError as exc:
            self._logger.warning(f"Skipping a firmware store record with an unsafe name: {exc}")
            return None

    # ── Fetching into the store ──────────────────────────────

    def do_fetch(self, platform_slug: str, firmware: dict[str, Any]) -> FetchOutcome:
        """Fetch the library file *firmware* into *platform_slug*'s store folder, kept only on RomM's checksum.

        A file already recorded there with the same checksum is not fetched
        again. A file lying there with no record is taken over where its bytes
        match the checksum, and otherwise moved to the backup folder first.

        Refuses ``folder_unreachable`` (Tender's folder), ``no_checksum`` where
        RomM states none, ``path_traversal`` for a name that leaves the store,
        ``bios_download_failed`` where this device could not write the file and
        :data:`DOWNLOAD_DAMAGED` where the bytes did not match. A RomM error
        propagates as it was raised.
        """
        name = firmware.get("file_name", "")
        md5 = str(firmware.get("md5_hash") or "").lower()
        if not md5:
            raise Refused("no_checksum", f"RomM states no checksum for {name}, so Tender cannot check a download.")
        folder = self._prepare_folder()
        try:
            path = _in_platform_folder(folder, platform_slug, name)
        except PathTraversalError as exc:
            self._logger.error(f"Rejected firmware with unsafe file name {name!r}: {exc}")
            raise Refused("path_traversal", "Server sent an unsafe firmware file name — download aborted") from exc
        stored = self._stored(platform_slug, name, folder, md5, firmware)
        record = self._record_of(platform_slug, name)
        backup = None
        if self._files.exists(path):
            if record is not None and record.md5 == md5:
                return FetchOutcome(file_name=name, downloaded=False)
            if record is None:
                if self._files.checksum_md5(path).lower() == md5:
                    self._save(stored)
                    return FetchOutcome(file_name=name, downloaded=False)
                backup = self._backup(path)
        self._download(firmware, path, md5)
        self._save(stored)
        self._logger.info(f"Firmware downloaded into the store: {name} -> {path}")
        return FetchOutcome(file_name=name, downloaded=True, backup=backup)

    def _download(self, firmware: dict[str, Any], path: str, md5: str) -> None:
        name = firmware.get("file_name", "")
        tmp = path + TMP_EXT
        try:
            self._files.make_dirs(os.path.dirname(path))
            self._romm_api.download_firmware(int(firmware["id"]), name, tmp)
        except Exception as exc:
            self._files.remove_file(tmp)
            if isinstance(exc, OSError):
                self._logger.error(f"Failed to download firmware {name}: {exc}")
                raise Refused(
                    "bios_download_failed",
                    with_system_text(f"{name} could not be downloaded: it could not be written on this device", exc),
                ) from exc
            raise
        if self._files.checksum_md5(tmp).lower() != md5:
            self._files.remove_file(tmp)
            self._logger.warning(f"Firmware {name} did not match the checksum RomM states; discarded")
            raise Refused(DOWNLOAD_DAMAGED, "The download was damaged. Try again.")
        self._files.rename(tmp, path)

    def _record_of(self, platform_slug: str, name: str) -> StoredFirmware | None:
        with self._uow_factory() as uow:
            return uow.firmware_store.get(platform_slug, name)

    def _stored(self, platform_slug: str, name: str, folder: str, md5: str, firmware: dict[str, Any]) -> StoredFirmware:
        """The record a fetch of *firmware* writes, built before anything is written so bad metadata writes nothing."""
        try:
            return StoredFirmware.mark_downloaded(
                platform_slug=platform_slug,
                file_name=name,
                folder=folder,
                md5=md5,
                size=int(firmware.get("file_size_bytes") or 0),
                downloaded_at=self._clock.now().isoformat(),
                firmware_id=firmware.get("id"),
            )
        except ValueError as exc:
            raise Refused("invalid_firmware", f"Invalid firmware metadata: {exc}") from exc

    def _save(self, stored: StoredFirmware) -> None:
        with self._uow_factory() as uow:
            uow.firmware_store.save(stored)

    def _backup(self, path: str) -> str:
        """Move the file at *path* to the backup folder under its whole path, and answer where it went."""
        destination = backup_path(self._data_dir, self._clock.now().strftime(BACKUP_STAMP_FORMAT), path)
        self._files.move_aside(path, destination)
        self._logger.info(f"Moved {path} to {destination}")
        return destination

    # ── The links ────────────────────────────────────────────

    def do_update_links(self, platform_slug: str, system: str | None) -> LinkOutcome:
        """Bring *platform_slug*'s links up to date in every enabled source; download nothing.

        A platform with no system to ask about has no emulator to link for,
        and only its records are looked at.
        """
        catalogues = self._resolver.every_source(system) if system is not None else ()
        records = self.do_read_records()
        copies = self.do_copies(records, platform_slug)
        owned = self.do_owned(records)
        wanted: list[WantedLink] = []
        looks: dict[str, PlaceLook] = {}
        for catalogue in catalogues:
            for want, placement in wanted_links(catalogue.placements, platform_slug, catalogue.source or "", copies):
                look = looks.setdefault(want.place, self._files.look(want.place))
                refusal = place_refusal(want.place, look, catalogue.roots, unreadable=placement.place_unreadable)
                wanted.append(replace(want, refusal=refusal))
        roots = self._resolver.source_roots()
        blocked: set[str] = set()
        for place, link in records.links.items():
            if link.platform_slug != platform_slug:
                continue
            looks.setdefault(place, self._files.look(place))
            if _blocked(place, roots.get(link.source)):
                blocked.add(place)
        plan = plan_link_update(
            platform_slug,
            wanted,
            looks,
            store_targets=owned.store_targets,
            present=owned.present,
            records=records.links,
            blocked=frozenset(blocked),
        )
        return self._carry_out(plan)

    def _carry_out(self, plan: LinkUpdatePlan) -> LinkOutcome:
        outcome = LinkOutcome(left_in_place=plan.left_in_place)
        saved: list[FirmwareLink] = []
        dropped: list[str] = []
        for want in (*plan.place, *plan.replace):
            if want in plan.replace and not self._try(outcome, want, lambda w=want: self._files.remove_link(w.place)):
                continue
            if self._try(outcome, want, lambda w=want: self._files.symlink(w.target or "", w.place)):
                outcome.placed += 1
                saved.append(self._link_record(want))
        for place in plan.remove:
            try:
                self._files.remove_link(place)
            except OSError as exc:
                self._logger.warning(f"Could not remove Tender's link at {place}: {exc}")
                continue
            outcome.removed += 1
            dropped.append(place)
        saved.extend(self._link_record(want) for want in plan.record)
        dropped.extend(place for place in plan.drop if place not in dropped)
        self._write_links(saved, [place for place in dropped if place not in {link.place for link in saved}])
        return outcome

    def _try(self, outcome: LinkOutcome, want: WantedLink, act: Callable[[], None]) -> bool:
        try:
            act()
        except OSError as exc:
            self._logger.warning(f"Could not place a link at {want.place}: {exc}")
            outcome.failed.append(
                {"place": want.place, "file_name": want.file_name, "reason": exc.strerror or str(exc)}
            )
            return False
        return True

    def _link_record(self, want: WantedLink) -> FirmwareLink:
        return FirmwareLink.mark_placed(
            place=want.place,
            target=want.target or "",
            platform_slug=want.platform_slug,
            file_name=want.file_name or os.path.basename(want.target or ""),
            source=want.source,
            placed_at=self._clock.now().isoformat(),
        )

    def _write_links(self, saved: Iterable[FirmwareLink], dropped: Iterable[str]) -> None:
        with self._uow_factory() as uow:
            for place in dropped:
                uow.firmware_links.delete(place)
            for link in saved:
                uow.firmware_links.save(link)

    def do_place_link(self, place: str, target: str, platform_slug: str, source: str) -> None:
        """Place one link at the free *place* naming *target*, and record it; an ``OSError`` propagates."""
        self._files.symlink(target, place)
        self._write_links(
            [
                FirmwareLink.mark_placed(
                    place=place,
                    target=target,
                    platform_slug=platform_slug,
                    file_name=os.path.basename(target),
                    source=source,
                    placed_at=self._clock.now().isoformat(),
                )
            ],
            (),
        )

    def do_back_up(self, path: str) -> str:
        """Move the file at *path* to the backup folder, and answer where it went."""
        return self._backup(path)

    # ── Removing ─────────────────────────────────────────────

    def do_remove(self, platform_slug: str, names: frozenset[str] | None) -> RemovalOutcome:
        """Remove *platform_slug*'s store files named in *names* — all of them for ``None`` — and every link to each.

        A link is removed only while it still points at the store file, and
        only where its place may be touched: one under a root the resolver
        only assumed, or one whose source's roots could not be asked, stays
        with its record, and the outcome names it. Only Tender's own files and
        links are ever removed.
        """
        records = self.do_read_records()
        roots = self._resolver.source_roots()
        outcome = RemovalOutcome()
        removed_links: list[str] = []
        removed_files: list[tuple[str, str]] = []
        for stored in records.stored:
            if stored.platform_slug != platform_slug or (names is not None and stored.file_name not in names):
                continue
            path = self._path_or_none(stored)
            if path is None:
                continue
            for link in records.links.values():
                if link.target != path:
                    continue
                source_roots = roots.get(link.source)
                if _blocked(link.place, source_roots):
                    outcome.kept.append({"place": link.place, "folder": _assumed_folder(link.place, source_roots)})
                    continue
                if self._files.look(link.place).link_target == path:
                    try:
                        self._files.remove_link(link.place)
                    except OSError as exc:
                        outcome.errors.append(f"{link.place}: {exc}")
                        continue
                removed_links.append(link.place)
            if self._files.exists(path):
                try:
                    self._files.remove_file(path)
                except OSError as exc:
                    self._logger.warning(f"Failed to remove BIOS file {stored.file_name}: {exc}")
                    outcome.errors.append(f"{stored.file_name}: {exc}")
                    continue
                outcome.deleted += 1
            removed_files.append((stored.platform_slug, stored.file_name))
        with self._uow_factory() as uow:
            for place in removed_links:
                uow.firmware_links.delete(place)
            for slug, name in removed_files:
                uow.firmware_store.delete(slug, name)
        return outcome

    # ── What is recorded under Tender's folder ───────────────

    def recorded_folders(self) -> frozenset[str]:
        """Every Tender folder a store file is recorded under."""
        with self._uow_factory() as uow:
            return uow.firmware_store.get_folders()

    def forget_records_under(self, folder: str) -> None:
        """Forget every store file recorded under *folder*, and remove Tender's links to them; delete no file.

        A link goes only while it still points at the recorded store path and
        its place may be touched; one under a root the resolver only assumed
        stays, its record forgotten with the rest.
        """
        records = self.do_read_records()
        roots = self._resolver.source_roots()
        forgotten = [stored for stored in records.stored if stored.folder == folder]
        paths = {path for stored in forgotten if (path := self._path_or_none(stored)) is not None}
        dropped = []
        for link in records.links.values():
            if link.target not in paths:
                continue
            dropped.append(link.place)
            if (
                not _blocked(link.place, roots.get(link.source))
                and self._files.look(link.place).link_target == link.target
            ):
                try:
                    self._files.remove_link(link.place)
                except OSError as exc:
                    self._logger.warning(f"Could not remove Tender's link at {link.place}: {exc}")
        with self._uow_factory() as uow:
            for place in dropped:
                uow.firmware_links.delete(place)
            for stored in forgotten:
                uow.firmware_store.delete(stored.platform_slug, stored.file_name)


def _in_platform_folder(folder: str, platform_slug: str, name: str) -> str:
    """*name* in *platform_slug*'s store folder, refused where either leaves the folder it is joined onto.

    Joined in two steps so a server-supplied name can no more reach another
    platform's folder than leave the store.
    """
    return safe_join(safe_join(folder, STORE_FOLDER, platform_slug), name)


def _blocked(place: str, roots: SourceRoots | None) -> bool:
    """May *place* not be touched now — under a root the resolver only assumed, or with no roots to judge it by?"""
    if roots is None:
        return True
    root = root_over(place, roots)
    return root is not None and root.assumed


def _assumed_folder(place: str, roots: SourceRoots | None) -> str | None:
    root = root_over(place, roots) if roots is not None else None
    return root.path if root is not None else None
