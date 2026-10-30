"""Library sync subsystem.

The package's public API is the :class:`LibraryService` façade, which the
endpoints call for platform/collection metadata, sync preview/apply,
post-apply reporting and the ``roms``-derived queries. Its parts — the library
sync sub-services (:class:`LibraryFetcher`, :class:`CollectionFetcher`,
:class:`SyncOrchestrator`, :class:`UnitApplier`, :class:`SyncPreview`,
:class:`SyncProgress`, :class:`SyncReporter`, :class:`RunFinalizer`,
:class:`SessionBudgetMonitor`, :class:`ShortcutLaunchResolver`,
:class:`ChunkDispatcher`, :class:`CoverPreparer`, :class:`SyncRunRecorder`,
:class:`LocalLibraryReader`) over a shared :class:`LibrarySyncStateBox` — are built only by the composition root, from
the re-exports below, and handed to the parts that hold them. RomM
communication goes through Protocol-typed adapters.
"""

from services.library._state import LibrarySyncStateBox
from services.library.chunk_dispatcher import ChunkDispatcher, ChunkDispatcherConfig
from services.library.collection_fetcher import CollectionFetcher, CollectionFetcherConfig
from services.library.cover_preparer import CoverPreparer, CoverPreparerConfig
from services.library.fetcher import LibraryFetcher, LibraryFetcherConfig
from services.library.local_library_reader import LocalLibraryReader, LocalLibraryReaderConfig
from services.library.reporter import SyncReporter, SyncReporterConfig
from services.library.run_finalizer import RunFinalizer, RunFinalizerConfig
from services.library.service import LibraryService, LibraryServiceConfig
from services.library.session_budget import SessionBudgetMonitor, SessionBudgetMonitorConfig
from services.library.shortcut_launch_resolver import ShortcutLaunchResolver, ShortcutLaunchResolverConfig
from services.library.sync_orchestrator import SyncOrchestrator, SyncOrchestratorConfig
from services.library.sync_preview import SyncPreview, SyncPreviewConfig
from services.library.sync_progress import SyncProgress, SyncProgressConfig
from services.library.sync_run_recorder import SyncRunRecorder, SyncRunRecorderConfig
from services.library.unit_apply import UnitApplier, UnitApplierConfig

__all__ = [
    "ChunkDispatcher",
    "ChunkDispatcherConfig",
    "CollectionFetcher",
    "CollectionFetcherConfig",
    "CoverPreparer",
    "CoverPreparerConfig",
    "LibraryFetcher",
    "LibraryFetcherConfig",
    "LibraryService",
    "LibraryServiceConfig",
    "LibrarySyncStateBox",
    "LocalLibraryReader",
    "LocalLibraryReaderConfig",
    "RunFinalizer",
    "RunFinalizerConfig",
    "SessionBudgetMonitor",
    "SessionBudgetMonitorConfig",
    "ShortcutLaunchResolver",
    "ShortcutLaunchResolverConfig",
    "SyncOrchestrator",
    "SyncOrchestratorConfig",
    "SyncPreview",
    "SyncPreviewConfig",
    "SyncProgress",
    "SyncProgressConfig",
    "SyncReporter",
    "SyncReporterConfig",
    "SyncRunRecorder",
    "SyncRunRecorderConfig",
    "UnitApplier",
    "UnitApplierConfig",
]
