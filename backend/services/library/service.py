"""LibraryService façade.

The use cases the endpoints call — platform/collection metadata, sync
preview/apply/cancel, reporting, the ``roms``-derived queries. Each checks its
conflict rules at its entry and delegates to the part that does the work;
nothing that talks to RomM or runs a sync lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.library._state import LibrarySyncStateBox
    from services.library.fetcher import LibraryFetcher
    from services.library.reporter import SyncReporter
    from services.library.session_budget import SessionBudgetMonitor
    from services.library.sync_orchestrator import SyncOrchestrator
    from services.library.sync_preview import SyncPreview
    from services.library.sync_progress import SyncProgress
    from services.library.views import LibraryViews
    from services.protocols import ConflictRules


@dataclass(frozen=True)
class LibraryServiceConfig:
    """Frozen wiring bundle handed to ``LibraryService.__init__``: the parts the façade calls.

    The parts share the one ``sync_state_box``.
    """

    sync_state_box: LibrarySyncStateBox
    fetcher: LibraryFetcher
    progress: SyncProgress
    session_budget: SessionBudgetMonitor
    reporter: SyncReporter
    views: LibraryViews
    preview: SyncPreview
    orchestrator: SyncOrchestrator
    conflict_rules: ConflictRules


class LibraryService:
    """Façade for the library sync pipeline.

    Holds the parts its use cases delegate to — :class:`LibraryFetcher`
    (platform/collection roundtrips and the sync toggles), :class:`SyncOrchestrator`
    (a run's lifecycle and control + safety heartbeat), :class:`SyncPreview`
    (the read-only preview, its staged snapshot and its cancel),
    :class:`SyncProgress` (the status read), :class:`SessionBudgetMonitor`
    (Steam's renderer-heap budget), :class:`SyncReporter` (the apply's ack and
    Force Full Sync's reset) and :class:`LibraryViews` (the ``roms``-derived
    queries and the run history) — over the shared
    :class:`LibrarySyncStateBox`. ``bootstrap/library.py`` builds them and the
    parts they hold; the façade builds none. A use case an endpoint calls
    checks its conflict rules at its entry, under that endpoint's name, and
    raises the rule's refusal when one holds (GLOSSARY.md → Conflict rules).
    """

    def __init__(self, *, config: LibraryServiceConfig) -> None:
        self._rules = config.conflict_rules
        self._box = config.sync_state_box
        self._fetcher = config.fetcher
        self._progress = config.progress
        self._session_budget = config.session_budget
        self._reporter = config.reporter
        self._views = config.views
        self._preview = config.preview
        self._orchestrator = config.orchestrator

    # ── Public properties ────────────────────────────────────────

    def is_sync_in_flight(self) -> bool:
        """True while a sync run is in flight (RUNNING or CANCELLING; IDLE is not).

        Read-only predicate behind the sync rule.
        """
        return self._box.is_in_flight()

    @property
    def pending_sync(self) -> dict[int, dict[str, Any]]:
        """The shortcut entries emitted for the active unit, keyed by ``rom_id``.

        An abandoned chunk leaves them in place until the next unit is staged.
        """
        return self._box.pending_sync

    # ── Use cases the endpoints call ─────────────────────────────

    async def shutdown(self) -> None:
        """Stop a running library sync: its cancel flag, then its run cancelled and awaited."""
        await self._orchestrator.shutdown()

    # Platform metadata
    async def get_platforms(self):
        return await self._fetcher.get_platforms()

    async def save_platform_sync(self, platform_id, enabled):
        async with self._rules.hold("save_platform_sync", update=True, migration=True):
            return self._fetcher.save_platform_sync(platform_id, enabled)

    async def set_all_platforms_sync(self, enabled):
        async with self._rules.hold("set_all_platforms_sync", update=True, migration=True):
            return await self._fetcher.set_all_platforms_sync(enabled)

    # Collection metadata
    async def get_collections(self):
        return await self._fetcher.get_collections()

    async def save_collection_sync(self, collection_id, kind, enabled):
        async with self._rules.hold("save_collection_sync", update=True, migration=True):
            return self._fetcher.save_collection_sync(collection_id, kind, enabled)

    async def save_collections_sync(self, collection_ids, kind, enabled):
        async with self._rules.hold("save_collections_sync", update=True, migration=True):
            return self._fetcher.save_collections_sync(collection_ids, kind, enabled)

    # Sync control
    async def start_sync(self):
        async with self._rules.hold("start_sync", update=True, migration=True, prune=True):
            return self._orchestrator.start_sync()

    def cancel_sync(self, run_id=None):
        return self._orchestrator.cancel_sync(run_id)

    def sync_heartbeat(self):
        return self._orchestrator.sync_heartbeat()

    # Preview / apply
    async def sync_preview(self):
        async with self._rules.hold("sync_preview", update=True, migration=True, prune=True):
            return await self._preview.sync_preview()

    async def sync_apply_delta(self, preview_id):
        async with self._rules.hold("sync_apply_delta", update=True, migration=True, prune=True):
            return await self._orchestrator.sync_apply_delta(preview_id)

    def sync_cancel_preview(self):
        return self._preview.sync_cancel_preview()

    def get_pending_preview(self):
        return self._preview.get_pending_preview()

    def get_sync_status(self):
        return self._progress.get_sync_status()

    async def get_session_budget_status(self):
        return await self._session_budget.get_session_budget_status()

    async def renderer_above_ceiling(self) -> bool:
        """Implements ``services.protocols.RendererAboveCeilingFn``, through the sync's own monitor."""
        return await self._session_budget.above_ceiling()

    # Reporting
    async def report_unit_results(self, rom_id_to_app_id, run_id, unit_id, chunk_index):
        async with self._rules.hold("report_unit_results", prune=True):
            return await self._reporter.report_unit_results(rom_id_to_app_id, run_id, unit_id, chunk_index)

    # ``roms``-derived queries
    def get_registry_platforms(self):
        return self._views.get_registry_platforms()

    async def clear_sync_cache(self):
        async with self._rules.hold("clear_sync_cache", update=True, migration=True, prune=True):
            return self._reporter.clear_sync_cache()

    def get_sync_stats(self):
        return self._views.get_sync_stats()

    def get_sync_runs(self):
        return self._views.get_sync_runs()
