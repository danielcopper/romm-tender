"""Fetching firmware out of the RomM library into Tender's firmware store, and linking it.

Every entry point fetches into the store folder of the platform it is pressed
for (:meth:`FirmwareStore.do_fetch`) and then brings that platform's links up to
date in every enabled source (:meth:`FirmwareStore.do_update_links`), so a file
reaches each emulator that wants it and its source does not state is not
installed, at the place the resolver names. No download lands outside the
store. A library file fills a requirement by its content wherever RomM files it
(:mod:`domain.firmware_match`).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.emulator_commands import resolve_firmware_option
from domain.emulator_pick import pick_for, source_kind
from domain.firmware_groups import fetched_as_required
from domain.firmware_rows import LibraryFirmware, StoreCopy, folder_entries, library_file, library_match, store_copy
from lib.errors import Refused, RommApiError, romm_error_log_level

if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import Iterator

    from domain.firmware_wants import FirmwareCatalogue, FirmwarePlacement
    from domain.platform_system import PlatformSystem
    from services.firmware.demand import FirmwareDemand
    from services.firmware.listing import FirmwareListing
    from services.firmware.store import FirmwareStore, LinkOutcome
    from services.protocols import (
        CoreInfoProvider,
        FirmwareFileStore,
        PlatformCoreReader,
        PlatformSystems,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class FirmwareDownloaderConfig:
    """Frozen wiring bundle handed to ``FirmwareDownloader.__init__``.

    Holds the three peer sub-services (the listing a platform's files are
    picked out of, the demand each requirement is read from, the store files
    are fetched into and linked from), the ES-DE core reads and the
    per-platform emulator override the required-only filter resolves its
    emulator from, the per-game pick a game page's press is for, the file
    store places are looked at through, the Unit-of-Work factory an install
    record is read through, and runtime infrastructure.
    """

    listing: FirmwareListing
    demand: FirmwareDemand
    store: FirmwareStore
    core_info: CoreInfoProvider
    platform_systems: PlatformSystems
    platform_core_reader: PlatformCoreReader
    firmware_file_store: FirmwareFileStore
    uow_factory: UnitOfWorkFactory
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class FirmwareDownloader:
    """The firmware download entry points: into the store, then linked."""

    def __init__(self, *, config: FirmwareDownloaderConfig) -> None:
        self._listing = config.listing
        self._demand = config.demand
        self._store = config.store
        self._core_info = config.core_info
        self._platform_systems = config.platform_systems
        self._platform_core_reader = config.platform_core_reader
        self._files = config.firmware_file_store
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

    async def download_all_firmware(self, platform_slug, rom_id: int | None = None) -> dict[str, Any]:
        """Fetch every firmware file RomM lists for *platform_slug* into its store folder, and link them.

        A file no emulator asks for lands in the store too, and is linked once
        one does. With *rom_id*, the links follow the system the game's install
        record holds.
        """
        firmware_list = await self._firmware_list()
        rows = [fw for fw in firmware_list if fw["platform_slug"] == platform_slug]
        return await self._fetch_and_link(platform_slug, rows, await self.platform_system(platform_slug, rom_id))

    async def download_platform_firmware_file(
        self, platform_slug, file_name, firmware_id: int | None = None, rom_id: int | None = None
    ) -> dict[str, Any]:
        """Fetch the library file *file_name* into *platform_slug*'s store folder, and link it.

        *firmware_id* is the library file the row shows, so the file fetched is
        that one even where two files share its name. An id the listing does
        not hold under *file_name* counts for nothing, and neither does a missing
        one: the name — the file's name in RomM — is then looked up among the
        files RomM lists for the platform first, and then in the whole library,
        since a file fills a requirement wherever RomM files it. A name the
        library does not hold is a ``not_in_library`` refusal, never a silent
        no-op. A single file's refusal or RomM error reaches the caller as it
        stands.
        """
        firmware_list = await self._firmware_list()
        named = [fw for fw in firmware_list if fw.get("file_name") == file_name]
        own = [fw for fw in named if fw["platform_slug"] == platform_slug]
        exact = [fw for fw in named if firmware_id is not None and fw.get("id") == firmware_id]
        wanted = exact or own or named
        if not wanted:
            raise Refused("not_in_library", f"{file_name} is not in your RomM library for {platform_slug}")
        fetched = await self._loop.run_in_executor(None, self._store.do_fetch, platform_slug, wanted[0])
        links = await self._update(platform_slug, await self.platform_system(platform_slug, rom_id))
        return {
            "success": True,
            "message": f"Downloaded {file_name}" if fetched.downloaded else f"{file_name} is already here",
            "downloaded": 1 if fetched.downloaded else 0,
            "backups": [fetched.backup] if fetched.backup else [],
            **links.payload(),
        }

    async def download_required_firmware(
        self, platform_slug, emulator: str | None = None, rom_id: int | None = None
    ) -> dict[str, Any]:
        """Fetch what *emulator* will not run without, where nothing lies at its place yet, and link it.

        *emulator* is the one the page shows; without one it is the platform's
        own pick (:meth:`_shown_emulator`). What is fetched is
        :func:`~domain.firmware_groups.fetched_as_required`'s answer, the rule
        each row's ``fetch_for_required`` carries to the button's count, less
        what the store already holds and every place something lies at: a file
        there is never replaced from here, and a link of Tender's into another
        platform's folder already serves the place.
        """
        system = await self.platform_system(platform_slug, rom_id)
        catalogue = await self._loop.run_in_executor(None, self._demand.platform_catalogue, system)
        identity = await self._shown_emulator(catalogue, system, platform_slug, emulator)
        firmware_list = await self._firmware_list()
        rows = await self._loop.run_in_executor(
            None, self._required_files, catalogue, platform_slug, identity, firmware_list
        )
        return await self._fetch_and_link(platform_slug, rows, system, what="required firmware files")

    async def _fetch_and_link(
        self, platform_slug: str, rows: list[dict[str, Any]], system: str | None, *, what: str = "firmware files"
    ) -> dict[str, Any]:
        """Fetch each of *rows* into the store, then bring the platform's links up to date once.

        A file whose fetch this service refuses (logged where it is refused) or
        that meets a RomM error (logged here, at the RomM error's level) is named
        in ``failed`` and passed over; anything else ends the batch, Tender's
        folder that cannot be reached included — no later file could land either.
        """
        downloaded = 0
        failed: list[str] = []
        backups: list[str] = []
        for fw in rows:
            name = fw.get("file_name", str(fw.get("id")))
            try:
                fetched = await self._loop.run_in_executor(None, self._store.do_fetch, platform_slug, fw)
            except Refused as exc:
                if exc.reason == "folder_unreachable":
                    raise
                failed.append(name)
                continue
            except RommApiError as exc:
                self._logger.log(
                    romm_error_log_level(exc), f"Failed to download firmware {name}: {type(exc).__name__}: {exc}"
                )
                failed.append(name)
                continue
            downloaded += 1 if fetched.downloaded else 0
            if fetched.backup:
                backups.append(fetched.backup)
        links = await self._update(platform_slug, system)
        message = f"Downloaded {downloaded} {what}"
        if failed:
            message += f" ({len(failed)} failed: {', '.join(failed)})"
        return {
            "success": True,
            "message": message,
            "downloaded": downloaded,
            "failed": failed,
            "backups": backups,
            **links.payload(),
        }

    async def _update(self, platform_slug: str, system: str | None) -> LinkOutcome:
        outcome = await self._loop.run_in_executor(None, self._store.do_update_links, platform_slug, system)
        self._listing.invalidate()
        return outcome

    def _required_files(
        self,
        catalogue: FirmwareCatalogue,
        platform_slug: str,
        identity: str | None,
        firmware_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """The library files "Download required" fetches for *identity*. Blocking: it looks at each place.

        A file requirement's library file, where the store holds no copy and
        nothing lies at its place. A folder the emulator requires and lists,
        whose verdict is not met: its alphabetically first recognised library
        file, unless the store already holds one it recognises.
        """
        placements = catalogue.placements_for(identity)
        groups = catalogue.groups_for(identity)
        library = [library_file(fw) for fw in firmware_list]
        by_file = dict(zip(library, firmware_list, strict=True))
        copies = self._store.do_copies(self._store.do_read_records(), platform_slug)
        wanted: dict[int | None, dict[str, Any]] = {}
        for placement in placements.values():
            if not fetched_as_required(placement, identity, groups):
                continue
            file = self._required_file(placement, library, copies)
            if file is not None:
                wanted.setdefault(file.firmware_id, by_file[file])
        return list(wanted.values())

    def _required_file(
        self, placement: FirmwarePlacement, library: list[LibraryFirmware], copies: list[StoreCopy]
    ) -> LibraryFirmware | None:
        if placement.declares_directory:
            if placement.satisfied is True:
                return None
            entries = folder_entries(placement, library, copies)
            if any(copy is not None for _name, _file, copy in entries):
                return None
            return next((file for _name, file, _copy in entries if file is not None), None)
        if store_copy(placement, copies) is not None or placement.place is None:
            return None
        if self._files.look(placement.place).lexists:
            return None
        match = library_match(placement, library)
        if match is None:
            return None
        return next((file for file in library if file.firmware_id == match["firmware_id"]), None)

    async def _firmware_list(self) -> list[dict[str, Any]]:
        """The whole library's firmware; a listing that could not be read propagates as it was raised."""
        return await self._loop.run_in_executor(None, self._listing.get_firmware_list)

    async def _shown_emulator(
        self, catalogue: FirmwareCatalogue, system: str | None, platform_slug: str, emulator: str | None
    ) -> str | None:
        """The emulator a "Download required" is for: the one the page shows, or the platform's pick.

        A named emulator must be one the answer holds: fetching for an emulator
        nobody answered for would fetch for a requirement nobody stated.
        """
        if emulator is None:
            if system is None:
                return None
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
        """
        options = self._core_info.get_emulator_options(system)
        pick = pick_for(self._platform_core_reader.get_platform_core(platform_slug), source_kind(options))
        emulator = resolve_firmware_option(options["options"], pick.label if pick is not None else None)
        return emulator.emulator if emulator is not None else None

    async def platform_system(self, platform_slug: str, rom_id: int | None = None) -> str | None:
        """The system *platform_slug* is in the answering source — or the one *rom_id*'s install record holds.

        ``None`` where no source answers for it: a download then lands in the
        store and nothing is linked.
        """
        platform = await self._loop.run_in_executor(None, self._platform_system_io, platform_slug, rom_id)
        return platform.taken

    def _platform_system_io(self, platform_slug: str, rom_id: int | None) -> PlatformSystem:
        if rom_id is None:
            return self._platform_systems.platform_system(platform_slug)
        with self._uow_factory() as uow:
            install = uow.rom_installs.get(int(rom_id))
        return self._platform_systems.rom_system(platform_slug, install)
