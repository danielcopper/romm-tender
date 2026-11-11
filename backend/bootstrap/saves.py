"""The save service's wiring — every part, built once and handed in.

``SaveService`` builds none of its parts: this module builds each sub-service
of the save package, in the order their holders need them, and hands the
façade the ones it calls. ``wire_services`` calls :func:`wire_saves` and keeps
the service, the save backup and the removed-game cleanup's support; a test
calls it with fakes and reaches the parts it built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.identity import VERSION
from services.saves import SaveService, SaveServiceConfig
from services.saves.copies import SaveCopyService, SaveCopyServiceConfig
from services.saves.local_saves import LocalSaveFiles, LocalSaveFilesConfig
from services.saves.prune_support import PruneSaveSupport, PruneSaveSupportConfig
from services.saves.rom_info import RomInfoService, RomInfoServiceConfig
from services.saves.save_backup import SaveBackup, SaveBackupConfig
from services.saves.save_transfer import SaveTransfer, SaveTransferConfig
from services.saves.slots import SetupWizard, SlotDeleter, SlotListing, SlotSwitcher
from services.saves.status import StatusService, StatusServiceConfig
from services.saves.sync_engine import SyncEngine, SyncEngineConfig
from services.saves.sync_engine.devices import DeviceRegistry
from services.saves.sync_engine.matrix import MatrixExecutor
from services.saves.versions import VersionsService, VersionsServiceConfig

if TYPE_CHECKING:
    import asyncio
    import logging

    from services.protocols import (
        ActiveCoreReader,
        Clock,
        ComputeSyncActionFn,
        ConflictRules,
        DebugLogger,
        EventEmitter,
        HostnameReader,
        MachineIdReader,
        MigrationPendingFn,
        PlatformSystems,
        ResolveUploadConflictFn,
        RetroDeckFolders,
        RetryStrategy,
        RommSyncApi,
        RunningRomIdsFn,
        SaveFileStore,
        SaveLocationReader,
        SettingsPersister,
        UnitOfWorkFactory,
        UpdateInProgressFn,
    )


@dataclass(frozen=True)
class SaveWiring:
    """What the save package's parts are built from: adapters, runtime seams and peer services.

    Everything the parts take from outside the package, and nothing they take
    from one another — :func:`wire_saves` hands each field on to the parts that
    hold it.
    """

    romm_api: RommSyncApi
    retry: RetryStrategy
    resolve_upload_conflict: ResolveUploadConflictFn
    compute_sync_action: ComputeSyncActionFn
    settings: dict[str, Any]
    settings_persister: SettingsPersister
    save_file_store: SaveFileStore
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    clock: Clock
    retrodeck_folders: RetroDeckFolders
    active_core: ActiveCoreReader
    save_locations: SaveLocationReader
    platform_systems: PlatformSystems
    hostname_provider: HostnameReader
    machine_id_provider: MachineIdReader
    log_debug: DebugLogger
    emit: EventEmitter
    is_update_in_progress: UpdateInProgressFn
    is_retrodeck_migration_pending: MigrationPendingFn
    conflict_rules: ConflictRules
    running_rom_ids: RunningRomIdsFn
    uow_factory: UnitOfWorkFactory


@dataclass(frozen=True)
class SaveParts:
    """The save service and every part :func:`wire_saves` built for it, each built once.

    The device registry is not among them: the sync engine is the one holder
    of it, so nothing can read the stored id around ``live_device_id``.
    """

    service: SaveService
    rom_info: RomInfoService
    save_backup: SaveBackup
    save_transfer: SaveTransfer
    matrix: MatrixExecutor
    sync_engine: SyncEngine
    status: StatusService
    local_saves: LocalSaveFiles
    versions: VersionsService
    copies: SaveCopyService
    slot_listing: SlotListing
    slot_switcher: SlotSwitcher
    setup_wizard: SetupWizard
    slot_deleter: SlotDeleter
    prune_support: PruneSaveSupport


def wire_saves(wiring: SaveWiring, *, matrix_executor: type[MatrixExecutor] = MatrixExecutor) -> SaveParts:
    """Build every part of the save package, and the façade that holds the ones it calls.

    *matrix_executor* is the class the one matrix executor is built from; a
    test hands a stand-in for it here, where the engine and its peers are
    built, rather than rebinding a method on a built part.
    """
    rom_info = RomInfoService(
        config=RomInfoServiceConfig(
            uow_factory=wiring.uow_factory,
            save_file_store=wiring.save_file_store,
            retrodeck_folders=wiring.retrodeck_folders,
            active_core=wiring.active_core,
            save_locations=wiring.save_locations,
            platform_systems=wiring.platform_systems,
            logger=wiring.logger,
        ),
    )

    # Built once and handed to every part that backs a save file up or moves
    # one, so each holds the step it uses rather than the whole engine.
    save_backup = SaveBackup(
        config=SaveBackupConfig(save_file_store=wiring.save_file_store, clock=wiring.clock, logger=wiring.logger),
    )
    save_transfer = SaveTransfer(
        config=SaveTransferConfig(
            romm_api=wiring.romm_api,
            retry=wiring.retry,
            save_file_store=wiring.save_file_store,
            clock=wiring.clock,
            backup=save_backup,
            logger=wiring.logger,
            log_debug=wiring.log_debug,
        ),
    )

    matrix = matrix_executor(
        rom_info=rom_info,
        romm_api=wiring.romm_api,
        retry=wiring.retry,
        resolve_upload_conflict=wiring.resolve_upload_conflict,
        compute_sync_action=wiring.compute_sync_action,
        logger=wiring.logger,
        clock=wiring.clock,
        save_file_store=wiring.save_file_store,
        log_debug=wiring.log_debug,
        transfer=save_transfer,
    )

    # The single owner of the server device id (kv_config["device_id"]),
    # handed to the sync engine alone: every other save part takes its id from
    # ``SyncEngine.live_device_id``. The device label stays in settings.json
    # (ADR-0003).
    sync_engine = SyncEngine(
        config=SyncEngineConfig(
            settings=wiring.settings,
            uow_factory=wiring.uow_factory,
            rom_info=rom_info,
            device_registry=DeviceRegistry(
                uow_factory=wiring.uow_factory,
                settings=wiring.settings,
                romm_api=wiring.romm_api,
                retry=wiring.retry,
                logger=wiring.logger,
                log_debug=wiring.log_debug,
                settings_persister=wiring.settings_persister,
                version=VERSION,
                loop=wiring.loop,
                hostname_provider=wiring.hostname_provider,
                machine_id_provider=wiring.machine_id_provider,
            ),
            matrix=matrix,
            romm_api=wiring.romm_api,
            retry=wiring.retry,
            loop=wiring.loop,
            logger=wiring.logger,
            save_file_store=wiring.save_file_store,
            log_debug=wiring.log_debug,
            active_core=wiring.active_core,
            is_update_in_progress=wiring.is_update_in_progress,
            is_retrodeck_migration_pending=wiring.is_retrodeck_migration_pending,
            running_rom_ids=wiring.running_rom_ids,
            save_backup=save_backup,
            save_transfer=save_transfer,
            emit=wiring.emit,
        ),
    )

    status = StatusService(
        config=StatusServiceConfig(
            settings=wiring.settings,
            uow_factory=wiring.uow_factory,
            sync_engine=sync_engine,
            matrix=matrix,
            rom_info=rom_info,
            romm_api=wiring.romm_api,
            retry=wiring.retry,
            loop=wiring.loop,
            logger=wiring.logger,
            log_debug=wiring.log_debug,
            active_core=wiring.active_core,
            emit=wiring.emit,
            save_file_store=wiring.save_file_store,
            save_transfer=save_transfer,
        ),
    )

    local_saves = LocalSaveFiles(
        config=LocalSaveFilesConfig(
            rom_info=rom_info,
            sync_engine=sync_engine,
            save_file_store=wiring.save_file_store,
            loop=wiring.loop,
        ),
    )

    versions = VersionsService(
        config=VersionsServiceConfig(
            settings=wiring.settings,
            uow_factory=wiring.uow_factory,
            sync_engine=sync_engine,
            matrix=matrix,
            rom_info=rom_info,
            resolve_core=sync_engine.resolve_core,
            romm_api=wiring.romm_api,
            retry=wiring.retry,
            loop=wiring.loop,
            logger=wiring.logger,
            log_debug=wiring.log_debug,
            save_transfer=save_transfer,
        ),
    )

    copies = SaveCopyService(
        config=SaveCopyServiceConfig(
            settings=wiring.settings,
            uow_factory=wiring.uow_factory,
            sync_engine=sync_engine,
            matrix=matrix,
            rom_info=rom_info,
            resolve_core=sync_engine.resolve_core,
            romm_api=wiring.romm_api,
            retry=wiring.retry,
            loop=wiring.loop,
            logger=wiring.logger,
            log_debug=wiring.log_debug,
            save_transfer=save_transfer,
        ),
    )

    slot_listing = SlotListing(
        settings=wiring.settings,
        uow_factory=wiring.uow_factory,
        romm_api=wiring.romm_api,
        retry=wiring.retry,
        loop=wiring.loop,
        log_debug=wiring.log_debug,
        sync_engine=sync_engine,
    )
    slot_switcher = SlotSwitcher(
        settings=wiring.settings,
        uow_factory=wiring.uow_factory,
        sync_engine=sync_engine,
        status_service=status,
        rom_info=rom_info,
        romm_api=wiring.romm_api,
        retry=wiring.retry,
        loop=wiring.loop,
        clock=wiring.clock,
        save_file_store=wiring.save_file_store,
        log_debug=wiring.log_debug,
        save_backup=save_backup,
        save_transfer=save_transfer,
    )
    setup_wizard = SetupWizard(
        settings=wiring.settings,
        uow_factory=wiring.uow_factory,
        rom_info=rom_info,
        resolve_core=sync_engine.resolve_core,
        romm_api=wiring.romm_api,
        retry=wiring.retry,
        loop=wiring.loop,
        logger=wiring.logger,
        save_file_store=wiring.save_file_store,
        log_debug=wiring.log_debug,
        sync_engine=sync_engine,
        save_backup=save_backup,
        save_transfer=save_transfer,
    )
    slot_deleter = SlotDeleter(
        settings=wiring.settings,
        uow_factory=wiring.uow_factory,
        rom_info=rom_info,
        romm_api=wiring.romm_api,
        retry=wiring.retry,
        loop=wiring.loop,
        log_debug=wiring.log_debug,
        sync_engine=sync_engine,
    )

    prune_support = PruneSaveSupport(
        config=PruneSaveSupportConfig(
            uow_factory=wiring.uow_factory,
            save_file_store=wiring.save_file_store,
            retrodeck_folders=wiring.retrodeck_folders,
            rom_info=rom_info,
            sync_engine=sync_engine,
            save_backup=save_backup,
        ),
    )

    service = SaveService(
        config=SaveServiceConfig(
            settings=wiring.settings,
            settings_persister=wiring.settings_persister,
            save_file_store=wiring.save_file_store,
            uow_factory=wiring.uow_factory,
            loop=wiring.loop,
            logger=wiring.logger,
            conflict_rules=wiring.conflict_rules,
            save_locations=wiring.save_locations,
            rom_info=rom_info,
            sync_engine=sync_engine,
            status=status,
            local_saves=local_saves,
            versions=versions,
            copies=copies,
            slot_listing=slot_listing,
            slot_switcher=slot_switcher,
            setup_wizard=setup_wizard,
            slot_deleter=slot_deleter,
        ),
    )
    return SaveParts(
        service=service,
        rom_info=rom_info,
        save_backup=save_backup,
        save_transfer=save_transfer,
        matrix=matrix,
        sync_engine=sync_engine,
        status=status,
        local_saves=local_saves,
        versions=versions,
        copies=copies,
        slot_listing=slot_listing,
        slot_switcher=slot_switcher,
        setup_wizard=setup_wizard,
        slot_deleter=slot_deleter,
        prune_support=prune_support,
    )
