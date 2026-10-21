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

from domain.bios_file import BiosFile
from domain.emulator_commands import resolve_firmware_option
from domain.firmware_groups import fetched_as_required
from domain.retrodeck_folders import BIOS_DOWNLOAD
from domain.rom_files import TMP_EXT
from lib.errors import Refused, RommApiError, romm_error_log_level, with_system_text
from lib.path_safety import PathTraversalError

if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import Iterator, Mapping

    from domain.firmware_wants import FirmwareCatalogue, FirmwarePlacement
    from domain.platform_system import PlatformSystem
    from services.firmware.demand import FirmwareDemand
    from services.firmware.listing import FirmwareListing
    from services.protocols import (
        ActiveCoreReader,
        Clock,
        CoreInfoProvider,
        FirmwareFileStore,
        PlatformCoreReader,
        PlatformSystems,
        RommFirmwareApi,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class FirmwareDownloaderConfig:
    """Frozen wiring bundle handed to ``FirmwareDownloader.__init__``.

    Holds the RomM API adapter the bytes come from, the two peer sub-services
    (the listing a platform's rows are picked out of, the demand each
    destination is resolved through), the ES-DE core reads and the per-platform
    emulator override the required-only filter resolves its emulator from, the
    per-game pick a game page's download places its files for, the file
    store, the clock the download timestamp is taken from, the Unit-of-Work
    factory the record is written through, and runtime infrastructure.
    """

    romm_api: RommFirmwareApi
    listing: FirmwareListing
    demand: FirmwareDemand
    core_info: CoreInfoProvider
    platform_systems: PlatformSystems
    platform_core_reader: PlatformCoreReader
    active_core: ActiveCoreReader
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
        self._platform_systems = config.platform_systems
        self._platform_core_reader = config.platform_core_reader
        self._active_core = config.active_core
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

    def _download_firmware_post_io(self, fw, platform_slug, firmware_id, dest, tmp_path):
        """Sync worker for download_firmware — file rename, hash verification, DB persist.

        Runs in an executor. The filesystem work (rename, checksum) happens
        outside any transaction; only the ``BiosFile`` upsert is wrapped in a
        short write UoW (ADR-0006).

        Returns ``md5_match``. The record is filed under *platform_slug*, the
        platform the listing places the file on. Firmware RomM describes in a
        way the ``BiosFile`` invariants reject (an empty file name) refuses with
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
                platform_slug=platform_slug,
                file_name=file_name,
                file_path=dest,
                downloaded_at=self._clock.now().isoformat(),
                firmware_id=firmware_id,
            )
        except ValueError as e:
            # Malformed RomM firmware: the aggregate's invariant rejects it. Drop
            # the renamed file so we don't leave it untracked.
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

        A RomM error propagates unlogged: whoever answers it logs it. Every
        refusal raised here is logged here. Where this device cannot create the
        folder or write the ``.tmp`` — the adapter raises the latter as
        ``DeviceWriteError`` — the ``.tmp`` is removed and the download refuses
        with ``bios_download_failed``. Every other failure of the transfer
        reaches here as a RomM error, so an ``OSError`` here is this device's.
        """
        firmware_id = int(firmware_id)
        fw = await self._loop.run_in_executor(None, self._romm_api.get_firmware, firmware_id)
        platform_slug = await self._loop.run_in_executor(None, self._platform_of, firmware_id)

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
            if not isinstance(e, RommApiError):
                self._logger.error(f"Failed to download firmware {file_name}: {e}")
            if isinstance(e, OSError):
                raise Refused(
                    "bios_download_failed",
                    with_system_text(f"{file_name} could not be downloaded: it could not be written on this device", e),
                ) from e
            raise

        md5_match = await self._loop.run_in_executor(
            None, self._download_firmware_post_io, fw, platform_slug, firmware_id, dest, tmp_path
        )

        self._listing.invalidate()
        self._logger.info(f"Firmware downloaded: {file_name} -> {dest}")
        return {"success": True, "file_path": dest, "md5_match": md5_match}

    def _platform_of(self, firmware_id: int) -> str:
        """The platform the library files *firmware_id* on; refuses ``not_in_library`` where it holds no such file.

        A RomM error reading the listing propagates as it was raised.
        """
        for fw in self._listing.get_firmware_list():
            if fw.get("id") == firmware_id:
                return fw["platform_slug"]
        self._logger.warning(f"Firmware {firmware_id} is not in the RomM library's listing")
        raise Refused("not_in_library", f"Firmware {firmware_id} is not in your RomM library")

    async def _platform_firmware_rows(self, platform_slug) -> list[dict[str, Any]]:
        """The library rows of *platform_slug*.

        The three download entry points ask the same two questions first — what
        does the library hold, and which of it is this platform's — and answer
        them here, so a button never fetches a set the button beside it would
        not.

        A listing that could not be read propagates as it was raised.
        """
        firmware_list = await self._loop.run_in_executor(None, self._listing.get_firmware_list)
        return [fw for fw in firmware_list if fw["platform_slug"] == platform_slug]

    async def download_all_firmware(self, platform_slug, rom_id: int | None = None) -> dict[str, Any]:
        """Download all firmware for a given platform slug.

        With *rom_id*, a downloaded game's files are placed for the system its
        install record holds and where the emulator THAT game launches with opens
        them — its own pick over the platform's; without one, for the
        platform's current system and pick.
        """
        await self._loop.run_in_executor(None, self._demand.download_root)
        platform_firmware = await self._platform_firmware_rows(platform_slug)

        system = await self._platform_system(platform_slug, rom_id)
        if rom_id is None:
            placements = await self._platform_placements(system, platform_slug)
        else:
            placements = await self._rom_placements(system, platform_slug, int(rom_id))
        downloaded, errors = await self._download_firmware_batch(platform_firmware, placements)

        msg = f"Downloaded {downloaded} firmware files"
        if errors:
            msg += f" ({len(errors)} failed: {', '.join(errors)})"
        return {"success": True, "message": msg, "downloaded": downloaded, "failed": errors}

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
        emulator lists that name, so there is no file to fetch into it. So is a
        file whose place could not be looked at: a download there could
        overwrite a file nobody could see.

        A file whose download this service refuses (``Refused``, logged where it
        is refused) or that meets a RomM error (logged here, at the RomM error's
        level) is named in the errors and passed over; anything else
        ends the batch, RetroDECK's folder refusal included — no later file could
        land either.
        """
        downloaded = 0
        errors = []
        for fw in platform_firmware:
            placement = placements.get(fw.get("file_name", ""))
            if placement is not None and (placement.declares_directory or placement.place_unreadable):
                continue
            dest = self._demand.safe_dest_path(fw, placement)
            if dest is not None and self._firmware_file_store.exists(dest):
                continue
            name = fw.get("file_name", str(fw["id"]))
            try:
                await self._download_one(fw["id"], placements)
            except Refused:
                errors.append(name)
                continue
            except RommApiError as e:
                self._logger.log(
                    romm_error_log_level(e), f"Failed to download firmware {name}: {type(e).__name__}: {e}"
                )
                errors.append(name)
                continue
            downloaded += 1
        return downloaded, errors

    async def download_platform_firmware_file(
        self, platform_slug, file_name, emulator: str | None = None
    ) -> dict[str, Any]:
        """Download the one firmware file *file_name* the library holds for *platform_slug*.

        It lands where *emulator* opens it — the emulator the page shows — and,
        without one, where the platform's own pick does (:meth:`_shown_emulator`).

        The per-row Download button's backend. Addressed by name within the
        platform rather than by RomM's firmware id: the id is the server's, and
        the row it would come from is a status row the page may have been holding
        for a while — resolving the name against the current listing here keeps
        the platform scoping identical to :meth:`download_all_firmware` and the
        two buttons beside it. A name the platform's listing does not hold is a
        ``not_in_library`` refusal, never a silent no-op — and a plain reason
        rather than ``NOT_FOUND``, which is RomM's entity layer answering and
        carries deletion authority downstream.

        A file whose place could not be looked at is refused as the batch skips
        it. A folder declaration is refused on the same condition the batch skips it
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

        system = await self._platform_system(platform_slug)
        catalogue = await self._platform_catalogue(system)
        identity = await self._shown_emulator(catalogue, system, platform_slug, emulator)
        placements = catalogue.placements_for(identity)
        fw = wanted[0]
        placement = placements.get(file_name)
        if placement is not None and placement.declares_directory:
            raise Refused("declares_directory", f"{file_name} is a folder the emulator opens, not a file to download")
        if placement is not None and placement.place_unreadable:
            raise Refused(
                "place_unreadable",
                f"The place {file_name} goes could not be looked at, so a download could overwrite a file nobody saw",
            )

        # The already-there skip probes the disk rather than reading the
        # catalogue, for the reason the batch states: the catalogue's answer
        # predates every download since it was read.
        dest = self._demand.safe_dest_path(fw, placement)
        if dest is not None and self._firmware_file_store.exists(dest):
            return {"success": True, "message": f"{file_name} is already here", "downloaded": 0}

        result = await self._download_one(fw["id"], placements)
        return {**result, "message": f"Downloaded {file_name}", "downloaded": 1}

    async def download_required_firmware(self, platform_slug, emulator: str | None = None) -> dict[str, Any]:
        """Download only the firmware *emulator* will not run without, where it opens it.

        *emulator* is the one the page shows; without one it is the platform's
        own pick — the per-platform override when it still resolves, else the
        es_systems default — which is the same pick the status surfaces judge
        by (:meth:`_shown_emulator`). What is fetched is
        :func:`~domain.firmware_groups.fetched_as_required`'s answer, the same
        rule each row's ``fetch_for_required`` carries to the button's count: the
        files the emulator requires, and the options of its one-of groups that
        serve a region nothing in place covers yet. A pick the resolver could not
        identify fetches nothing, and answers as an emulator that requires
        nothing does.
        """
        await self._loop.run_in_executor(None, self._demand.download_root)
        rows = await self._platform_firmware_rows(platform_slug)

        system = await self._platform_system(platform_slug)
        catalogue = await self._platform_catalogue(system)
        identity = await self._shown_emulator(catalogue, system, platform_slug, emulator)
        placements = catalogue.placements_for(identity)
        groups = catalogue.groups_for(identity)
        platform_firmware = [
            fw for fw in rows if fetched_as_required(placements.get(fw.get("file_name", "")), identity, groups)
        ]

        downloaded, errors = await self._download_firmware_batch(platform_firmware, placements)

        msg = f"Downloaded {downloaded} required firmware files"
        if errors:
            msg += f" ({len(errors)} failed: {', '.join(errors)})"
        return {"success": True, "message": msg, "downloaded": downloaded, "failed": errors}

    async def _shown_emulator(
        self, catalogue: FirmwareCatalogue, system: str, platform_slug: str, emulator: str | None
    ) -> str | None:
        """The emulator a download is for: the one the page shows, or the platform's pick.

        A named emulator must be one the answer holds: placing for an emulator
        nobody answered for would fall back to the first declarer's place, which
        is where some OTHER emulator opens the file.
        """
        if emulator is None:
            return await self._loop.run_in_executor(None, self._platform_emulator_identity, system, platform_slug)
        if catalogue.verdict_for(emulator) is None:
            raise Refused("emulator_unknown", f"Nothing is known about where {emulator} opens its BIOS files")
        return emulator

    def _platform_emulator_identity(self, system: str, platform_slug: str) -> str | None:
        """The identity of the platform's resolved emulator, or ``None``.

        The same resolution the status surfaces read
        (:func:`domain.emulator_commands.resolve_firmware_option`), asked here
        rather than taken off a status payload because this path never builds
        one — and asked the same way, so a third answer to "which emulator is
        this platform about" cannot appear behind a download button.

        Takes both vocabularies because it needs both: the emulator list is keyed
        by the resolved *system* (ADR-0010 §2) and the per-platform override by
        the raw *platform_slug*.
        """
        options = self._core_info.get_emulator_options(system)
        emulator = resolve_firmware_option(
            options["options"], self._platform_core_reader.get_platform_core(platform_slug)
        )
        return emulator.emulator if emulator is not None else None

    async def _platform_system(self, platform_slug: str, rom_id: int | None = None) -> str:
        """The system *platform_slug* is in the answering source — or the one *rom_id*'s install record holds.

        Refused like a download where there is none.
        """
        platform = await self._loop.run_in_executor(None, self._platform_system_io, platform_slug, rom_id)
        if platform.taken is None:
            raise platform.refusal(BIOS_DOWNLOAD)
        return platform.taken

    def _platform_system_io(self, platform_slug: str, rom_id: int | None) -> PlatformSystem:
        if rom_id is None:
            return self._platform_systems.platform_system(platform_slug)
        with self._uow_factory() as uow:
            install = uow.rom_installs.get(int(rom_id))
        return self._platform_systems.rom_system(platform_slug, install)

    async def _platform_placements(self, system: str, platform_slug: str) -> Mapping[str, FirmwarePlacement]:
        """Where *system*'s firmware files go for the platform's launching emulator.

        Each file goes to the place the resolver names for THAT emulator —
        with YabaSanshiro picked for Saturn, ``saturn_bios.bin`` lands
        directly under the BIOS root, not under Kronos's ``kronos/``. A file it
        does not declare goes where its first declarer opens it.
        """
        identity = await self._loop.run_in_executor(None, self._platform_emulator_identity, system, platform_slug)
        return (await self._platform_catalogue(system)).placements_for(identity)

    async def _rom_placements(self, system: str, platform_slug: str, rom_id: int) -> Mapping[str, FirmwarePlacement]:
        """Where *system*'s firmware files go for the emulator the game *rom_id* launches with.

        The game's own pick — its override, then the platform's, then the
        system default (``ActiveCoreReader.active_emulator_for_rom``) — so a
        game set to Kronos on a YabaSanshiro platform gets ``kronos/saturn_bios.bin``.
        Where the game resolves to none, the platform's BIOS pick answers
        (:meth:`_platform_emulator_identity`), as it does for the game page's
        BIOS tab, so the play row fetches for the emulator that tab names.
        """
        invocation = await self._loop.run_in_executor(None, self._active_core.active_emulator_for_rom, rom_id)
        if invocation is not None:
            identity = invocation.emulator
        else:
            identity = await self._loop.run_in_executor(None, self._platform_emulator_identity, system, platform_slug)
        return (await self._platform_catalogue(system)).placements_for(identity)

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
