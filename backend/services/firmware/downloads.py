"""Fetching firmware out of the RomM library and onto the machine.

The only writer of a ``downloaded_bios`` record, which is what later authorises
a delete: having placed the file is the authority, and this record is the sole
evidence of it. Every entry point resolves the destination through the machine's
demand rather than inventing a layout, so a file lands where the emulator will
open it, and takes the BIOS folder first: where none may take a download, the
press is refused before anything is fetched.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain import firmware_paths
from domain.bios_file import BiosFile
from domain.emulator_commands import resolve_platform_option
from domain.firmware_groups import fetched_as_required
from domain.refusal import DomainRefused
from domain.rom_files import TMP_EXT
from lib.errors import Refused, RommApiError
from lib.path_safety import PathTraversalError

if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import Iterator, Mapping

    from domain.firmware_wants import FirmwareCatalogue, FirmwarePlacement
    from services.firmware.demand import FirmwareDemand
    from services.firmware.listing import FirmwareListing
    from services.protocols import (
        Clock,
        CoreInfoProvider,
        FirmwareFileStore,
        PlatformCoreReader,
        RommFirmwareApi,
        SystemResolver,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class FirmwareDownloaderConfig:
    """Frozen wiring bundle handed to ``FirmwareDownloader.__init__``.

    Holds the RomM API adapter the bytes come from, the two peer sub-services
    (the listing a platform's rows are picked out of, the demand each
    destination is resolved through), the ES-DE core reads and the per-platform
    emulator override the required-only filter resolves its emulator from, the file
    store, the clock the download timestamp is taken from, the Unit-of-Work
    factory the record is written through, and runtime infrastructure.
    """

    romm_api: RommFirmwareApi
    listing: FirmwareListing
    demand: FirmwareDemand
    core_info: CoreInfoProvider
    resolve_system: SystemResolver
    platform_core_reader: PlatformCoreReader
    firmware_file_store: FirmwareFileStore
    clock: Clock
    uow_factory: UnitOfWorkFactory
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class FirmwareDownloader:
    """The firmware download entry points and the record each one leaves behind."""

    def __init__(self, *, config: FirmwareDownloaderConfig) -> None:
        self._romm_api = config.romm_api
        self._listing = config.listing
        self._demand = config.demand
        self._core_info = config.core_info
        self._resolve_system = config.resolve_system
        self._platform_core_reader = config.platform_core_reader
        self._firmware_file_store = config.firmware_file_store
        self._clock = config.clock
        self._uow_factory = config.uow_factory
        self._loop = config.loop
        self._logger = config.logger
        # A download is awaited inside the call that asked for it, so the calls
        # still running are the whole of what is in flight.
        self._calls_in_flight = 0

    def is_downloading(self) -> bool:
        """Whether a call made under :meth:`downloading` is still running."""
        return self._calls_in_flight > 0

    @contextlib.contextmanager
    def downloading(self) -> Iterator[None]:
        """Count the block as a download in flight, for as long as it runs."""
        self._calls_in_flight += 1
        try:
            yield
        finally:
            self._calls_in_flight -= 1

    def _download_firmware_post_io(self, fw, firmware_id, dest, tmp_path):
        """Sync worker for download_firmware — file rename, hash verification, DB persist.

        Runs in an executor. The filesystem work (rename, checksum) happens
        outside any transaction; only the ``BiosFile`` upsert is wrapped in a
        short write UoW (ADR-0006).

        Returns ``md5_match``. Firmware RomM describes in a way the
        ``BiosFile`` invariants reject (empty slug/file_name) refuses with
        ``invalid_firmware``, once the renamed file is removed and with nothing
        persisted.
        """
        file_name = fw.get("file_name", "")
        self._firmware_file_store.rename(tmp_path, dest)

        expected_md5 = fw.get("md5_hash", "")
        local_md5 = self._firmware_file_store.checksum_md5(dest) if expected_md5 else None
        md5_match = local_md5 == expected_md5 if expected_md5 and local_md5 is not None else None

        try:
            bios_file = BiosFile.mark_downloaded(
                platform_slug=firmware_paths.parse_firmware_slug(fw.get("file_path", "")),
                file_name=file_name,
                file_path=dest,
                downloaded_at=self._clock.now().isoformat(),
                firmware_id=firmware_id,
            )
        except ValueError as e:
            # Malformed RomM firmware (e.g. file_path with no parseable slug):
            # the aggregate's invariant rejects it. Drop the renamed file so we
            # don't leave it untracked.
            self._firmware_file_store.remove_file(dest)
            self._logger.error(f"Failed to persist firmware {file_name}: Invalid firmware metadata: {e}")
            raise Refused("invalid_firmware", f"Invalid firmware metadata: {e}") from e

        with self._uow_factory() as uow:
            uow.bios_files.save(bios_file)

        return md5_match

    async def download_firmware(self, firmware_id) -> dict[str, Any]:
        """Download one firmware file — with none of the batch's eligibility checks.

        The folder-declaration refusal is among them, and no endpoint reaches
        this method — ``FirmwareService.download_firmware``, the use case over
        it, has none — which is the only reason that gap is unreachable.
        """
        await self._loop.run_in_executor(None, self._demand.download_root)
        placements = await self._loop.run_in_executor(None, self._demand.placement_index)
        return await self._download_one(firmware_id, placements)

    async def _download_one(self, firmware_id, placements: Mapping[str, FirmwarePlacement]) -> dict[str, Any]:
        """Fetch, place and record one firmware file against a pre-read demand index.

        The index is a parameter rather than a per-call read so a batch pays for
        the machine-wide question once instead of once per file.

        A RomM error propagates. Where this device cannot create the folder or
        prepare the temporary file, the ``.tmp`` is removed and the download
        refuses with ``bios_download_failed``; the adapter answers a failed
        transfer as a RomM error, so an ``OSError`` here is this device's.
        """
        firmware_id = int(firmware_id)
        fw = await self._loop.run_in_executor(None, self._romm_api.get_firmware, firmware_id)

        file_name = fw.get("file_name", "")
        try:
            dest = self._demand.dest_path(fw, placements.get(file_name))
        except PathTraversalError as e:
            self._logger.error(f"Rejected firmware with unsafe file name {file_name!r}: {e}")
            raise Refused("path_traversal", "Server sent an unsafe firmware file name — download aborted") from e
        tmp_path = dest + TMP_EXT

        try:
            await self._loop.run_in_executor(None, self._firmware_file_store.make_dirs, os.path.dirname(dest))
            await self._loop.run_in_executor(None, self._romm_api.download_firmware, firmware_id, file_name, tmp_path)
        except Exception as e:
            await self._loop.run_in_executor(None, self._firmware_file_store.remove_file, tmp_path)
            self._logger.error(f"Failed to download firmware {file_name}: {e}")
            if isinstance(e, OSError):
                raise Refused(
                    "bios_download_failed",
                    f"{file_name} could not be downloaded: its folder or temporary file could not be prepared "
                    "on this device",
                ) from e
            raise

        md5_match = await self._loop.run_in_executor(
            None, self._download_firmware_post_io, fw, firmware_id, dest, tmp_path
        )

        self._listing.invalidate()
        self._logger.info(f"Firmware downloaded: {file_name} -> {dest}")
        return {"success": True, "file_path": dest, "md5_match": md5_match}

    async def _platform_firmware_rows(self, platform_slug) -> list[dict[str, Any]]:
        """The library rows filed under *platform_slug*.

        The three download entry points ask the same two questions first — what
        does the library hold, and which of it is this platform's — and the
        second is not a plain slug match: ``psx`` is filed under ``psx`` and
        ``ps`` both. Answering it in one place is what keeps a button from
        fetching a set the button beside it would not.

        A listing that could not be read propagates as it was raised.
        """
        firmware_list = await self._loop.run_in_executor(None, self._listing.get_firmware_list)

        fw_slugs = firmware_paths.resolve_firmware_slugs(platform_slug)
        # One row per name, the first listed: RomM may list a name in both of a
        # platform's folders, and every copy lands at the same destination.
        rows: dict[str, dict[str, Any]] = {}
        for fw in firmware_list:
            if firmware_paths.parse_firmware_slug(fw.get("file_path", "")) in fw_slugs:
                rows.setdefault(fw.get("file_name", ""), fw)
        return list(rows.values())

    async def download_all_firmware(self, platform_slug) -> dict[str, Any]:
        """Download all firmware for a given platform slug."""
        await self._loop.run_in_executor(None, self._demand.download_root)
        platform_firmware = await self._platform_firmware_rows(platform_slug)

        placements = await self._platform_placements(self._resolve_system(platform_slug))
        downloaded, errors = await self._download_firmware_batch(platform_firmware, placements)

        msg = f"Downloaded {downloaded} firmware files"
        if errors:
            msg += f" ({len(errors)} failed: {', '.join(errors)})"
        return {"success": True, "message": msg, "downloaded": downloaded}

    async def _download_firmware_batch(
        self, platform_firmware, placements: Mapping[str, FirmwarePlacement]
    ) -> tuple[int, list[str]]:
        """Download a batch of firmware files, skipping already-downloaded ones.

        *placements* is the machine's demand index, read once by the caller: the
        question costs hundreds of milliseconds, and a batch that asked it per
        file would pay that for every download.

        The already-there skip probes the disk rather than reading the
        catalogue's answer (:meth:`FirmwareDemand.is_downloaded`) — that answer
        predates every download this batch has performed, and re-reading the
        whole machine per file to refresh it is the cost the index exists to
        avoid.

        A folder declaration is skipped whatever is at its destination: the
        emulator lists that name, so there is no file to fetch into it.

        A file whose download refuses or meets a RomM error is logged, named in
        the errors and passed over; anything else ends the batch.
        """
        downloaded = 0
        errors = []
        for fw in platform_firmware:
            placement = placements.get(fw.get("file_name", ""))
            if placement is not None and placement.declares_directory:
                continue
            dest = self._demand.safe_dest_path(fw, placement)
            if dest is not None and self._firmware_file_store.exists(dest):
                continue
            name = fw.get("file_name", str(fw["id"]))
            try:
                await self._download_one(fw["id"], placements)
            except (Refused, DomainRefused, RommApiError) as e:
                self._logger.error(f"Failed to download firmware {name}: {e}")
                errors.append(name)
                continue
            downloaded += 1
        return downloaded, errors

    async def download_platform_firmware_file(self, platform_slug, file_name) -> dict[str, Any]:
        """Download the one firmware file *file_name* the library holds for *platform_slug*.

        The per-row Download button's backend. Addressed by name within the
        platform rather than by RomM's firmware id: the id is the server's, and
        the row it would come from is a status row the page may have been holding
        for a while — resolving the name against the current listing here keeps
        the platform scoping identical to :meth:`download_all_firmware` and the
        two buttons beside it. A name the platform's listing does not hold is a
        ``not_in_library`` refusal, never a silent no-op — and a plain reason
        rather than ``NOT_FOUND``, which is RomM's entity layer answering and
        carries deletion authority downstream.

        A folder declaration is refused on the same condition the batch skips it
        on, and for the same reason: the emulator opens that name as a directory,
        so there is no file to fetch into it. Where the batch is sweeping a set
        and simply passes over the row, this answers one file the user named, so
        it says why — a silent success over a download that never happened would
        leave the row unchanged with nothing to explain it.

        Answers in the batch shape (``downloaded`` 0 or 1) because the file may
        already be at its destination, which the batch skips — the same outcome
        as pressing Download all with nothing left to fetch. What it does not
        borrow from the batch is the error fold: one press wants the reason the
        one file failed, so the single fetch's refusal or RomM error reaches the
        caller as it stands rather than collapsed into a name in a list.
        """
        await self._loop.run_in_executor(None, self._demand.download_root)
        rows = await self._platform_firmware_rows(platform_slug)

        wanted = [fw for fw in rows if fw.get("file_name") == file_name]
        if not wanted:
            raise Refused("not_in_library", f"{file_name} is not in your RomM library for {platform_slug}")

        placements = await self._platform_placements(self._resolve_system(platform_slug))
        fw = wanted[0]
        placement = placements.get(file_name)
        if placement is not None and placement.declares_directory:
            raise Refused("declares_directory", f"{file_name} is a folder the emulator opens, not a file to download")

        # The already-there skip probes the disk rather than reading the
        # catalogue, for the reason the batch states: the catalogue's answer
        # predates every download since it was read.
        dest = self._demand.safe_dest_path(fw, placement)
        if dest is not None and self._firmware_file_store.exists(dest):
            return {"success": True, "message": f"{file_name} is already here", "downloaded": 0}

        result = await self._download_one(fw["id"], placements)
        return {**result, "message": f"Downloaded {file_name}", "downloaded": 1}

    async def download_required_firmware(self, platform_slug) -> dict[str, Any]:
        """Download only the firmware the platform's launching emulator will not run without.

        The emulator is the platform's own pick — the per-platform override when
        it still resolves, else the es_systems default — which is the same pick
        the status surfaces judge by. What is fetched is
        :func:`~domain.firmware_groups.fetched_as_required`'s answer, the same
        rule each row's ``fetch_for_required`` carries to the button's count: the
        files the emulator requires, and the options of its one-of groups that
        serve a region nothing in place covers yet. A pick the resolver could not
        identify falls back to "any emulator requires it".
        """
        await self._loop.run_in_executor(None, self._demand.download_root)
        rows = await self._platform_firmware_rows(platform_slug)

        system = self._resolve_system(platform_slug)
        identity = await self._loop.run_in_executor(None, self._platform_emulator_identity, system, platform_slug)
        catalogue = await self._platform_catalogue(system)
        placements = catalogue.by_file_name()
        groups = catalogue.groups_for(identity)
        platform_firmware = [
            fw for fw in rows if fetched_as_required(placements.get(fw.get("file_name", "")), identity, groups)
        ]

        downloaded, errors = await self._download_firmware_batch(platform_firmware, placements)

        msg = f"Downloaded {downloaded} required firmware files"
        if errors:
            msg += f" ({len(errors)} failed: {', '.join(errors)})"
        return {"success": True, "message": msg, "downloaded": downloaded}

    def _platform_emulator_identity(self, system: str, platform_slug: str) -> str | None:
        """The identity of the platform's resolved emulator, or ``None``.

        The same resolution the status surfaces read
        (:func:`domain.emulator_commands.resolve_platform_option`), asked here
        rather than taken off a status payload because this path never builds
        one — and asked the same way, so a third answer to "which emulator is
        this platform about" cannot appear behind a download button.

        Takes both vocabularies because it needs both: the emulator list is keyed
        by the resolved *system* (ADR-0010 §2) and the per-platform override by
        the raw *platform_slug*.
        """
        options = self._core_info.get_emulator_options(system)
        emulator = resolve_platform_option(
            options["options"], self._platform_core_reader.get_platform_core(platform_slug)
        )
        return emulator.emulator if emulator is not None else None

    async def _platform_placements(self, system: str) -> Mapping[str, FirmwarePlacement]:
        """Where *system*'s firmware files go, read off that platform's own demand."""
        return (await self._platform_catalogue(system)).by_file_name()

    async def _platform_catalogue(self, system: str) -> FirmwareCatalogue:
        """*system*'s own demand — where its files go, and the groups its emulators state.

        The platform-scoped reading rather than the whole machine's, for the same
        reason the status surfaces take it: a standalone emulator's declarations
        reach a platform only through the emulators ES-DE offers for it. The
        machine-wide reading does carry standalone entries, and being unverified
        it carries only what a card can name without reading bytes — so a file a
        content-identified card is the one declarer of would land in the flat
        fallback instead of where it will be opened from.
        """
        return await self._loop.run_in_executor(None, self._demand.platform_catalogue, system)
