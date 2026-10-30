"""The library service's wiring — its state box and every part, built once and handed in.

``LibraryService`` builds none of its parts: this module builds the box and
every sub-service over it, in the order their holders need them, and hands the
façade the ones it calls. ``wire_services`` calls :func:`wire_library`
and keeps the service; a test calls it with fakes and reaches the parts it
built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from services.library import (
    ChunkDispatcher,
    ChunkDispatcherConfig,
    CollectionFetcher,
    CollectionFetcherConfig,
    CoverPreparer,
    CoverPreparerConfig,
    LibraryFetcher,
    LibraryFetcherConfig,
    LibraryService,
    LibraryServiceConfig,
    LibrarySyncStateBox,
    LibraryViews,
    LibraryViewsConfig,
    LocalLibraryReader,
    LocalLibraryReaderConfig,
    RunFinalizer,
    RunFinalizerConfig,
    SessionBudgetMonitor,
    SessionBudgetMonitorConfig,
    ShortcutLaunchResolver,
    ShortcutLaunchResolverConfig,
    SyncOrchestrator,
    SyncOrchestratorConfig,
    SyncPreview,
    SyncPreviewConfig,
    SyncProgress,
    SyncProgressConfig,
    SyncReporter,
    SyncReporterConfig,
    SyncRunRecorder,
    SyncRunRecorderConfig,
    UnitApplier,
    UnitApplierConfig,
)

if TYPE_CHECKING:
    import asyncio
    import logging

    from services.protocols import (
        ActiveCoreReader,
        ArtworkManager,
        Clock,
        ConflictRules,
        DebugLogger,
        DiscResolver,
        EmulatorSourcesReader,
        EventEmitter,
        RendererGcFn,
        RendererRssFn,
        RommLibraryApi,
        RunningRomIdsFn,
        SettingsPersister,
        ShortcutArtworkJob,
        Sleeper,
        SteamConfigStore,
        UnitOfWorkFactory,
        UuidGen,
    )


@dataclass(frozen=True)
class LibraryWiring:
    """What the library's parts are built from: adapters, runtime seams and peer services.

    Everything the parts take from outside the library, and nothing they take
    from one another — :func:`wire_library` hands each field on to the parts
    that hold it.
    """

    romm_api: RommLibraryApi
    steam_config: SteamConfigStore
    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    launcher_exe: str
    emit: EventEmitter
    clock: Clock
    uuid_gen: UuidGen
    sleeper: Sleeper
    settings_persister: SettingsPersister
    log_debug: DebugLogger
    artwork: ArtworkManager
    uow_factory: UnitOfWorkFactory
    active_core: ActiveCoreReader
    disc_resolver: DiscResolver
    emulator_sources: EmulatorSourcesReader
    renderer_rss: RendererRssFn
    renderer_gc: RendererGcFn
    conflict_rules: ConflictRules
    artwork_job: ShortcutArtworkJob
    running_rom_ids: RunningRomIdsFn


@dataclass(frozen=True)
class LibraryParts:
    """The library service and every part :func:`wire_library` built for it, each built once."""

    service: LibraryService
    sync_state_box: LibrarySyncStateBox
    local_library_reader: LocalLibraryReader
    progress: SyncProgress
    fetcher: LibraryFetcher
    collection_fetcher: CollectionFetcher
    shortcut_launch_resolver: ShortcutLaunchResolver
    session_budget: SessionBudgetMonitor
    reporter: SyncReporter
    run_finalizer: RunFinalizer
    views: LibraryViews
    chunk_dispatcher: ChunkDispatcher
    cover_preparer: CoverPreparer
    sync_run_recorder: SyncRunRecorder
    unit_applier: UnitApplier
    preview: SyncPreview
    orchestrator: SyncOrchestrator


def wire_library(wiring: LibraryWiring) -> LibraryParts:
    """Build the state box, every part over it, and the façade that holds the ones it calls."""
    box = LibrarySyncStateBox()

    local_library_reader = LocalLibraryReader(config=LocalLibraryReaderConfig(uow_factory=wiring.uow_factory))

    progress = SyncProgress(config=SyncProgressConfig(emit=wiring.emit, logger=wiring.logger, sync_state_box=box))

    fetcher = LibraryFetcher(
        config=LibraryFetcherConfig(
            romm_api=wiring.romm_api,
            settings=wiring.settings,
            loop=wiring.loop,
            logger=wiring.logger,
            settings_persister=wiring.settings_persister,
            log_debug=wiring.log_debug,
            uow_factory=wiring.uow_factory,
            sync_state_box=box,
            emit_progress=progress.emit_progress,
            local_library_reader=local_library_reader,
        )
    )

    collection_fetcher = CollectionFetcher(
        config=CollectionFetcherConfig(
            romm_api=wiring.romm_api,
            loop=wiring.loop,
            logger=wiring.logger,
            uow_factory=wiring.uow_factory,
            sync_state_box=box,
            emit_progress=progress.emit_progress,
            clock=wiring.clock,
        )
    )

    shortcut_launch_resolver = ShortcutLaunchResolver(
        config=ShortcutLaunchResolverConfig(
            uow_factory=wiring.uow_factory,
            active_core=wiring.active_core,
            disc_resolver=wiring.disc_resolver,
            emulator_sources=wiring.emulator_sources,
        )
    )

    session_budget = SessionBudgetMonitor(
        config=SessionBudgetMonitorConfig(
            loop=wiring.loop,
            logger=wiring.logger,
            sync_state_box=box,
            renderer_rss=wiring.renderer_rss,
            renderer_gc=wiring.renderer_gc,
        )
    )

    reporter = SyncReporter(
        config=SyncReporterConfig(
            steam_config=wiring.steam_config,
            settings=wiring.settings,
            loop=wiring.loop,
            logger=wiring.logger,
            clock=wiring.clock,
            uow_factory=wiring.uow_factory,
            sync_state_box=box,
            artwork=wiring.artwork,
        )
    )

    run_finalizer = RunFinalizer(
        config=RunFinalizerConfig(
            settings=wiring.settings,
            loop=wiring.loop,
            emit=wiring.emit,
            uow_factory=wiring.uow_factory,
            sync_state_box=box,
            emit_progress=progress.emit_progress,
            conflict_rules=wiring.conflict_rules,
        )
    )

    views = LibraryViews(config=LibraryViewsConfig(settings=wiring.settings, uow_factory=wiring.uow_factory))

    chunk_dispatcher = ChunkDispatcher(
        config=ChunkDispatcherConfig(
            logger=wiring.logger,
            emit=wiring.emit,
            clock=wiring.clock,
            sleeper=wiring.sleeper,
            sync_state_box=box,
            reporter=reporter,
            session_budget=session_budget,
            icon_placeholder_path=wiring.artwork_job.placeholder_path,
        )
    )

    cover_preparer = CoverPreparer(
        config=CoverPreparerConfig(
            artwork=wiring.artwork,
            sync_state_box=box,
            emit_progress=progress.emit_progress,
        )
    )

    sync_run_recorder = SyncRunRecorder(
        config=SyncRunRecorderConfig(
            clock=wiring.clock,
            uow_factory=wiring.uow_factory,
        )
    )

    unit_applier = UnitApplier(
        config=UnitApplierConfig(
            settings=wiring.settings,
            loop=wiring.loop,
            logger=wiring.logger,
            launcher_exe=wiring.launcher_exe,
            clock=wiring.clock,
            uow_factory=wiring.uow_factory,
            sync_state_box=box,
            fetcher=fetcher,
            collection_fetcher=collection_fetcher,
            shortcut_launch_resolver=shortcut_launch_resolver,
            local_library_reader=local_library_reader,
            cover_preparer=cover_preparer,
            chunk_dispatcher=chunk_dispatcher,
            emit_progress=progress.emit_progress,
            running_rom_ids=wiring.running_rom_ids,
        )
    )

    preview = SyncPreview(
        config=SyncPreviewConfig(
            settings=wiring.settings,
            loop=wiring.loop,
            logger=wiring.logger,
            launcher_exe=wiring.launcher_exe,
            clock=wiring.clock,
            uuid_gen=wiring.uuid_gen,
            sync_state_box=box,
            fetcher=fetcher,
            collection_fetcher=collection_fetcher,
            shortcut_launch_resolver=shortcut_launch_resolver,
            local_library_reader=local_library_reader,
            session_budget=session_budget,
            cover_preparer=cover_preparer,
            progress=progress,
            artwork_job=wiring.artwork_job,
            running_rom_ids=wiring.running_rom_ids,
        )
    )

    orchestrator = SyncOrchestrator(
        config=SyncOrchestratorConfig(
            loop=wiring.loop,
            logger=wiring.logger,
            emit=wiring.emit,
            clock=wiring.clock,
            uuid_gen=wiring.uuid_gen,
            sync_state_box=box,
            fetcher=fetcher,
            unit_applier=unit_applier,
            run_finalizer=run_finalizer,
            shortcut_launch_resolver=shortcut_launch_resolver,
            local_library_reader=local_library_reader,
            session_budget=session_budget,
            sync_run_recorder=sync_run_recorder,
            conflict_rules=wiring.conflict_rules,
            artwork_job=wiring.artwork_job,
            progress=progress,
            running_rom_ids=wiring.running_rom_ids,
        )
    )

    service = LibraryService(
        config=LibraryServiceConfig(
            sync_state_box=box,
            fetcher=fetcher,
            progress=progress,
            session_budget=session_budget,
            reporter=reporter,
            views=views,
            preview=preview,
            orchestrator=orchestrator,
            conflict_rules=wiring.conflict_rules,
            settings=wiring.settings,
        )
    )
    return LibraryParts(
        service=service,
        sync_state_box=box,
        local_library_reader=local_library_reader,
        progress=progress,
        fetcher=fetcher,
        collection_fetcher=collection_fetcher,
        shortcut_launch_resolver=shortcut_launch_resolver,
        session_budget=session_budget,
        reporter=reporter,
        run_finalizer=run_finalizer,
        views=views,
        chunk_dispatcher=chunk_dispatcher,
        cover_preparer=cover_preparer,
        sync_run_recorder=sync_run_recorder,
        unit_applier=unit_applier,
        preview=preview,
        orchestrator=orchestrator,
    )
