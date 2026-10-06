"""Adapter half of the composition root — the only place adapters are constructed.

Adapter construction lives here so ``main.py`` only deals with the
process and the endpoints. ``bootstrap()`` also loads
and migrates settings as part of adapter wiring so adapters that bind
a live mutable settings dict (such as ``RommHttpAdapter``) bind the
migrated dict in a single pass; that same dict is returned for the
caller to keep as its source of truth.

The bundles defined here are the typed vocabulary the service half
consumes; nothing outside this module instantiates an adapter.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from models.shortcut_launcher import ShortcutLauncher

from adapters.adoption_move import AdoptionMoveAdapter
from adapters.asyncio_sleeper import AsyncioSleeper
from adapters.atlas_catalogue import AtlasCatalogueAdapter
from adapters.atlas_firmware import AtlasFirmwareAdapter, AtlasPlatformFirmwareAdapter
from adapters.atlas_saves import AtlasSaveLocationAdapter, describe_core_probe_interpreter
from adapters.cover_art_file_store import CoverArtFileStoreAdapter
from adapters.database_rename import DatabaseRenameAdapter
from adapters.debug_logger import SettingsAwareDebugLogger
from adapters.download_file import DownloadFileAdapter
from adapters.emulator_sources import EmulatorSourcesAdapter
from adapters.es_find_rules import EsFindRulesAdapter
from adapters.firmware_file import FirmwareFileAdapter
from adapters.game_process import GameProcessAdapter
from adapters.gavel_native import GavelNativeAdapter
from adapters.github_releases import GithubReleaseAdapter
from adapters.hostname import HostnameAdapter
from adapters.journal import JournalctlAdapter
from adapters.launcher_install import LauncherInstallAdapter
from adapters.machine_id import MachineIdAdapter
from adapters.migration_file import MigrationFileAdapter
from adapters.path_probe import PathProbeAdapter, ResolvedPathAdapter
from adapters.persistence import (
    PersistenceAdapter,
    PlatformCoreReaderAdapter,
    SettingsPersisterAdapter,
)
from adapters.prune_artifacts import PruneArtifactAdapter
from adapters.recovery_bundle import RecoveryBundleAdapter
from adapters.renderer_gc import RendererGcAdapter
from adapters.renderer_rss import RendererRssAdapter
from adapters.repositories.unit_of_work import SqliteUnitOfWork
from adapters.retrodeck_paths import RetroDeckPathsAdapter
from adapters.rom_files import RomFileAdapter
from adapters.romm.http import RommHttpAdapter
from adapters.romm.romm_api import RommApiAdapter
from adapters.save_file import SaveFileAdapter
from adapters.sgdb_artwork_cache import SgdbArtworkCacheAdapter
from adapters.sqlite_migrations import MIGRATIONS_DIR, apply_migrations
from adapters.steam_config import SteamConfigAdapter
from adapters.steam_recovery import SteamRecoveryAdapter
from adapters.steamgriddb import SteamGridDbAdapter
from adapters.system_clock import SystemClock
from adapters.system_uuid_gen import SystemUuidGen
from adapters.transient_unit import SystemdRunAdapter
from adapters.update_attempt import UpdateAttemptFileAdapter
from adapters.update_failure import UpdateFailureFileAdapter
from adapters.update_staging import UpdateStagingAdapter
from domain.identity import PACKAGE_NAME, VERSION
from domain.state_migrations import migrate_settings
from domain.update_install import UPDATE_DIR_NAME
from domain.user_data_location import launcher_in_bin_dir, launcher_path

if TYPE_CHECKING:
    import asyncio
    import logging
    from typing import Any

    from domain.app_directories import AppDirectories
    from domain.update_release import UpdateSource
    from services.protocols import (
        AdoptionMoveStore,
        Clock,
        ComputeSyncActionFn,
        CoreInfoProvider,
        CoverArtFileStore,
        DebugLogger,
        DirectoryFileListerFn,
        DownloadFileStore,
        EmulatorSourcesReader,
        EventEmitter,
        FirmwareFileStore,
        FirmwarePlatformResolver,
        FirmwareResolver,
        GameProcessControl,
        HostnameReader,
        JournalEntriesFn,
        LatestReleaseFn,
        MachineIdReader,
        MigrationFileStore,
        PathExistsReader,
        PlatformCoreReader,
        PruneArtifactStore,
        RecoveryBundleInventoryReader,
        RecoveryBundleStore,
        ReleaseAssetDownloadFn,
        RendererGcFn,
        RendererRssFn,
        ResolvedPathFn,
        ResolveUploadConflictFn,
        RetroDeckPaths,
        RomFileStore,
        RommApi,
        SandboxLauncherFn,
        SaveFileStore,
        SaveLocationReader,
        SettingsPersister,
        SgdbArtworkCache,
        Sleeper,
        SteamConfigStore,
        SteamInterfaceReader,
        SteamRecoveryStore,
        SystemKnownFn,
        SystemM3uSupportFn,
        SystemSupportedExtensionsFn,
        TransientUnitControl,
        UnitOfWorkFactory,
        UpdateAttemptStore,
        UpdateFailureFn,
        UpdateStagingStore,
        UuidGen,
    )

# Filename of the SQLite database under the data root, created by the schema
# migration runner at startup where no database exists yet. A literal of its own, never derived from
# ``PACKAGE_NAME`` (``domain/identity.py``) or ``APP_DIR_NAME``
# (``domain/user_data_location.py``), both of which spell its stem: derived from
# either, a rename of that one would start every user on an empty database and
# leave the library under a name nothing opens, with nothing failing and nothing
# said. ``domain/identity.py`` carries the rest of that split. ``install.sh``
# spells it again, and its tests hold the two equal.
DB_FILENAME = "romm-tender.db"

# What the database was called before the program was Tender. A start renames a
# file by this name to DB_FILENAME, and the pre-install check copies it where no
# DB_FILENAME exists yet.
LEGACY_DB_FILENAME = "romm_sync.db"


@dataclass(frozen=True)
class AdapterBundle:
    """Concrete I/O adapters wired into services."""

    http_adapter: RommHttpAdapter
    romm_api: RommApi
    steam_config: SteamConfigStore
    sgdb_adapter: SteamGridDbAdapter
    cover_art_file_store: CoverArtFileStore
    sgdb_artwork_cache: SgdbArtworkCache
    download_file_store: DownloadFileStore
    adoption_move: AdoptionMoveStore
    firmware_file_store: FirmwareFileStore
    firmware_resolver: FirmwareResolver
    platform_firmware_resolver: FirmwarePlatformResolver
    migration_file_store: MigrationFileStore
    rom_file_store: RomFileStore
    save_file_store: SaveFileStore
    path_probe: PathExistsReader
    resolve_path: ResolvedPathFn
    core_info_provider: CoreInfoProvider
    save_locations: SaveLocationReader
    emulator_sources: EmulatorSourcesReader
    renderer_rss: RendererRssFn
    renderer_gc: RendererGcFn
    game_process: GameProcessControl
    resolve_upload_conflict: ResolveUploadConflictFn
    compute_sync_action: ComputeSyncActionFn
    recovery_store: RecoveryBundleStore
    # The same object as ``recovery_store``, offered under the narrower
    # question: a consumer that only describes the bundles must not be able
    # to seal or validate one.
    recovery_inventory: RecoveryBundleInventoryReader
    prune_artifacts: PruneArtifactStore
    steam_recovery: SteamRecoveryStore
    latest_release: LatestReleaseFn
    update_failure: UpdateFailureFn
    update_attempt: UpdateAttemptStore
    download_release_asset: ReleaseAssetDownloadFn
    update_staging: UpdateStagingStore
    transient_units: TransientUnitControl
    journal: JournalEntriesFn


@dataclass(frozen=True)
class StateBundle:
    """Live mutable state shared across services."""

    settings: dict[str, Any]


@dataclass(frozen=True)
class RuntimeBundle:
    """Process-level runtime infrastructure (event loop, logger, the event sink's emit, time/UUID/sleep seams).

    ``steam`` is the host's reading of Steam's interface, which only the
    process hosting this backend can take and which ``main.py`` hands in the
    way it hands in the emit. The bundle carries no directory. Where anything
    lives is the ``AppDirectories`` the entry point resolved, which reaches a
    service as ``WiringConfig.directories`` — this bundle used to hold two
    paths beside the seams above, which is how a question about the plugin
    loader's own layout came to sit next to a question about the user's data.
    """

    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    emit: EventEmitter
    clock: Clock
    uuid_gen: UuidGen
    sleeper: Sleeper
    hostname_provider: HostnameReader
    machine_id_provider: MachineIdReader
    steam: SteamInterfaceReader


@dataclass(frozen=True)
class CallbackBundle:
    """Provider callables and persister Protocols injected into services."""

    retrodeck_paths: RetroDeckPaths
    platform_core_reader: PlatformCoreReader
    m3u_support: SystemM3uSupportFn
    sandbox_launcher: SandboxLauncherFn
    system_known: SystemKnownFn
    system_extensions: SystemSupportedExtensionsFn
    list_rom_dir_files: DirectoryFileListerFn
    settings_persister: SettingsPersister
    log_debug: DebugLogger
    uow_factory: UnitOfWorkFactory


@dataclass(frozen=True)
class RuntimeAdaptersBundle:
    """Concrete adapters for the Clock/UuidGen/Sleeper/HostnameReader/MachineIdReader seams.

    Bootstrap owns adapter instantiation, but the ``RuntimeBundle``
    handed to ``wire_services`` also needs runtime-only state the entry point
    introduces (the ``asyncio`` loop, the event sink's emit). This sub-bundle
    carries the seams bootstrap builds so ``build_application`` can compose the
    final ``RuntimeBundle`` without instantiating any adapters itself.
    """

    clock: Clock
    uuid_gen: UuidGen
    sleeper: Sleeper
    hostname_provider: HostnameReader
    machine_id_provider: MachineIdReader


@dataclass(frozen=True)
class BootstrapResult:
    """Typed return shape for :func:`bootstrap`.

    The four bundles carry every Protocol-typed seam and live state dict that
    services need; :attr:`directories` is the set this run was handed, passed
    back so every consumer reads the same seven fields rather than composing any
    of them again; :attr:`launcher` is this start's :class:`ShortcutLauncher`;
    and :attr:`user_agent` is ``<package name>/<version>``, composed once and
    used both as the outgoing User-Agent and as the identity the host answers
    under.
    """

    adapters: AdapterBundle
    stores: StateBundle
    callbacks: CallbackBundle
    runtime_adapters: RuntimeAdaptersBundle
    directories: AppDirectories
    launcher: ShortcutLauncher
    user_agent: str


def bootstrap(
    *,
    directories: AppDirectories,
    update_source: UpdateSource,
    user_home: str,
    logger: logging.Logger,
) -> BootstrapResult:
    """Build every adapter and bundle them for ``build_application``.

    Bootstrap owns adapter instantiation and is the only path that
    constructs ``PersistenceAdapter``. Settings are loaded + migrated
    inside here so the ``SettingsPersisterAdapter`` binds the live dict
    at construction; mutating that dict from the caller side is visible
    to every adapter/service that holds the same reference.

    Parameters
    ----------
    directories:
        Where this program's directories ARE — resolved from the environment by
        the entry point and handed in, never derived here. Bootstrap composing
        them itself is what used to make packaging decide where a user's library
        lived.
    update_source:
        Where the newest release is asked for — resolved from the environment
        by the entry point, like ``directories``.
    user_home:
        The user's home directory, for RetroDECK and Steam path lookups and for
        the recovery root.
    logger:
        The configured root logger.

    Returns
    -------
    :class:`BootstrapResult`
        What ``build_application`` builds the services and the
        :class:`Application` from.
    """
    # SystemClock is dependency-free; construct it first so the single shared
    # instance threads into PersistenceAdapter (corrupt-settings backup stamp)
    # and every later seam (uuid_gen/sleeper neighbours, runtime bundle).
    clock = SystemClock()

    # The launcher is installed outside every directory this program owns: a
    # shortcut's ``exe`` names it, and that is the one thing about a shortcut
    # this program cannot repair from inside. The bin root is XDG's place for a
    # user's own executables, and a place the uninstaller never removes — so a
    # shortcut goes on working after the program that wrote it is gone. It is
    # written on every start rather than once, so the launcher a shortcut runs
    # is always the one this release ships — installed once, it would freeze at
    # whatever version the day of the move happened to bring.
    #
    # The path a new shortcut is built against follows the INSTALL, not the
    # intent: the bin root only where this start actually got the launcher into
    # it, and the copy the release ships otherwise. A start whose write failed is
    # the second case, and pointing a shortcut at a home the write never reached
    # would name a file that is not there.
    installed_launcher = launcher_in_bin_dir(directories.bin_dir)
    launcher_at_home = LauncherInstallAdapter(
        source=launcher_path(directories.code_dir),
        destination=installed_launcher,
        logger=logger,
    ).install()
    launcher = ShortcutLauncher(
        path=installed_launcher if launcher_at_home else launcher_path(directories.code_dir),
        at_home=launcher_at_home,
    )

    # The database is moved from its old name before anything opens it under the
    # current one: the migration runner would otherwise create an empty database
    # beside the user's library. Here rather than in the entry point because on
    # a start this runs under the single-instance lock — a second backend
    # refused a moment later must not have renamed the file under the running
    # one.
    db_path = os.path.join(directories.data_dir, DB_FILENAME)
    DatabaseRenameAdapter(
        legacy=os.path.join(directories.data_dir, LEGACY_DB_FILENAME),
        current=db_path,
        logger=logger,
    ).rename()

    # Bring the on-disk SQLite schema up to date before any service is wired —
    # the composition root owns startup infra. SQLite is the sole persistence
    # backend: there is no JSON fallback, so a failed or unopenable database is
    # fatal. Log the cause, then re-raise so bootstrap aborts and the backend
    # does not start — matching the RomM-minimum-version
    # gate's "inert until the environment is fixed" posture.
    try:
        apply_migrations(db_path, MIGRATIONS_DIR, logger=logger)
    except Exception:
        logger.exception("SQLite schema migration failed; the backend cannot start")
        raise

    # The runtime Unit-of-Work factory: each call opens a fresh sync sqlite3
    # connection on db_path (ADR-0004). Wired here but not yet threaded into any
    # service config — the service cutover (#784) consumes it.
    uow_factory: UnitOfWorkFactory = functools.partial(SqliteUnitOfWork, db_path)

    retrodeck_paths = RetroDeckPathsAdapter(user_home=user_home, logger=logger)
    es_find_rules = EsFindRulesAdapter(logger=logger, user_home=user_home)

    persistence = PersistenceAdapter(directories.config_dir, logger, clock=clock)
    settings = migrate_settings(persistence.load_settings())
    # If load_settings quarantined a corrupt file this boot, fold the reset into
    # the settings dict as a persistent marker. Set AFTER migration and BEFORE
    # the save so it lands in the fresh settings.json and survives a backend
    # restart — the frontend surfaces it as a banner (QAM + game detail) until the
    # next successful sign-in clears it (ConnectionService pops it on persist).
    if persistence.corrupt_reset is not None:
        settings["_settings_reset_notice"] = {"backed_up_to": persistence.corrupt_reset["backed_up_to"]}
    persistence.save_settings(settings)
    settings_persister = SettingsPersisterAdapter(persistence, settings)
    # Binds the same live settings dict so the per-platform-core fan-out resolves
    # the freshly-written value, not a snapshot.
    platform_core_reader = PlatformCoreReaderAdapter(settings)
    # Single source of truth for outgoing User-Agent — thread the string to the
    # three adapters that talk to a server off this machine (RomM, SteamGridDB
    # and GitHub's releases API, which refuses a request with none).
    # ``RendererGcAdapter`` also speaks HTTP, to Steam's own debugger on
    # localhost, and takes no UA. Bot Fight Mode on Cloudflare
    # blocks the default ``Python-urllib`` UA before requests reach self-hosted
    # RomM (#249). Both halves come from ``domain/identity.py``, and so does the
    # recovery root built out of the same name below: a literal spelled here
    # would be a second spelling of the package, free to drift away from it.
    user_agent = f"{PACKAGE_NAME}/{VERSION}"
    recovery_store = RecoveryBundleAdapter(
        user_home=user_home,
        package_name=PACKAGE_NAME,
        version=VERSION,
    )
    # The CACHE root: this adapter's whole subject is ``covers/`` and
    # ``artwork/``, which live there and not under the data root.
    prune_artifacts = PruneArtifactAdapter(cache_dir=directories.cache_dir)
    steam_recovery = SteamRecoveryAdapter(user_home=user_home, logger=logger)
    # Built here rather than beside its peers below because the transport wants
    # it: `log_level` gates this seam, where a bare `logger.debug` reaches no log
    # the user reads — the root's level, and why nothing moves it, is
    # `host.logging_setup.configure_logging`'s.
    debug_logger = SettingsAwareDebugLogger(settings=settings, logger=logger)
    http_adapter = RommHttpAdapter(settings, directories.code_dir, logger, user_agent, log_debug=debug_logger)
    # Once, at start, rather than per request: the adapter reads the flag on
    # every call, and a line per request would drown the log it is written into.
    if settings.get("romm_allow_insecure_ssl", False):
        logger.warning("Certificate verification is off for RomM requests (romm_allow_insecure_ssl is on)")
    romm_api = RommApiAdapter(http_adapter)
    steam_config = SteamConfigAdapter(user_home=user_home, logger=logger)
    sgdb_adapter = SteamGridDbAdapter(settings=settings, logger=logger, user_agent=user_agent)
    cover_art_file_store = CoverArtFileStoreAdapter()
    sgdb_artwork_cache = SgdbArtworkCacheAdapter(cache_dir=directories.cache_dir)
    download_file_store = DownloadFileAdapter()
    adoption_move = AdoptionMoveAdapter()
    firmware_file_store = FirmwareFileAdapter()
    migration_file_store = MigrationFileAdapter()
    rom_file_store = RomFileAdapter()
    save_file_store = SaveFileAdapter(logger=logger)
    path_probe = PathProbeAdapter()
    resolve_path = ResolvedPathAdapter()
    renderer_rss = RendererRssAdapter()
    renderer_gc = RendererGcAdapter(logger=logger)
    github_releases = GithubReleaseAdapter(
        api_url=update_source.release_api,
        user_agent=user_agent,
        log_debug=debug_logger,
    )
    update_failure = UpdateFailureFileAdapter(state_dir=directories.state_dir, log_debug=debug_logger)
    update_attempt = UpdateAttemptFileAdapter(state_dir=directories.state_dir, log_debug=debug_logger)
    update_staging = UpdateStagingAdapter(directory=os.path.join(directories.cache_dir, UPDATE_DIR_NAME))
    transient_units = SystemdRunAdapter()
    journal = JournalctlAdapter(log_debug=debug_logger)
    game_process = GameProcessAdapter()
    # The compiled gavel core owns both save-sync decisions — the per-file sync
    # action and the upload-409 resolution. Loaded eagerly so a missing /
    # wrong-architecture artifact is fatal here (like the SQLite migration gate
    # above) rather than surfacing mid-sync — there is no Python fallback
    # (GavelNativeLoadError propagates, Tender stays inert).
    gavel = GavelNativeAdapter()
    uuid_gen = SystemUuidGen()
    sleeper = AsyncioSleeper()
    hostname_provider = HostnameAdapter()
    machine_id_provider = MachineIdAdapter()
    logger.info(describe_core_probe_interpreter())
    # Built after the debug logger because the resolver never logs on its own:
    # its caveats are the whole degradation channel and reach the log through
    # this seam or not at all. That holds for both firmware questions and for
    # the emulator catalogue. Every one of them asks the one holder of the
    # emulator sources, which picks the source that answers.
    emulator_sources = EmulatorSourcesAdapter(user_home=user_home, settings=settings, log_debug=debug_logger)
    firmware_resolver = AtlasFirmwareAdapter(sources=emulator_sources, log_debug=debug_logger)
    platform_firmware_resolver = AtlasPlatformFirmwareAdapter(sources=emulator_sources, log_debug=debug_logger)
    emulator_catalogue = AtlasCatalogueAdapter(
        sources=emulator_sources,
        emulator_installed=es_find_rules.command_emulator_installed,
        log_debug=debug_logger,
    )
    save_locations = AtlasSaveLocationAdapter(sources=emulator_sources, log_debug=debug_logger)

    adapters = AdapterBundle(
        http_adapter=http_adapter,
        romm_api=romm_api,
        steam_config=steam_config,
        sgdb_adapter=sgdb_adapter,
        cover_art_file_store=cover_art_file_store,
        sgdb_artwork_cache=sgdb_artwork_cache,
        download_file_store=download_file_store,
        adoption_move=adoption_move,
        firmware_file_store=firmware_file_store,
        firmware_resolver=firmware_resolver,
        platform_firmware_resolver=platform_firmware_resolver,
        migration_file_store=migration_file_store,
        rom_file_store=rom_file_store,
        save_file_store=save_file_store,
        path_probe=path_probe,
        resolve_path=resolve_path,
        core_info_provider=emulator_catalogue,
        save_locations=save_locations,
        emulator_sources=emulator_sources,
        renderer_rss=renderer_rss,
        renderer_gc=renderer_gc,
        game_process=game_process,
        resolve_upload_conflict=gavel,
        compute_sync_action=gavel.compute_sync_action,
        recovery_store=recovery_store,
        recovery_inventory=recovery_store,
        prune_artifacts=prune_artifacts,
        steam_recovery=steam_recovery,
        latest_release=github_releases.get_latest_release,
        update_failure=update_failure.read_update_failure,
        update_attempt=update_attempt,
        download_release_asset=github_releases.download_asset,
        update_staging=update_staging,
        transient_units=transient_units,
        journal=journal,
    )
    stores = StateBundle(
        settings=settings,
    )
    callbacks = CallbackBundle(
        retrodeck_paths=retrodeck_paths,
        platform_core_reader=platform_core_reader,
        m3u_support=emulator_catalogue.system_supports_m3u,
        sandbox_launcher=es_find_rules.resolve_sandbox_launcher,
        system_known=emulator_catalogue.is_known_system,
        system_extensions=emulator_catalogue.get_supported_extensions,
        list_rom_dir_files=download_file_store.list_files,
        settings_persister=settings_persister,
        log_debug=debug_logger,
        uow_factory=uow_factory,
    )
    runtime_adapters = RuntimeAdaptersBundle(
        clock=clock,
        uuid_gen=uuid_gen,
        sleeper=sleeper,
        hostname_provider=hostname_provider,
        machine_id_provider=machine_id_provider,
    )
    return BootstrapResult(
        adapters=adapters,
        stores=stores,
        callbacks=callbacks,
        runtime_adapters=runtime_adapters,
        directories=directories,
        launcher=launcher,
        user_agent=user_agent,
    )
