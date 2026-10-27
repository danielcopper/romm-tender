"""The ``library`` fixture every test under ``tests/services/library/`` runs on, and its factory.

``make_library`` builds a :class:`LibraryService` through ``wire_library``, the
way ``bootstrap/`` does, beside the artwork, metadata and shortcut-removal
services; ``library`` is what it builds with no argument.
All but the metadata service check conflict rules, and theirs share one
:class:`PruneConflicts`, which refuses nothing unless a test holds a claim.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from unittest.mock import MagicMock

import pytest
from _factories import _make_conflict_rules, _make_prune_conflicts
from fakes.fake_core_info_provider import FakeCoreInfoProvider
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_emulator_sources import FakeEmulatorSources
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_platform_systems import FakePlatformSystems
from fakes.fake_renderer_gc import FakeRendererGc
from fakes.fake_renderer_rss import FakeRendererRss
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_shortcut_artwork_job import FakeShortcutArtworkJob
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.system_time import FakeClock, FakeSleeper, FakeUuidGen

from adapters.cover_art_file_store import CoverArtFileStoreAdapter
from adapters.steam_config import SteamConfigAdapter
from bootstrap import LibraryWiring, wire_library
from lib.prune_conflicts import PruneConflicts
from services.active_core_resolver import ActiveCoreResolver, ActiveCoreResolverConfig
from services.artwork import ArtworkService, ArtworkServiceConfig
from services.library import (
    ChunkDispatcher,
    CollectionFetcher,
    CoverPreparer,
    LibraryService,
    LocalLibraryReader,
    ShortcutLaunchResolver,
    SyncRunRecorder,
    UnitApplier,
)
from services.metadata import MetadataService, MetadataServiceConfig
from services.shortcut_removal import ShortcutRemovalService, ShortcutRemovalServiceConfig


@dataclass
class LibraryHarness:
    """The wired library services and the seams a test seeds or asserts against.

    ``sync`` is the :class:`LibraryService` under test; the parts after it are
    the ones ``wire_library`` built that the façade does not hold. ``uow`` is
    the one :class:`FakeUnitOfWork` every service's factory wraps, so a row a
    test seeds there is the row the services read.
    """

    sync: LibraryService
    local_library_reader: LocalLibraryReader
    collection_fetcher: CollectionFetcher
    shortcut_launch_resolver: ShortcutLaunchResolver
    chunk_dispatcher: ChunkDispatcher
    cover_preparer: CoverPreparer
    sync_run_recorder: SyncRunRecorder
    unit_applier: UnitApplier
    artwork: ArtworkService
    metadata: MetadataService
    shortcut_removal: ShortcutRemovalService
    active_core: ActiveCoreResolver
    uow: FakeUnitOfWork
    romm_api: MagicMock | FakeRommApi
    steam_config: SteamConfigAdapter
    settings_persister: FakeSettingsPersister
    core_info: FakeCoreInfoProvider
    platform_core_reader: FakePlatformCoreReader
    renderer_rss: FakeRendererRss
    renderer_gc: FakeRendererGc
    prune_conflicts: PruneConflicts
    settings: dict[str, Any]
    emulator_sources: FakeEmulatorSources
    artwork_job: FakeShortcutArtworkJob


class MakeLibrary(Protocol):
    def __call__(
        self,
        *,
        loop: Any = None,
        romm_api: Any = None,
        settings_persister: Any = None,
        sleeper: Any = None,
        artwork: Any = None,
        renderer_rss: Any = None,
    ) -> LibraryHarness: ...


@pytest.fixture
async def make_library(tmp_path, emit, logger, home) -> MakeLibrary:
    """Build the library service and its peers over the fakes a test hands it.

    Each argument replaces one seam of the library service's parts: ``loop``
    the loop they run their executor hops on, ``romm_api`` the RomM API the two
    fetchers and the artwork service read, ``artwork`` the artwork seam the
    cover preparer and the reporter ask (the harness's ``artwork`` stays the
    real peer). The parts get the test's running loop unless one is handed; the
    peers always get it.
    """
    running = asyncio.get_running_loop()

    def build(
        *,
        loop=None,
        romm_api=None,
        settings_persister=None,
        sleeper=None,
        artwork=None,
        renderer_rss=None,
    ) -> LibraryHarness:
        settings: dict[str, Any] = {
            "romm_url": "",
            "romm_user": "",
            "romm_pass": "",
            "enabled_platforms": {},
            "enabled_collections": {"standard": {}, "smart": {}, "virtual": {}},
        }
        api = MagicMock() if romm_api is None else romm_api
        prune_conflicts = _make_prune_conflicts()
        persister = FakeSettingsPersister() if settings_persister is None else settings_persister
        steam_config = SteamConfigAdapter(user_home=str(home), logger=logger)

        # ONE shared FakeUnitOfWork across every sub-service + peer service so a
        # write by one (reporter upserting ``roms``) is visible to a read by
        # another (artwork resolving a cover, metadata building the app_id map).
        uow = FakeUnitOfWork()

        metadata_service = MetadataService(
            config=MetadataServiceConfig(
                loop=running,
                logger=logger,
                log_debug=lambda msg: None,
                uow_factory=FakeUnitOfWorkFactory(uow=uow),
            ),
        )

        artwork_service = ArtworkService(
            config=ArtworkServiceConfig(
                romm_api=api,
                steam_config=steam_config,
                cover_art_file_store=CoverArtFileStoreAdapter(),
                cover_cache_dir=str(tmp_path / "covers"),
                loop=running,
                logger=logger,
                get_pending_sync=dict,
                uow_factory=FakeUnitOfWorkFactory(uow=uow),
                conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
            ),
        )

        # Shared core-info fake so a sync-apply test can seed ``available_cores`` and
        # assert a per-game emulator_override (or per-platform core) re-bakes the
        # ``-e`` form. The real ActiveCoreResolver folds the DB override + the
        # per-platform map over this fake's es_systems default — the same seam the
        # orchestrator's bake site draws from.
        core_info = FakeCoreInfoProvider()
        platform_core_reader = FakePlatformCoreReader()
        active_core = ActiveCoreResolver(
            config=ActiveCoreResolverConfig(
                uow_factory=FakeUnitOfWorkFactory(uow=uow),
                core_info=core_info,
                platform_core_reader=platform_core_reader,
                platform_systems=FakePlatformSystems(),
                logger=logger,
            ),
        )

        # Session-budget seams default to "measurement unavailable" (RSS None) + a
        # no-op GC, so the gate is inert in the shared fixture; gate-specific tests
        # reassign ``library.renderer_rss.rss_kb`` / ``library.renderer_gc.result``
        # to drive a pause.
        rss = FakeRendererRss() if renderer_rss is None else renderer_rss
        renderer_gc = FakeRendererGc()
        emulator_sources = FakeEmulatorSources()
        artwork_job = FakeShortcutArtworkJob()

        parts = wire_library(
            LibraryWiring(
                romm_api=api,
                steam_config=steam_config,
                settings=settings,
                loop=running if loop is None else loop,
                logger=logger,
                launcher_exe=f"{home}/.local/bin/tender-rom-launcher",
                emit=emit,
                clock=FakeClock(),
                uuid_gen=FakeUuidGen(),
                sleeper=FakeSleeper() if sleeper is None else sleeper,
                settings_persister=persister,
                log_debug=lambda msg: None,
                artwork=artwork_service if artwork is None else artwork,
                uow_factory=FakeUnitOfWorkFactory(uow=uow),
                active_core=active_core,
                disc_resolver=FakeDiscResolver(),
                emulator_sources=emulator_sources,
                renderer_rss=rss,
                renderer_gc=renderer_gc,
                conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
                artwork_job=artwork_job,
            ),
        )

        shortcut_removal_service = ShortcutRemovalService(
            config=ShortcutRemovalServiceConfig(
                steam_config=steam_config,
                loop=running,
                logger=logger,
                artwork_remover=artwork_service,
                uow_factory=FakeUnitOfWorkFactory(uow=uow),
                conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
                held_claims=prune_conflicts.held_claims,
                artwork_job=artwork_job,
            ),
        )
        return LibraryHarness(
            sync=parts.service,
            local_library_reader=parts.local_library_reader,
            collection_fetcher=parts.collection_fetcher,
            shortcut_launch_resolver=parts.shortcut_launch_resolver,
            chunk_dispatcher=parts.chunk_dispatcher,
            cover_preparer=parts.cover_preparer,
            sync_run_recorder=parts.sync_run_recorder,
            unit_applier=parts.unit_applier,
            artwork=artwork_service,
            metadata=metadata_service,
            shortcut_removal=shortcut_removal_service,
            active_core=active_core,
            uow=uow,
            romm_api=api,
            steam_config=steam_config,
            settings_persister=persister,
            core_info=core_info,
            platform_core_reader=platform_core_reader,
            renderer_rss=rss,
            renderer_gc=renderer_gc,
            prune_conflicts=prune_conflicts,
            settings=settings,
            emulator_sources=emulator_sources,
            artwork_job=artwork_job,
        )

    return build


@pytest.fixture
def library(make_library) -> LibraryHarness:
    return make_library()
