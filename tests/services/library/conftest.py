"""Shared fixtures for the LibraryService sub-service test files.

Builds the full LibraryService composition (fetcher + orchestrator +
reporter) plus the peer services LibraryService coordinates with
(MetadataService, ArtworkService, ShortcutRemovalService), whose conflict
rules share one prune conflicts and refuse nothing unless a test holds a
claim. All test files under ``tests/services/library/`` consume the same
``library`` fixture so coverage of the façade integration and the
sub-service internals sits on top of an identical setup.
"""

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from _factories import _make_conflict_rules, _make_prune_conflicts
from fakes.fake_core_info_provider import FakeCoreInfoProvider, FakeSandboxLauncher
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_renderer_gc import FakeRendererGc
from fakes.fake_renderer_rss import FakeRendererRss
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock, FakeSleeper, FakeUuidGen

from adapters.cover_art_file_store import CoverArtFileStoreAdapter
from adapters.steam_config import SteamConfigAdapter
from lib.prune_conflicts import PruneConflicts
from services.active_core_resolver import ActiveCoreResolver, ActiveCoreResolverConfig
from services.artwork import ArtworkService, ArtworkServiceConfig
from services.library import LibraryService, LibraryServiceConfig
from services.metadata import MetadataService, MetadataServiceConfig
from services.shortcut_removal import ShortcutRemovalService, ShortcutRemovalServiceConfig
from tests.services.library._helpers import rebind_loop


@dataclass
class LibraryHarness:
    """What a library test reaches for: the wired services and the seams they share.

    ``sync`` is the :class:`LibraryService` under test; ``artwork``,
    ``metadata`` and ``shortcut_removal`` are its peers, and ``active_core``
    the resolver its launch-option bake draws from. ``uow`` is the one
    :class:`FakeUnitOfWork` behind every service's factory, the handle tests
    seed and assert against. ``romm_api`` starts as a ``MagicMock`` and is
    replaced by ``_use_fake_romm`` for the end-to-end paths.
    """

    sync: LibraryService
    artwork: ArtworkService
    metadata: MetadataService
    shortcut_removal: ShortcutRemovalService
    active_core: ActiveCoreResolver
    uow: FakeUnitOfWork
    romm_api: Any
    steam_config: SteamConfigAdapter
    settings_persister: FakeSettingsPersister
    core_info: FakeCoreInfoProvider
    platform_core_reader: FakePlatformCoreReader
    renderer_rss: FakeRendererRss
    renderer_gc: FakeRendererGc
    prune_conflicts: PruneConflicts
    settings: dict[str, Any]
    emit: AsyncMock


@pytest.fixture
def library(tmp_path, emit, logger, home) -> LibraryHarness:
    settings: dict[str, Any] = {
        "romm_url": "",
        "romm_user": "",
        "romm_pass": "",
        "enabled_platforms": {},
        "enabled_collections": {"standard": {}, "smart": {}, "virtual": {}},
    }
    romm_api = MagicMock()
    prune_conflicts = _make_prune_conflicts()
    settings_persister = FakeSettingsPersister()
    steam_config = SteamConfigAdapter(user_home=str(home), logger=logger)

    # ONE shared FakeUnitOfWork across every sub-service + peer service so a
    # write by one (reporter upserting ``roms``) is visible to a read by
    # another (artwork resolving a cover, metadata building the app_id map).
    # Each service gets its own factory wrapping the same unit.
    uow = FakeUnitOfWork()

    metadata_service = MetadataService(
        config=MetadataServiceConfig(
            loop=running_loop(),
            logger=logger,
            log_debug=lambda msg: None,
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
        ),
    )

    artwork_service = ArtworkService(
        config=ArtworkServiceConfig(
            romm_api=romm_api,
            steam_config=steam_config,
            cover_art_file_store=CoverArtFileStoreAdapter(),
            cover_cache_dir=str(tmp_path / "covers"),
            loop=running_loop(),
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
            sandbox_launcher=FakeSandboxLauncher(),
            platform_core_reader=platform_core_reader,
            resolve_system=lambda platform_slug, platform_fs_slug=None: platform_slug,
            logger=logger,
        ),
    )

    # Session-budget seams default to "measurement unavailable" (RSS None) + a
    # no-op GC, so the gate is inert in the shared fixture; gate-specific tests
    # reassign ``library.renderer_rss.rss_kb`` / ``.result`` to drive a pause.
    renderer_rss = FakeRendererRss()
    renderer_gc = FakeRendererGc()

    sync_service = LibraryService(
        config=LibraryServiceConfig(
            romm_api=romm_api,
            steam_config=steam_config,
            settings=settings,
            loop=running_loop(),
            logger=logger,
            launcher_exe=f"{home}/.local/bin/tender-rom-launcher",
            emit=emit,
            clock=FakeClock(),
            uuid_gen=FakeUuidGen(),
            sleeper=FakeSleeper(),
            settings_persister=settings_persister,
            log_debug=lambda msg: None,
            artwork=artwork_service,
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            active_core=active_core,
            disc_resolver=FakeDiscResolver(),
            renderer_rss=renderer_rss,
            renderer_gc=renderer_gc,
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
        ),
    )

    shortcut_removal_service = ShortcutRemovalService(
        config=ShortcutRemovalServiceConfig(
            steam_config=steam_config,
            loop=running_loop(),
            logger=logger,
            artwork_remover=artwork_service,
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
        ),
    )
    return LibraryHarness(
        sync=sync_service,
        artwork=artwork_service,
        metadata=metadata_service,
        shortcut_removal=shortcut_removal_service,
        active_core=active_core,
        uow=uow,
        romm_api=romm_api,
        steam_config=steam_config,
        settings_persister=settings_persister,
        core_info=core_info,
        platform_core_reader=platform_core_reader,
        renderer_rss=renderer_rss,
        renderer_gc=renderer_gc,
        prune_conflicts=prune_conflicts,
        settings=settings,
        emit=emit,
    )


@pytest.fixture(autouse=True)
async def _set_event_loop(library):
    """Rebind every service's loop to the running event loop for async tests."""
    rebind_loop(library.sync, asyncio.get_running_loop())
    library.artwork._loop = asyncio.get_running_loop()
    library.shortcut_removal._loop = asyncio.get_running_loop()
    library.metadata._loop = asyncio.get_running_loop()
