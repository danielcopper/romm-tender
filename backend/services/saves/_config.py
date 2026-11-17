"""Construction-time wiring bundle for ``SaveService``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    import logging

    from services.protocols import (
        ConflictRules,
        SaveFileStore,
        SaveLocationReader,
        SettingsPersister,
        UnitOfWorkFactory,
    )
    from services.saves.copies import SaveCopyService
    from services.saves.local_saves import LocalSaveFiles
    from services.saves.rom_info import RomInfoService
    from services.saves.slots import SetupWizard, SlotDeleter, SlotListing, SlotSwitcher
    from services.saves.status import StatusService
    from services.saves.sync_engine import SyncEngine
    from services.saves.versions import VersionsService


@dataclass(frozen=True)
class SaveServiceConfig:
    """Frozen wiring bundle handed to ``SaveService.__init__``.

    The parts the façade calls, each built once by the composition root and
    shared with the parts that hold it too, beside what the façade reads and
    writes itself.

    Parameters
    ----------
    settings:
        Live reference to Tender's main settings dict, home of the save-sync
        feature toggles the façade reads and writes.
    settings_persister:
        Protocol-typed zero-arg flush for ``settings.json``, called after the
        façade changes a save-sync toggle in the live ``settings`` dict.
    save_file_store:
        Protocol-typed filesystem adapter the bulk local-save deletion removes
        files through.
    uow_factory:
        ``UnitOfWorkFactory`` Protocol seam — the deletion's state hygiene,
        the platform's installed ROMs and the one-time ``kv_config`` marker of
        the save-directory pass are each read or written in a narrow Unit of
        Work of their own (ADR-0006).
    loop:
        The backend's ``asyncio`` event loop (for ``run_in_executor``).
    logger:
        Standard-library logger, injected rather than fetched.
    conflict_rules:
        ``ConflictRules`` seam — the rules a save use case checks at its
        entry under its endpoint's name, and the operation it holds until
        detached work ends.
    save_locations:
        ``SaveLocationReader`` seam, asked whether an emulator installation is
        detected before the one-time save-directory pass runs.
    rom_info, sync_engine, status, local_saves, versions, copies,
    slot_listing, slot_switcher, setup_wizard, slot_deleter:
        The sub-services the use cases delegate to.
    """

    settings: dict[str, Any]
    settings_persister: SettingsPersister
    save_file_store: SaveFileStore
    uow_factory: UnitOfWorkFactory
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    conflict_rules: ConflictRules
    save_locations: SaveLocationReader
    rom_info: RomInfoService
    sync_engine: SyncEngine
    status: StatusService
    local_saves: LocalSaveFiles
    versions: VersionsService
    copies: SaveCopyService
    slot_listing: SlotListing
    slot_switcher: SlotSwitcher
    setup_wizard: SetupWizard
    slot_deleter: SlotDeleter
