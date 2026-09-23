"""Construction-time wiring bundle for ``SaveService``.

Holds every dependency SaveService needs at construction time —
Protocol-typed adapters, runtime infrastructure, live mutable state
references, plugin metadata, and callbacks into other services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    import logging

    from services.protocols import (
        ActiveCoreReader,
        Clock,
        ComputeSyncActionFn,
        DebugLogger,
        EventEmitter,
        HostnameReader,
        MachineIdReader,
        MigrationPendingFn,
        ResolveUploadConflictFn,
        RetroDeckPaths,
        RetryStrategy,
        RommSyncApi,
        SaveFileStore,
        SaveLocationReader,
        SettingsPersister,
        SystemResolver,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class SaveServiceConfig:
    """Frozen wiring bundle handed to ``SaveService.__init__``.

    Parameters
    ----------
    romm_api:
        Protocol adapter for all RomM save/notes HTTP operations.
    retry:
        Retry strategy — provides ``with_retry`` and ``is_retryable``.
    resolve_upload_conflict:
        ``ResolveUploadConflictFn`` seam — the upload-409 resolution kernel,
        backed at runtime by the compiled gavel native core. Threaded down to
        the ``MatrixExecutor`` where the upload-409 backstop calls it to decide
        ``"download"`` vs ``"conflict"`` purely from hashes.
    compute_sync_action:
        ``ComputeSyncActionFn`` seam — the per-``(rom, filename, slot)`` sync
        decision, backed at runtime by the same compiled gavel native core.
        Threaded down to the ``MatrixExecutor``, which calls it once per file to
        pick ``Skip`` / ``Upload`` / ``Download`` / ``Conflict``.
    settings:
        Live reference to the main plugin settings dict.
    settings_persister:
        Protocol-typed zero-arg flush for ``settings.json``. SaveService
        calls ``.save_settings()`` after mutating the save-sync feature
        toggles or the device label in the live ``settings`` dict — those
        values live in settings.json, not the save-sync aggregate.
    save_file_store:
        Protocol-typed filesystem adapter for local save files. Owns the
        raw POSIX, ``open()``, ``tempfile``, and ``hashlib``-on-file
        calls SaveService and its sub-services use when reading,
        writing, backing up, hashing, and removing local save files.
    loop:
        The plugin's ``asyncio`` event loop (for ``run_in_executor``).
    logger:
        Standard-library logger, injected rather than fetched.
    retrodeck_paths:
        Bundled accessor for the four RetroDECK runtime directory
        paths. SaveService consumes ``saves_path()`` and ``roms_path()``;
        the BIOS and home accessors are unused here but the Protocol
        is bundled so every service shares a uniform shape.
    active_core:
        ``ActiveCoreReader`` seam resolving the active RetroArch core for a
        ROM by ``rom_id``. Returns ``(core_so, label)``; either may be None if
        unresolved. Folds the per-game ``emulator_override`` pin over the
        system-layer ES-DE resolution so the save answer / save-emulator tag /
        core-change warning key off the same core the ROM launches with.
    hostname_provider:
        ``HostnameReader`` Protocol seam — supplies the local device
        hostname used as the registered device name during initial
        server-side device registration.
    machine_id_provider:
        ``MachineIdReader`` Protocol seam — supplies the stable
        ``/etc/machine-id`` value sent as the RomM ``hostname``
        fingerprint during initial server-side device registration so the
        server dedupes this device across reinstalls. ``None`` when the
        machine id is unreadable, which degrades registration to the
        no-fingerprint path.
    emit:
        Event emitter for pushing save-sync progress to the frontend.
    is_retrodeck_migration_pending:
        Callback returning ``True`` when a RetroDECK migration is in
        flight; SaveService gates destructive operations on this signal.
    log_debug:
        ``DebugLogger`` Protocol seam — routes through the user's QAM
        log-level filter. Injected directly into each sub-service that
        needs it; not reached through the ``_save_service`` back-ref.
    uow_factory:
        ``UnitOfWorkFactory`` Protocol seam — opens a fresh transactional
        Unit of Work over the SQLite repositories. The saves vertical
        reads/writes the ``rom_save_sync_states`` aggregate + ``kv_config``
        device id through it; each public callable owns a narrow
        read→I/O→write bracket (ADR-0006).
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
    retrodeck_paths: RetroDeckPaths
    active_core: ActiveCoreReader
    save_locations: SaveLocationReader
    resolve_system: SystemResolver
    hostname_provider: HostnameReader
    machine_id_provider: MachineIdReader
    log_debug: DebugLogger
    emit: EventEmitter
    is_retrodeck_migration_pending: MigrationPendingFn
    uow_factory: UnitOfWorkFactory
