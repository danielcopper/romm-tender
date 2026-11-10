"""FirmwareService façade.

Owns the public use cases the endpoints call (the whole-library overview, the
per-platform BIOS check, the downloads, bringing a platform's links up to date,
a replacement, what a library file is, the deletes) and composes the
sub-services behind it. Implementation lives in those modules:
:class:`FirmwareListing` for what the RomM library holds and its cache,
:class:`FirmwareDemand` for what the installed emulators want,
:class:`FirmwareStore` for Tender's firmware store and its links,
:class:`FirmwareStatusReader` for every status-bearing answer,
:class:`FirmwareDownloader` for fetching, :class:`FirmwareReplacer` for
"Replace…", :class:`PlatformBiosDeleter` for removing what Tender put there.
The façade itself only wires the pieces together, checks each use case's
conflict rules and delegates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lib.errors import Refused
from services.firmware.deletion import FirmwareDeletionIncomplete, PlatformBiosDeleter, PlatformBiosDeleterConfig
from services.firmware.demand import FirmwareDemand, FirmwareDemandConfig
from services.firmware.downloads import FirmwareDownloader, FirmwareDownloaderConfig
from services.firmware.listing import FirmwareListing, FirmwareListingConfig
from services.firmware.replacement import FirmwareReplacer, FirmwareReplacerConfig
from services.firmware.status import FirmwareStatusReader, FirmwareStatusReaderConfig

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.emulator_commands import LaunchingEmulator
    from domain.rom_install import RomInstall
    from services.firmware.store import FirmwareStore
    from services.protocols import (
        ActiveCoreReader,
        Clock,
        ConflictRules,
        CoreInfoProvider,
        FirmwareFileStore,
        FirmwareIdentityProvider,
        FirmwarePlatformResolver,
        ListingRefiler,
        PlatformCoreReader,
        PlatformSystems,
        RetroDeckFolders,
        RommFirmwareApi,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class FirmwareServiceConfig:
    """Frozen wiring bundle handed to ``FirmwareService.__init__``.

    Holds the API adapter, runtime infrastructure, Protocol-typed file
    adapters, the resolver seams (the per-platform demand and the
    identification of a piece of content), Tender's firmware store — built
    before this service, because Tender's folder asks it what is recorded
    under a folder — the SQLite Unit-of-Work factory, the refiling a fresh read
    of RomM's platform list is handed to, and the provider callables the
    firmware subsystem needs at construction time.
    """

    romm_api: RommFirmwareApi
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    clock: Clock
    firmware_file_store: FirmwareFileStore
    platform_firmware_resolver: FirmwarePlatformResolver
    firmware_identifier: FirmwareIdentityProvider
    store: FirmwareStore
    retrodeck_folders: RetroDeckFolders
    core_info: CoreInfoProvider
    platform_systems: PlatformSystems
    platform_core_reader: PlatformCoreReader
    active_core: ActiveCoreReader
    uow_factory: UnitOfWorkFactory
    conflict_rules: ConflictRules
    refiling: ListingRefiler


class FirmwareService:
    """BIOS/firmware management: what is wanted, what is here, the store and its links, and removal.

    Each use case an endpoint calls that writes into Tender's folder or an
    emulator source checks its conflict rules here, under its name, and raises
    the rule's refusal when one holds (GLOSSARY.md → Conflict rules); the
    sub-services check none. Each names the folder rule: the store lies in
    Tender's folder and every link points into it.
    """

    def __init__(
        self,
        *,
        config: FirmwareServiceConfig,
    ) -> None:
        self._config = config
        self._rules = config.conflict_rules
        self._identifier = config.firmware_identifier
        self._store = config.store

        # Sub-service: the RomM listing and its cache. Constructed first —
        # every other sub-service either reads the listing or invalidates it,
        # and they share this one instance so a download's invalidation is seen
        # by the query that follows it.
        self._listing = FirmwareListing(
            config=FirmwareListingConfig(
                romm_api=config.romm_api,
                logger=config.logger,
                clock=config.clock,
                uow_factory=config.uow_factory,
                refiling=config.refiling,
            )
        )

        self._demand = FirmwareDemand(
            config=FirmwareDemandConfig(platform_firmware_resolver=config.platform_firmware_resolver)
        )

        self._status = FirmwareStatusReader(
            config=FirmwareStatusReaderConfig(
                demand=self._demand,
                listing=self._listing,
                store=config.store,
                core_info=config.core_info,
                platform_systems=config.platform_systems,
                platform_core_reader=config.platform_core_reader,
                firmware_file_store=config.firmware_file_store,
                uow_factory=config.uow_factory,
                refiling=config.refiling,
                loop=config.loop,
                logger=config.logger,
            )
        )

        self._downloads = FirmwareDownloader(
            config=FirmwareDownloaderConfig(
                listing=self._listing,
                demand=self._demand,
                store=config.store,
                core_info=config.core_info,
                platform_systems=config.platform_systems,
                platform_core_reader=config.platform_core_reader,
                firmware_file_store=config.firmware_file_store,
                uow_factory=config.uow_factory,
                loop=config.loop,
                logger=config.logger,
            )
        )

        self._replacer = FirmwareReplacer(
            config=FirmwareReplacerConfig(
                listing=self._listing,
                demand=self._demand,
                store=config.store,
                firmware_file_store=config.firmware_file_store,
                loop=config.loop,
                logger=config.logger,
            )
        )

        self._deletion = PlatformBiosDeleter(
            config=PlatformBiosDeleterConfig(
                listing=self._listing,
                store=config.store,
                firmware_file_store=config.firmware_file_store,
                retrodeck_folders=config.retrodeck_folders,
                uow_factory=config.uow_factory,
                refiling=config.refiling,
                loop=config.loop,
                logger=config.logger,
            )
        )

    def is_downloading(self) -> bool:
        """Whether a firmware download is under way."""
        return self._downloads.is_downloading()

    def invalidate_firmware_cache(self) -> None:
        """Clear the cached firmware list so the next query re-fetches."""
        self._listing.invalidate()

    async def get_firmware_status(self) -> dict[str, Any]:
        """Return whether the RomM firmware listing could be read, without reading any platform's state."""
        return await self._status.get_firmware_status()

    async def get_platform_firmware_status(self, platform_slug, platform_folder) -> dict[str, Any]:
        """Return one platform's whole BIOS entry."""
        _require_folder(platform_folder)
        return await self._status.get_platform_firmware_status(platform_slug, platform_folder)

    async def check_platform_bios(
        self,
        platform_slug,
        platform_folder,
        launching_emulator: LaunchingEmulator | None = None,
        rom_regions: tuple[str, ...] = (),
        install: RomInstall | None = None,
    ) -> dict[str, Any]:
        """Return the platform's BIOS status, filtered by what *launching_emulator* needs.

        *rom_regions* narrows a one-of group to the game's own regions; an
        *install* is asked about the system it records.
        """
        _require_folder(platform_folder)
        return await self._status.check_platform_bios(
            platform_slug, platform_folder, launching_emulator, rom_regions, install
        )

    async def identify_library_firmware(self, md5: object) -> dict[str, Any]:
        """Every name the resolver knows the content *md5* under; ``known_as`` ``None`` where nothing could be asked.

        Asked only about the library files a page lists as asked for by no
        emulator, after the page is shown. Refuses ``invalid_value`` for
        anything but a checksum's hex digits.
        """
        if not isinstance(md5, str) or not md5 or any(c not in "0123456789abcdefABCDEF" for c in md5):
            raise Refused("invalid_value", f"{md5!r} is not an MD5 checksum")
        known_as = await self._config.loop.run_in_executor(None, self._identifier, md5.lower())
        return {"success": True, "known_as": list(known_as) if known_as is not None else None}

    async def download_all_firmware(self, platform_slug, platform_folder, rom_id: int | None = None) -> dict[str, Any]:
        """Download every firmware file RomM lists for a platform into the store, and link it."""
        _require_folder(platform_folder)
        async with self._rules.hold("download_all_firmware", update=True, folder=True, migration=True):
            with self._downloads.downloading():
                return await self._downloads.download_all_firmware(platform_slug, platform_folder, rom_id)

    async def download_platform_firmware_file(
        self, platform_slug, platform_folder, file_name, firmware_id: int | None = None, rom_id: int | None = None
    ) -> dict[str, Any]:
        """Download one library file into a platform's store folder — by its id where given — and link it.

        With *rom_id*, the links follow the system the game's install record holds.
        """
        _require_folder(platform_folder)
        async with self._rules.hold("download_platform_firmware_file", update=True, folder=True, migration=True):
            with self._downloads.downloading():
                return await self._downloads.download_platform_firmware_file(
                    platform_slug, platform_folder, file_name, firmware_id, rom_id
                )

    async def download_required_firmware(
        self, platform_slug, platform_folder, emulator: str | None = None, rom_id: int | None = None
    ) -> dict[str, Any]:
        """Download only the firmware *emulator* — the platform's pick without one — will not run without.

        With *rom_id*, the requirement is read, and the links placed, for the system the game's install record holds.
        """
        _require_folder(platform_folder)
        async with self._rules.hold("download_required_firmware", update=True, folder=True, migration=True):
            with self._downloads.downloading():
                return await self._downloads.download_required_firmware(
                    platform_slug, platform_folder, emulator, rom_id
                )

    async def update_bios_links(self, platform_slug, platform_folder, rom_id: int | None = None) -> dict[str, Any]:
        """Bring a platform's links up to date in every enabled source, for *rom_id*'s recorded system where given.

        *platform_folder* is the folder RomM keeps the platform in, whose store
        copies are linked. Places every link an emulator of the platform wants
        from the store where the name is free, puts back a link of Tender's that
        points at nothing where a store copy exists, removes it where none does,
        and downloads nothing.
        """
        _require_folder(platform_folder)
        async with self._rules.hold("update_bios_links", update=True, folder=True, migration=True):
            system = await self._downloads.platform_system(platform_slug, rom_id)
            outcome = await self._config.loop.run_in_executor(
                None, self._store.do_update_links, platform_slug, platform_folder, system
            )
            return {"success": True, **outcome.payload()}

    async def replace_bios_file(
        self, platform_slug, platform_folder, place, rom_id: int | None = None
    ) -> dict[str, Any]:
        """Replace the wrong file at *place* with Tender's proven-right copy; the old file goes to the backup folder.

        With *rom_id*, the place is read, and the links placed, for the system the game's install record holds.
        """
        _require_folder(platform_folder)
        async with self._rules.hold("replace_bios_file", update=True, folder=True, migration=True):
            with self._downloads.downloading():
                system = await self._downloads.platform_system(platform_slug, rom_id)
                return await self._replacer.replace_bios_file(platform_slug, platform_folder, place, system)

    async def delete_platform_bios(self, platform_slug, platform_folder) -> dict[str, Any] | FirmwareDeletionIncomplete:
        """Delete every BIOS file Tender put on the machine for a platform, and its links."""
        _require_folder(platform_folder)
        async with self._rules.hold("delete_platform_bios", update=True, folder=True, migration=True):
            return await self._deletion.delete_platform_bios(platform_slug, platform_folder)

    async def delete_bios_file(
        self, platform_slug, platform_folder, file_name
    ) -> dict[str, Any] | FirmwareDeletionIncomplete:
        """Delete one BIOS file Tender put on the machine for a platform, by its name in RomM, and its links."""
        _require_folder(platform_folder)
        async with self._rules.hold("delete_bios_file", update=True, folder=True, migration=True):
            return await self._deletion.delete_bios_file(platform_slug, platform_folder, file_name)


def _require_folder(platform_folder: object) -> None:
    """Refuse ``invalid_value`` for a platform folder that is not a non-empty name.

    Every platform use case here is keyed by the folder RomM keeps the
    platform in; without one there is no store folder to read or write, and
    the slug is never taken in its place.
    """
    if not isinstance(platform_folder, str) or not platform_folder:
        raise Refused("invalid_value", f"{platform_folder!r} is not a RomM platform folder")
