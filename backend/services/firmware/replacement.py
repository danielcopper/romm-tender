"""Replacing a wrong file at an emulator's place with Tender's proven-right copy, on the user's confirmation.

The one path that changes a file Tender did not put there. It is offered on a
row only where another file lies at the place, the resolver reads that file as
wrong, and the library copy is proven right by the checksum the resolver pins
(:func:`domain.firmware_rows.place_facts`). Each of those is asked again here,
off a fresh reading, before anything moves: the page the confirmation came from
may be stale. Then, in this order, the copy is fetched into the store and
checked against RomM's checksum, the file in the way goes to the backup folder,
the link takes its place, and the platform's links are brought up to date.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.firmware_match import MATCHED_CONTENT, fills
from domain.firmware_places import is_own_link, place_refusal
from domain.firmware_rows import LibraryFirmware, library_file, library_match
from lib.errors import Refused

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.firmware_wants import FirmwareCatalogue, FirmwarePlacement
    from services.firmware.demand import FirmwareDemand
    from services.firmware.listing import FirmwareListing
    from services.firmware.store import FirmwareStore
    from services.protocols import FirmwareFileStore, PlatformSystems

# The reason a Replace whose conditions no longer hold is refused with.
NOT_REPLACEABLE = "not_replaceable"


@dataclass(frozen=True)
class FirmwareReplacerConfig:
    """Frozen wiring bundle handed to ``FirmwareReplacer.__init__``.

    Holds the peer sub-services (the listing the library copy comes from, the
    demand the place is read from, the store it is fetched into and linked
    from), the platform's system mapping, the file store the place is looked at
    through, and runtime infrastructure.
    """

    listing: FirmwareListing
    demand: FirmwareDemand
    store: FirmwareStore
    platform_systems: PlatformSystems
    firmware_file_store: FirmwareFileStore
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class FirmwareReplacer:
    """The "Replace…" press."""

    def __init__(self, *, config: FirmwareReplacerConfig) -> None:
        self._listing = config.listing
        self._demand = config.demand
        self._store = config.store
        self._platform_systems = config.platform_systems
        self._files = config.firmware_file_store
        self._loop = config.loop
        self._logger = config.logger

    async def replace_bios_file(self, platform_slug: str, place: str) -> dict[str, Any]:
        """Replace the file at *place* with Tender's copy for *platform_slug*; answer where the old file went.

        Refuses :data:`NOT_REPLACEABLE` where the conditions no longer hold,
        and as a download refuses where the copy cannot be fetched or checked
        — before the file in the way is touched.
        """
        system = (await self._loop.run_in_executor(None, self._platform_systems.platform_system, platform_slug)).taken
        firmware_list = await self._loop.run_in_executor(None, self._listing.get_firmware_list)
        firmware, source = await self._loop.run_in_executor(None, self._replaceable, system, place, firmware_list)
        backup = await self._loop.run_in_executor(None, self._replace_io, platform_slug, place, firmware, source)
        links = await self._loop.run_in_executor(None, self._store.do_update_links, platform_slug, system)
        self._listing.invalidate()
        self._logger.info(f"Replaced {place} with Tender's copy; the old file is at {backup}")
        return {"success": True, "backup_path": backup, **links.payload(), "placed": links.placed + 1}

    def _replaceable(
        self, system: str | None, place: str, firmware_list: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], str]:
        """The library file that replaces what lies at *place*, asked again off a fresh reading. Blocking."""
        catalogue = self._demand.platform_catalogue(system)
        library = [library_file(fw) for fw in firmware_list]
        found = _placement_at(catalogue, place)
        if found is None:
            raise _not_replaceable(place)
        placement, satisfied = found
        look = self._files.look(place)
        owned = self._store.do_owned(self._store.do_read_records())
        refusal = place_refusal(place, look, catalogue.roots, unreadable=placement.place_unreadable)
        if (
            not look.lexists
            or look.is_dir
            or refusal is not None
            or satisfied is not False
            or is_own_link(place, look, owned.store_targets, owned.records)
        ):
            raise _not_replaceable(place)
        file = _proven_copy(placement, place, library)
        if file is None:
            raise _not_replaceable(place)
        return next(fw for fw in firmware_list if fw.get("id") == file.firmware_id), catalogue.source or ""

    def _replace_io(self, platform_slug: str, place: str, firmware: dict[str, Any], source: str) -> str:
        """Fetch and check the copy, move the file in the way aside, and link in its place. Blocking."""
        self._store.do_fetch(platform_slug, firmware)
        records = self._store.do_read_records()
        copy = next(
            (c for c in self._store.do_copies(records, platform_slug) if c.file_name == firmware.get("file_name")),
            None,
        )
        if copy is None or not copy.present:
            raise _not_replaceable(place)
        backup = self._store.do_back_up(place)
        self._store.do_place_link(place, copy.path, platform_slug, source)
        return backup


def _placement_at(catalogue: FirmwareCatalogue, place: str) -> tuple[FirmwarePlacement, bool | None] | None:
    """The placement whose place, or a file in whose folder, is *place*, with the resolver's verdict over it."""
    for placement in catalogue.placements:
        if placement.place is None:
            continue
        if not placement.declares_directory and placement.place == place:
            return placement, placement.satisfied
        if placement.declares_directory and os.path.dirname(place) == placement.place:
            statement = next((file for file in placement.folder_files if file.path == place), None)
            return placement, statement.satisfied if statement is not None else None
    return None


def _proven_copy(placement: FirmwarePlacement, place: str, library: list[LibraryFirmware]) -> LibraryFirmware | None:
    """The library file proven right for *place* by the checksum the resolver pins, or ``None``."""
    if placement.declares_directory:
        name = os.path.basename(place)
        return next((f for f in library if f.file_name == name and fills(placement, f) == MATCHED_CONTENT), None)
    match = library_match(placement, library)
    if match is None or match["matched"] != MATCHED_CONTENT:
        return None
    return next((f for f in library if f.firmware_id == match["firmware_id"]), None)


def _not_replaceable(place: str) -> Refused:
    return Refused(NOT_REPLACEABLE, f"The file at {place} can no longer be replaced from this page.", place=place)
