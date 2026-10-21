"""Service half of the composition root — bundles in, live services out.

Service construction is separated from adapter construction because it
needs runtime state only the entry point can supply (the event loop, the
event sink's emit) plus state that exists once ``bootstrap()`` has run.
Services never reach each other by import: every cross-service reference is
threaded through a ``*ServiceConfig`` here, or deferred through a
``LateBinding`` when the two constructors form a cycle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.identity import VERSION
from domain.shortcut_data import RETRODECK_APP_ID
from lib.background_tasks import BackgroundTasks
from lib.conflict_rules import ConflictRuleSet
from lib.late_binding import LateBinding
from lib.prune_conflicts import PruneConflicts
from services.achievements import AchievementsService, AchievementsServiceConfig
from services.active_core_resolver import ActiveCoreResolver, ActiveCoreResolverConfig
from services.artwork import ArtworkService, ArtworkServiceConfig
from services.connection import ConnectionService, ConnectionServiceConfig
from services.cores import CoreService, CoreServiceConfig
from services.data_inventory import DataInventoryService, DataInventoryServiceConfig
from services.disc import DiscService, DiscServiceConfig
from services.disc_launch_resolver import DiscLaunchResolver, DiscLaunchResolverConfig
from services.downloads import DownloadService, DownloadServiceConfig
from services.emulator_sources import EmulatorSourcesService, EmulatorSourcesServiceConfig
from services.firmware import FirmwareService, FirmwareServiceConfig
from services.firmware.store import FirmwareStore, FirmwareStoreConfig
from services.game_detail import GameDetailService, GameDetailServiceConfig
from services.game_process import GameProcessService, GameProcessServiceConfig
from services.leftover_tmp_cleanup import LeftoverTmpCleanupService, LeftoverTmpCleanupServiceConfig
from services.library import LibraryService, LibraryServiceConfig
from services.metadata import MetadataService, MetadataServiceConfig
from services.migration import MigrationService, MigrationServiceConfig
from services.platform_systems import PlatformSystemService, PlatformSystemServiceConfig
from services.playtime import PlaytimeService, PlaytimeServiceConfig
from services.prune import PruneService, PruneServiceConfig
from services.prune_leases import PruneLeaseService, PruneLeaseServiceConfig
from services.relaunch_options_resolver import RelaunchOptionsResolver, RelaunchOptionsResolverConfig
from services.rom_adoption import RomAdoptionService, RomAdoptionServiceConfig
from services.rom_install_recorder import RomInstallRecorder, RomInstallRecorderConfig
from services.rom_removal import RomRemovalService, RomRemovalServiceConfig
from services.saves import SaveService, SaveServiceConfig
from services.session_lifecycle import SessionLifecycleService, SessionLifecycleServiceConfig
from services.settings import SettingsService, SettingsServiceConfig
from services.shortcut_artwork import ShortcutArtworkService, ShortcutArtworkServiceConfig
from services.shortcut_file import ShortcutFileService, ShortcutFileServiceConfig
from services.shortcut_relocation import ShortcutRelocationService, ShortcutRelocationServiceConfig
from services.shortcut_removal import ShortcutRemovalService, ShortcutRemovalServiceConfig
from services.startup_healing import StartupHealingService, StartupHealingServiceConfig
from services.steamgrid import SteamGridService, SteamGridServiceConfig
from services.tender_folder import TenderFolderService, TenderFolderServiceConfig
from services.update_check import UpdateCheckService, UpdateCheckServiceConfig
from services.update_install import UpdateInstallService, UpdateInstallServiceConfig
from services.update_outcome import UpdateOutcomeService, UpdateOutcomeServiceConfig
from services.update_output import UpdateOutputService, UpdateOutputServiceConfig
from services.version_switch import VersionSwitchService, VersionSwitchServiceConfig

if TYPE_CHECKING:
    from typing import Any

    from domain.app_directories import AppDirectories
    from domain.update_release import UpdateSource
    from lib.errors import Refused
    from models.shortcut_launcher import ShortcutLauncher
    from services.protocols import (
        InstalledRomRemoverFn,
        RendererAboveCeilingFn,
        SaveDirectoriesRecorderFn,
        SiblingSupersedeFn,
    )

    from .adapters import AdapterBundle, CallbackBundle, RuntimeBundle, StateBundle


@dataclass(frozen=True)
class WiringConfig:
    """Composition-root inputs for ``wire_services``.

    Four bundles carry the wiring; ``min_required_version`` sits at the top
    level — it is the oldest RomM release this program accepts
    (``MIN_ROMM_VERSION``), a constant rather than a runtime seam.
    ``directories`` sits beside it for the same reason: it is where this
    program's directories are, resolved from the environment by the entry
    point rather than derived. It is the same value ``bootstrap()`` was
    handed, carried on so that a service's wiring reads a directory rather
    than composing one — the entry point is the only caller of
    ``resolve_directories``, and nothing downstream of it builds a root out
    of a home. ``launcher`` sits beside it for the same reason: it says
    where the launcher a Steam shortcut runs through lives, and whether this
    start got it there. Its path is the data directory's only where this
    start actually put the launcher under it — otherwise it is the copy the
    release ships. ``update_source`` is the entry point's other environment
    answer — where releases are asked for, and whether this process is the
    installed program — handed to ``bootstrap()`` too. ``installer_environment``
    is the third: the variables an installer started from the panel runs with,
    resolved by the entry point from this process's environment and
    directories.
    """

    adapters: AdapterBundle
    stores: StateBundle
    runtime: RuntimeBundle
    callbacks: CallbackBundle
    min_required_version: tuple[int, ...]
    directories: AppDirectories
    launcher: ShortcutLauncher
    update_source: UpdateSource
    installer_environment: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ServicesBundle:
    """Every wired service, and the prune conflicts they share, as ``wire_services`` hands them out."""

    prune_conflicts: PruneConflicts
    save_sync_service: SaveService
    playtime_service: PlaytimeService
    sync_service: LibraryService
    download_service: DownloadService
    rom_adoption_service: RomAdoptionService
    rom_removal_service: RomRemovalService
    prune_service: PruneService
    prune_lease_service: PruneLeaseService
    data_inventory_service: DataInventoryService
    firmware_service: FirmwareService
    sgdb_service: SteamGridService
    shortcut_artwork_service: ShortcutArtworkService
    metadata_service: MetadataService
    achievements_service: AchievementsService
    migration_service: MigrationService
    game_detail_service: GameDetailService
    artwork_service: ArtworkService
    shortcut_removal_service: ShortcutRemovalService
    settings_service: SettingsService
    core_service: CoreService
    emulator_sources_service: EmulatorSourcesService
    tender_folder_service: TenderFolderService
    disc_service: DiscService
    version_switch_service: VersionSwitchService
    connection_service: ConnectionService
    startup_healing_service: StartupHealingService
    shortcut_relocation_service: ShortcutRelocationService
    shortcut_file_service: ShortcutFileService
    update_check_service: UpdateCheckService
    update_outcome_service: UpdateOutcomeService
    update_install_service: UpdateInstallService
    update_output_service: UpdateOutputService
    session_lifecycle_service: SessionLifecycleService
    game_process_service: GameProcessService
    relaunch_options_resolver: RelaunchOptionsResolver
    leftover_tmp_cleanup_service: LeftoverTmpCleanupService


def wire_services(cfg: WiringConfig) -> ServicesBundle:
    """Create service instances once ``bootstrap()`` has loaded the state they share.

    Called from ``build_application`` after ``bootstrap()`` has loaded
    and migrated the settings, so that services receive a live reference
    to the migrated settings dict.

    Returns
    -------
    The :class:`ServicesBundle` of every wired service.
    """

    # Retry-progress surface (#1345): the RommHttpAdapter runs its retry+backoff
    # ladder inside ``run_in_executor`` worker threads, so each retry is off the
    # event loop. Wire a listener that marshals a ``server_retry_progress`` emit
    # back onto the loop (the same call_soon_threadsafe → spawned-task pattern the
    # download-progress emits use), letting the saves surfaces show a live
    # "connecting… (attempt N/M)" indicator instead of a frozen spinner. Wired
    # here (not in ``bootstrap``) because the loop and emit only exist at
    # service-wiring time; the adapter stays service-free (plain injected callable).
    retry_frames = BackgroundTasks(logger=cfg.runtime.logger)

    def _emit_server_retry(attempt: int, max_attempts: int, delay_s: float) -> None:
        payload = {"attempt": attempt, "max_attempts": max_attempts, "delay_s": delay_s}
        cfg.runtime.loop.call_soon_threadsafe(
            lambda: retry_frames.spawn(cfg.runtime.emit("server_retry_progress", payload), "server_retry_progress"),
        )

    cfg.adapters.http_adapter.on_retry = _emit_server_retry

    # The one record of every claim that conflicts with a removed-game cleanup,
    # whoever holds it. Built before every service so any of them can be handed it.
    prune_conflicts = PruneConflicts(
        logger=cfg.runtime.logger,
        log_debug=cfg.callbacks.log_debug,
        release_tasks=BackgroundTasks(logger=cfg.runtime.logger),
    )
    # The conflict rules a use case checks at its entry, built beside the
    # prune conflicts so any service can be handed them. The update install,
    # folder, migration and sync services that answer the other four rules are
    # built later.
    update_in_progress_binding: LateBinding[bool] = LateBinding("update_in_progress")
    folder_unreachable_binding: LateBinding[Refused | None] = LateBinding("folder_unreachable")
    migration_pending_binding: LateBinding[bool] = LateBinding("migration_pending")
    sync_in_flight_binding: LateBinding[bool] = LateBinding("sync_in_flight")
    renderer_above_ceiling_binding: LateBinding[RendererAboveCeilingFn] = LateBinding("renderer_above_ceiling")
    conflict_rules = ConflictRuleSet(
        prune_conflicts=prune_conflicts,
        update_in_progress=update_in_progress_binding.get,
        folder_unreachable=folder_unreachable_binding.get,
        migration_pending=migration_pending_binding.get,
        sync_in_flight=sync_in_flight_binding.get,
    )

    # The firmware store records files under Tender's folder, which asks it
    # what is recorded there, while every write into the store goes through
    # the folder made ready for it — so the store is built first and reaches
    # the folder service through this binding.
    prepare_folder_binding: LateBinding[str] = LateBinding("prepare_folder")
    firmware_store = FirmwareStore(
        config=FirmwareStoreConfig(
            romm_api=cfg.adapters.romm_api,
            firmware_file_store=cfg.adapters.firmware_file_store,
            platform_firmware_resolver=cfg.adapters.platform_firmware_resolver,
            prepare_folder=prepare_folder_binding.get,
            uow_factory=cfg.callbacks.uow_factory,
            clock=cfg.runtime.clock,
            data_dir=cfg.directories.data_dir,
            logger=cfg.runtime.logger,
        )
    )

    # Every store that records something under Tender's folder joins this tuple.
    tender_folder_service = TenderFolderService(
        config=TenderFolderServiceConfig(
            store=cfg.adapters.tender_folder,
            sources=cfg.adapters.emulator_sources,
            records=(firmware_store,),
            settings=cfg.stores.settings,
            settings_persister=cfg.callbacks.settings_persister,
            uow_factory=cfg.callbacks.uow_factory,
            uuid_gen=cfg.runtime.uuid_gen,
            conflict_rules=conflict_rules,
            loop=cfg.runtime.loop,
            log_debug=cfg.callbacks.log_debug,
        ),
    )
    folder_unreachable_binding.set(tender_folder_service.unreachable_refusal)
    prepare_folder_binding.set(tender_folder_service.do_prepare_for_write)

    # Forward-reference bindings for producers constructed later in this
    # function. Consumers receive ``binding.get`` (a bound method); the
    # binding is populated via ``.set(...)`` once the producer exists.
    # Accessing ``.get()`` before ``.set()`` raises RuntimeError instead of
    # the NameError a bare forward-ref lambda would produce.
    pending_sync_binding: LateBinding[dict[int, dict[str, Any]]] = LateBinding("pending_sync")
    # DownloadService needs RomRemovalService.remove_rom_unchecked for the sibling
    # supersede, but RomRemovalService needs DownloadService's queue-cleanup seam —
    # a construction cycle. Bind the remover after both services exist.
    rom_remover_binding: LateBinding[InstalledRomRemoverFn] = LateBinding("rom_remover")
    # RomAdoptionService needs DownloadService's sibling supersede (#1298), but
    # DownloadService needs the adoption service's occupancy gate — the same
    # shape of cycle, bound after both exist.
    sibling_supersede_binding: LateBinding[SiblingSupersedeFn] = LateBinding("sibling_supersede")
    # MigrationService re-records the save directories once a home migration has
    # moved the files, but SaveService is built after it (it takes the migration
    # gate) — bound once both exist.
    save_directories_binding: LateBinding[SaveDirectoriesRecorderFn] = LateBinding("save_directories")
    # The shortcut artwork job yields to a shortcut removal, and the removal stops
    # the job — the same shape of cycle, bound once both exist.
    removal_in_flight_binding: LateBinding[bool] = LateBinding("removal_in_flight")

    # The single read-path core resolver (B1): folds the per-game
    # emulator_override pin over the system-layer ES-DE resolution. Built first
    # (no service deps) so every per-game-core read consumer — migration, saves,
    # game-detail, cores — draws from the SAME seam and the read-path core never
    # diverges from the launched core.
    # The system a RomM platform is in an emulator source, which every reader of
    # a platform's system asks — the download, the emulator choice, the BIOS
    # answers and the saves — so they all ask the same question.
    platform_systems = PlatformSystemService(
        config=PlatformSystemServiceConfig(
            uow_factory=cfg.callbacks.uow_factory,
            romm_api=cfg.adapters.romm_api,
            source_platform_systems=cfg.adapters.source_platform_systems,
            logger=cfg.runtime.logger,
            log_debug=cfg.callbacks.log_debug,
        ),
    )

    active_core_resolver = ActiveCoreResolver(
        config=ActiveCoreResolverConfig(
            uow_factory=cfg.callbacks.uow_factory,
            core_info=cfg.adapters.core_info_provider,
            sandbox_launcher=cfg.callbacks.sandbox_launcher,
            platform_core_reader=cfg.callbacks.platform_core_reader,
            platform_systems=platform_systems,
            logger=cfg.runtime.logger,
        ),
    )

    # The single read-path disc resolver (#865): folds the per-game
    # selected_disc pick over the live disc-image enumeration of an installed
    # ROM's directory. Built alongside active_core_resolver (no service deps) so
    # every launch-bake site and the picker use cases draw the bake path from the
    # SAME seam and the baked launch_options never diverge from the selection.
    disc_launch_resolver = DiscLaunchResolver(
        config=DiscLaunchResolverConfig(
            list_files=cfg.callbacks.list_rom_dir_files,
            system_extensions=cfg.callbacks.system_extensions,
            logger=cfg.runtime.logger,
        ),
    )

    # The single installed+bound relaunch-items resolver (#1154): snapshots the
    # rows in one short read UoW it closes before resolving core + disc, so the
    # nested resolver UoW never deadlocks. Both the RetroDECK-home migration and
    # the startup launch-options reconcile draw their relaunch items from this
    # SAME seam, so the two never carry a divergent build of the list.
    relaunch_options_resolver = RelaunchOptionsResolver(
        config=RelaunchOptionsResolverConfig(
            uow_factory=cfg.callbacks.uow_factory,
            active_core=active_core_resolver,
            disc_resolver=disc_launch_resolver,
            loop=cfg.runtime.loop,
            conflict_rules=conflict_rules,
        ),
    )

    # MigrationService is constructed before SaveService so that
    # save_sync_service can receive a bound reference to
    # ``migration_service.is_retrodeck_migration_pending``.
    migration_service = MigrationService(
        config=MigrationServiceConfig(
            migration_file_store=cfg.adapters.migration_file_store,
            settings=cfg.stores.settings,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            settings_persister=cfg.callbacks.settings_persister,
            emit=cfg.runtime.emit,
            firmware_resolver=cfg.adapters.firmware_resolver,
            retrodeck_folders=cfg.callbacks.retrodeck_folders,
            relaunch_options=relaunch_options_resolver,
            save_directories=save_directories_binding.get,
            uow_factory=cfg.callbacks.uow_factory,
            conflict_rules=conflict_rules,
        ),
    )
    migration_pending_binding.set(migration_service.is_retrodeck_migration_pending)

    save_service_config = SaveServiceConfig(
        romm_api=cfg.adapters.romm_api,
        retry=cfg.adapters.http_adapter,
        resolve_upload_conflict=cfg.adapters.resolve_upload_conflict,
        compute_sync_action=cfg.adapters.compute_sync_action,
        settings=cfg.stores.settings,
        settings_persister=cfg.callbacks.settings_persister,
        save_file_store=cfg.adapters.save_file_store,
        loop=cfg.runtime.loop,
        logger=cfg.runtime.logger,
        clock=cfg.runtime.clock,
        retrodeck_folders=cfg.callbacks.retrodeck_folders,
        active_core=active_core_resolver,
        save_locations=cfg.adapters.save_locations,
        platform_systems=platform_systems,
        hostname_provider=cfg.runtime.hostname_provider,
        machine_id_provider=cfg.runtime.machine_id_provider,
        log_debug=cfg.callbacks.log_debug,
        emit=cfg.runtime.emit,
        is_update_in_progress=update_in_progress_binding.get,
        is_retrodeck_migration_pending=migration_service.is_retrodeck_migration_pending,
        conflict_rules=conflict_rules,
        uow_factory=cfg.callbacks.uow_factory,
    )
    save_sync_service = SaveService(config=save_service_config)
    save_directories_binding.set(lambda: save_sync_service.rerecord_save_directories)

    playtime_service = PlaytimeService(
        config=PlaytimeServiceConfig(
            romm_api=cfg.adapters.romm_api,
            retry=cfg.adapters.http_adapter,
            device_id_provider=save_sync_service,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            clock=cfg.runtime.clock,
            log_debug=cfg.callbacks.log_debug,
            uow_factory=cfg.callbacks.uow_factory,
            conflict_rules=conflict_rules,
        ),
    )

    metadata_service = MetadataService(
        config=MetadataServiceConfig(
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            log_debug=cfg.callbacks.log_debug,
            uow_factory=cfg.callbacks.uow_factory,
        ),
    )

    artwork_service = ArtworkService(
        config=ArtworkServiceConfig(
            romm_api=cfg.adapters.romm_api,
            steam_config=cfg.adapters.steam_config,
            cover_art_file_store=cfg.adapters.cover_art_file_store,
            cover_cache_dir=os.path.join(cfg.directories.cache_dir, "covers"),
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            get_pending_sync=pending_sync_binding.get,
            uow_factory=cfg.callbacks.uow_factory,
            conflict_rules=conflict_rules,
        ),
    )

    sgdb_service = SteamGridService(
        config=SteamGridServiceConfig(
            sgdb_api=cfg.adapters.sgdb_adapter,
            romm_api=cfg.adapters.romm_api,
            steam_config=cfg.adapters.steam_config,
            sgdb_artwork_cache=cfg.adapters.sgdb_artwork_cache,
            settings=cfg.stores.settings,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            settings_persister=cfg.callbacks.settings_persister,
            get_pending_sync=pending_sync_binding.get,
            log_debug=cfg.callbacks.log_debug,
            uow_factory=cfg.callbacks.uow_factory,
            conflict_rules=conflict_rules,
            downscale_icon=cfg.adapters.downscale_icon,
            clock=cfg.runtime.clock,
        ),
    )

    shortcut_artwork_service = ShortcutArtworkService(
        config=ShortcutArtworkServiceConfig(
            steam_config=cfg.adapters.steam_config,
            source=sgdb_service,
            uow_factory=cfg.callbacks.uow_factory,
            settings=cfg.stores.settings,
            conflict_rules=conflict_rules,
            emit=cfg.runtime.emit,
            sleeper=cfg.runtime.sleeper,
            clock=cfg.runtime.clock,
            sync_in_flight=sync_in_flight_binding.get,
            cleanup_running=lambda: prune_conflicts.cleanup_running,
            removal_in_flight=removal_in_flight_binding.get,
            panel_connected=cfg.runtime.panel_connected,
            renderer_above_ceiling=renderer_above_ceiling_binding.get,
            tender_logo=cfg.adapters.tender_logo,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            log_debug=cfg.callbacks.log_debug,
        ),
    )

    shortcut_removal_service = ShortcutRemovalService(
        config=ShortcutRemovalServiceConfig(
            steam_config=cfg.adapters.steam_config,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            artwork_remover=artwork_service,
            uow_factory=cfg.callbacks.uow_factory,
            conflict_rules=conflict_rules,
            held_claims=prune_conflicts.held_claims,
            artwork_job=shortcut_artwork_service,
        ),
    )
    removal_in_flight_binding.set(shortcut_removal_service.is_removal_in_flight)

    sync_service = LibraryService(
        config=LibraryServiceConfig(
            romm_api=cfg.adapters.romm_api,
            steam_config=cfg.adapters.steam_config,
            settings=cfg.stores.settings,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            launcher_exe=cfg.launcher.path,
            emit=cfg.runtime.emit,
            clock=cfg.runtime.clock,
            uuid_gen=cfg.runtime.uuid_gen,
            sleeper=cfg.runtime.sleeper,
            settings_persister=cfg.callbacks.settings_persister,
            log_debug=cfg.callbacks.log_debug,
            artwork=artwork_service,
            uow_factory=cfg.callbacks.uow_factory,
            active_core=active_core_resolver,
            disc_resolver=disc_launch_resolver,
            emulator_sources=cfg.adapters.emulator_sources,
            renderer_rss=cfg.adapters.renderer_rss,
            renderer_gc=cfg.adapters.renderer_gc,
            conflict_rules=conflict_rules,
            artwork_job=shortcut_artwork_service,
        ),
    )
    pending_sync_binding.set(lambda: sync_service.pending_sync)
    sync_in_flight_binding.set(sync_service.is_sync_in_flight)
    renderer_above_ceiling_binding.set(lambda: sync_service.renderer_above_ceiling)

    rom_install_recorder = RomInstallRecorder(
        config=RomInstallRecorderConfig(
            logger=cfg.runtime.logger,
            clock=cfg.runtime.clock,
            uow_factory=cfg.callbacks.uow_factory,
            system_extensions=cfg.callbacks.system_extensions,
            active_core=active_core_resolver,
            disc_resolver=disc_launch_resolver,
        ),
    )

    rom_adoption_service = RomAdoptionService(
        config=RomAdoptionServiceConfig(
            romm_api=cfg.adapters.romm_api,
            download_file_store=cfg.adapters.download_file_store,
            adoption_move=cfg.adapters.adoption_move,
            quarantine_save=save_sync_service.quarantine_local_file,
            platform_systems=platform_systems,
            retrodeck_folders=cfg.callbacks.retrodeck_folders,
            install_recorder=rom_install_recorder,
            m3u_support=cfg.callbacks.m3u_support,
            system_extensions=cfg.callbacks.system_extensions,
            system_known=cfg.callbacks.system_known,
            save_locations=cfg.adapters.save_locations,
            active_core=active_core_resolver,
            sibling_supersede=sibling_supersede_binding.get,
            uow_factory=cfg.callbacks.uow_factory,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            log_debug=cfg.callbacks.log_debug,
            emit=cfg.runtime.emit,
            clock=cfg.runtime.clock,
            conflict_rules=conflict_rules,
        ),
    )

    download_service = DownloadService(
        config=DownloadServiceConfig(
            romm_api=cfg.adapters.romm_api,
            download_file_store=cfg.adapters.download_file_store,
            platform_systems=platform_systems,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            emit=cfg.runtime.emit,
            clock=cfg.runtime.clock,
            sleeper=cfg.runtime.sleeper,
            retrodeck_folders=cfg.callbacks.retrodeck_folders,
            install_recorder=rom_install_recorder,
            target_gate=rom_adoption_service.check_download_target,
            m3u_support=cfg.callbacks.m3u_support,
            uow_factory=cfg.callbacks.uow_factory,
            rom_remover=rom_remover_binding.get,
            conflict_rules=conflict_rules,
        ),
    )

    leftover_tmp_cleanup_service = LeftoverTmpCleanupService(
        config=LeftoverTmpCleanupServiceConfig(
            logger=cfg.runtime.logger,
            download_file_store=cfg.adapters.download_file_store,
            retrodeck_folders=cfg.callbacks.retrodeck_folders,
            tender_folder=cfg.adapters.tender_folder,
        ),
    )

    rom_removal_service = RomRemovalService(
        config=RomRemovalServiceConfig(
            logger=cfg.runtime.logger,
            loop=cfg.runtime.loop,
            clock=cfg.runtime.clock,
            emit=cfg.runtime.emit,
            rom_file_store=cfg.adapters.rom_file_store,
            retrodeck_folders=cfg.callbacks.retrodeck_folders,
            download_queue_cleanup=download_service,
            uow_factory=cfg.callbacks.uow_factory,
            conflict_rules=conflict_rules,
        ),
    )
    # Close the download↔removal cycle: DownloadService's sibling supersede now
    # resolves the live remover through this binding (#1298).
    rom_remover_binding.set(lambda: rom_removal_service.remove_rom_unchecked)
    sibling_supersede_binding.set(lambda: download_service.supersede_sibling_installs)

    firmware_service = FirmwareService(
        config=FirmwareServiceConfig(
            romm_api=cfg.adapters.romm_api,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            clock=cfg.runtime.clock,
            firmware_file_store=cfg.adapters.firmware_file_store,
            platform_firmware_resolver=cfg.adapters.platform_firmware_resolver,
            firmware_identifier=cfg.adapters.firmware_identifier,
            store=firmware_store,
            retrodeck_folders=cfg.callbacks.retrodeck_folders,
            core_info=cfg.adapters.core_info_provider,
            platform_systems=platform_systems,
            platform_core_reader=cfg.callbacks.platform_core_reader,
            active_core=active_core_resolver,
            uow_factory=cfg.callbacks.uow_factory,
            conflict_rules=conflict_rules,
            settings=cfg.stores.settings,
            settings_persister=cfg.callbacks.settings_persister,
        ),
    )

    achievements_service = AchievementsService(
        config=AchievementsServiceConfig(
            romm_api=cfg.adapters.romm_api,
            uow_factory=cfg.callbacks.uow_factory,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            clock=cfg.runtime.clock,
            log_debug=cfg.callbacks.log_debug,
        ),
    )

    game_detail_service = GameDetailService(
        config=GameDetailServiceConfig(
            candidate_probe=rom_adoption_service.has_adoption_candidate,
            settings=cfg.stores.settings,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            clock=cfg.runtime.clock,
            uow_factory=cfg.callbacks.uow_factory,
            bios_checker=firmware_service,
            achievements=achievements_service,
            active_core=active_core_resolver,
            path_exists=cfg.adapters.path_probe,
            retrodeck_folders=cfg.callbacks.retrodeck_folders,
            platform_systems=platform_systems,
        ),
    )

    settings_service = SettingsService(
        config=SettingsServiceConfig(
            settings=cfg.stores.settings,
            uow_factory=cfg.callbacks.uow_factory,
            logger=cfg.runtime.logger,
            settings_persister=cfg.callbacks.settings_persister,
            steam_config=cfg.adapters.steam_config,
            conflict_rules=conflict_rules,
        ),
    )

    core_service = CoreService(
        config=CoreServiceConfig(
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            core_info=cfg.adapters.core_info_provider,
            platform_systems=platform_systems,
            settings=cfg.stores.settings,
            settings_persister=cfg.callbacks.settings_persister,
            uow_factory=cfg.callbacks.uow_factory,
            active_core=active_core_resolver,
            disc_resolver=disc_launch_resolver,
            conflict_rules=conflict_rules,
        ),
    )
    emulator_sources_service = EmulatorSourcesService(
        config=EmulatorSourcesServiceConfig(
            sources=cfg.adapters.emulator_sources,
            settings=cfg.stores.settings,
            settings_persister=cfg.callbacks.settings_persister,
            loop=cfg.runtime.loop,
            log_debug=cfg.callbacks.log_debug,
        ),
    )

    disc_service = DiscService(
        config=DiscServiceConfig(
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            uow_factory=cfg.callbacks.uow_factory,
            disc_resolver=disc_launch_resolver,
            active_core=active_core_resolver,
            conflict_rules=conflict_rules,
        ),
    )

    connection_service = ConnectionService(
        config=ConnectionServiceConfig(
            settings=cfg.stores.settings,
            romm_api=cfg.adapters.romm_api,
            settings_persister=cfg.callbacks.settings_persister,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            min_required_version=cfg.min_required_version,
            forget_device=save_sync_service.forget_device,
            clear_playtime_scope_notice=playtime_service.clear_scope_notice,
            conflict_rules=conflict_rules,
        ),
    )

    startup_healing_service = StartupHealingService(
        config=StartupHealingServiceConfig(
            logger=cfg.runtime.logger,
            clock=cfg.runtime.clock,
            path_probe=cfg.adapters.path_probe,
            resolve_path=cfg.adapters.resolve_path,
            uow_factory=cfg.callbacks.uow_factory,
            relaunch_options=relaunch_options_resolver,
            loop=cfg.runtime.loop,
            conflict_rules=conflict_rules,
        ),
    )

    shortcut_relocation_service = ShortcutRelocationService(
        config=ShortcutRelocationServiceConfig(
            launcher_exe=cfg.launcher.path,
            launcher_at_home=cfg.launcher.at_home,
            steam_config=cfg.adapters.steam_config,
            uow_factory=cfg.callbacks.uow_factory,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
        ),
    )

    shortcut_file_service = ShortcutFileService(
        config=ShortcutFileServiceConfig(
            steam_config=cfg.adapters.steam_config,
            loop=cfg.runtime.loop,
        ),
    )

    # Built after SaveService + ConnectionService: the version-switch
    # save-stranding gate reads the stranding verdict of
    # SaveService.check_local_drift and reachability from
    # ConnectionService.probe_reachability, and re-bakes a switched-onto install
    # via the relaunch resolver. The active-download guard reads
    # DownloadService's in-progress set (built earlier — no LateBinding).
    version_switch_service = VersionSwitchService(
        config=VersionSwitchServiceConfig(
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            clock=cfg.runtime.clock,
            uow_factory=cfg.callbacks.uow_factory,
            romm_api=cfg.adapters.romm_api,
            settings=cfg.stores.settings,
            drift_probe=save_sync_service.check_local_drift,
            reachability_probe=connection_service.probe_reachability,
            relaunch_resolver=relaunch_options_resolver,
            active_downloads=download_service.active_download_rom_ids,
            conflict_rules=conflict_rules,
        ),
    )

    # Stop Game (the running-overlay chevron action). The kill is backend-side;
    # the service owns the escalation policy and takes the RetroDECK app id from
    # the single domain constant the launch command is built from. The instance the
    # ladder signals is matched against the launch path from the SAME resolver
    # that bakes the shortcut's launch command, so the path it compares a live
    # process against is the path that was launched.
    game_process_service = GameProcessService(
        config=GameProcessServiceConfig(
            game_process=cfg.adapters.game_process,
            launch_path=relaunch_options_resolver,
            sleeper=cfg.runtime.sleeper,
            logger=cfg.runtime.logger,
            log_debug=cfg.callbacks.log_debug,
            flatpak_app_id=RETRODECK_APP_ID,
        ),
    )

    session_lifecycle_service = SessionLifecycleService(
        config=SessionLifecycleServiceConfig(
            playtime_recorder=playtime_service,
            post_exit_sync=save_sync_service,
            achievement_sync=achievements_service,
            migration_reader=migration_service,
            update_in_progress=update_in_progress_binding.get,
            logger=cfg.runtime.logger,
            conflict_rules=conflict_rules,
        ),
    )

    data_inventory_service = DataInventoryService(
        config=DataInventoryServiceConfig(
            loop=cfg.runtime.loop,
            uow_factory=cfg.callbacks.uow_factory,
            recovery_inventory=cfg.adapters.recovery_inventory,
        ),
    )

    prune_service = PruneService(
        config=PruneServiceConfig(
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            clock=cfg.runtime.clock,
            uuid_gen=cfg.runtime.uuid_gen,
            emit=cfg.runtime.emit,
            uow_factory=cfg.callbacks.uow_factory,
            romm_api=cfg.adapters.romm_api,
            recovery_store=cfg.adapters.recovery_store,
            prepare_folder=tender_folder_service.do_prepare_for_write,
            prune_artifacts=cfg.adapters.prune_artifacts,
            steam_recovery=cfg.adapters.steam_recovery,
            retrodeck_folders=cfg.callbacks.retrodeck_folders,
            save_coordinator=save_sync_service.prune_support,
            active_downloads=download_service.active_download_rom_ids,
            drift_probe=save_sync_service.check_local_drift,
            remove_installed_files=rom_removal_service.delete_rom_files,
            settings=cfg.stores.settings,
            run_claim=prune_conflicts,
            conflict_rules=conflict_rules,
            artwork_job=shortcut_artwork_service,
        )
    )

    prune_lease_service = PruneLeaseService(config=PruneLeaseServiceConfig(conflict_rules=conflict_rules))

    update_check_service = UpdateCheckService(
        config=UpdateCheckServiceConfig(
            latest_release=cfg.adapters.latest_release,
            current_version=VERSION,
            installed_program=cfg.update_source.installed_program,
            clock=cfg.runtime.clock,
            uow_factory=cfg.callbacks.uow_factory,
            settings=cfg.stores.settings,
            settings_persister=cfg.callbacks.settings_persister,
            loop=cfg.runtime.loop,
            sleeper=cfg.runtime.sleeper,
            emit=cfg.runtime.emit,
            logger=cfg.runtime.logger,
            log_debug=cfg.callbacks.log_debug,
        ),
    )

    update_outcome_service = UpdateOutcomeService(
        config=UpdateOutcomeServiceConfig(
            current_version=VERSION,
            read_update_failure=cfg.adapters.update_failure,
            uow_factory=cfg.callbacks.uow_factory,
            settings=cfg.stores.settings,
            settings_persister=cfg.callbacks.settings_persister,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
        ),
    )

    # Built last: it reads the work in flight off every service that owns some.
    update_install_service = UpdateInstallService(
        config=UpdateInstallServiceConfig(
            releases=update_check_service,
            current_version=VERSION,
            installed_program=cfg.update_source.installed_program,
            steam=cfg.runtime.steam,
            library_sync_in_flight=sync_service.is_sync_in_flight,
            rom_downloads_in_flight=download_service.active_download_rom_ids,
            download_queue=download_service.get_download_queue,
            save_sync_in_flight=save_sync_service.is_save_sync_in_flight,
            firmware_downloads_in_flight=firmware_service.is_downloading,
            save_directory_move_in_flight=save_sync_service.is_save_directory_move_in_flight,
            cleanup_running=prune_service.is_active,
            migration_running=migration_service.is_retrodeck_migration_running,
            held_claims=prune_conflicts.held_claims,
            read_update_failure=cfg.adapters.update_failure,
            attempts=cfg.adapters.update_attempt,
            download_asset=cfg.adapters.download_release_asset,
            staging=cfg.adapters.update_staging,
            units=cfg.adapters.transient_units,
            installer_environment=cfg.installer_environment,
            uow_factory=cfg.callbacks.uow_factory,
            acknowledge_failure_toast=update_outcome_service.acknowledge_update_failure_toast,
            emit=cfg.runtime.emit,
            clock=cfg.runtime.clock,
            sleeper=cfg.runtime.sleeper,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
            log_debug=cfg.callbacks.log_debug,
        ),
    )
    update_in_progress_binding.set(update_install_service.is_update_in_progress)

    update_output_service = UpdateOutputService(
        config=UpdateOutputServiceConfig(
            current_version=VERSION,
            read_update_failure=cfg.adapters.update_failure,
            failed_installer_started_at=update_install_service.failed_installer_started_at,
            journal=cfg.adapters.journal,
            loop=cfg.runtime.loop,
            logger=cfg.runtime.logger,
        ),
    )

    return ServicesBundle(
        prune_conflicts=prune_conflicts,
        save_sync_service=save_sync_service,
        playtime_service=playtime_service,
        sync_service=sync_service,
        download_service=download_service,
        rom_adoption_service=rom_adoption_service,
        rom_removal_service=rom_removal_service,
        prune_service=prune_service,
        prune_lease_service=prune_lease_service,
        data_inventory_service=data_inventory_service,
        firmware_service=firmware_service,
        sgdb_service=sgdb_service,
        shortcut_artwork_service=shortcut_artwork_service,
        metadata_service=metadata_service,
        achievements_service=achievements_service,
        migration_service=migration_service,
        game_detail_service=game_detail_service,
        artwork_service=artwork_service,
        shortcut_removal_service=shortcut_removal_service,
        settings_service=settings_service,
        core_service=core_service,
        emulator_sources_service=emulator_sources_service,
        tender_folder_service=tender_folder_service,
        disc_service=disc_service,
        version_switch_service=version_switch_service,
        connection_service=connection_service,
        startup_healing_service=startup_healing_service,
        shortcut_relocation_service=shortcut_relocation_service,
        shortcut_file_service=shortcut_file_service,
        update_check_service=update_check_service,
        update_outcome_service=update_outcome_service,
        update_install_service=update_install_service,
        update_output_service=update_output_service,
        session_lifecycle_service=session_lifecycle_service,
        game_process_service=game_process_service,
        relaunch_options_resolver=relaunch_options_resolver,
        leftover_tmp_cleanup_service=leftover_tmp_cleanup_service,
    )
