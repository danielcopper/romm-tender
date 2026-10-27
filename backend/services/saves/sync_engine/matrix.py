"""Newest-wins matrix executor: the per-file sync decision and its dispatch.

The decision layer for "which side wins for this file", and the dispatch
of each decision to the step that carries it out. Moving a file and
recording what moved is :class:`services.saves.save_transfer.SaveTransfer`'s.
Read-only matrix consumption (status reporting) lives in StatusService; the
loaded :class:`RomSaveSyncState` aggregate is threaded in by the operation
entry, which owns the Unit-of-Work read/write bracketing this executor's
in-memory mutations (ADR-0006). Rom-level lock coordination and public
use-case orchestration live on
:class:`services.saves.sync_engine.engine.SyncEngine`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from domain.iso_time import parse_iso_to_epoch
from domain.rom_save_sync_state import FileSyncState, RomSaveSyncState
from domain.save_slot import filter_saves_to_slot
from domain.sync_action import Conflict, Download, Skip, SyncAction, Upload
from lib.errors import RommApiError, RommConflictError, classify_error
from services.saves._helpers import local_save_target
from services.saves._sync_stop import SyncStopped

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterator

    from domain.live_device_id import LiveDeviceId
    from domain.save_answer import SaveAnswer, SaveComponent
    from domain.save_measure import SaveFileMeasure
    from services.protocols import (
        Clock,
        ComputeSyncActionFn,
        DebugLogger,
        ResolveUploadConflictFn,
        RetryStrategy,
        RommSyncApi,
        SaveFileStore,
    )
    from services.saves._sync_stop import SyncStop
    from services.saves.rom_info import RomInfoService
    from services.saves.save_transfer import SaveTransfer


class TransferDirection(Enum):
    """Which way a dispatched save transfer actually moved (#250).

    Returned by the sync dispatch so ``sync_rom_saves`` can split the total
    transfer count into per-direction upload / download tallies for the
    completion toast. Direction reflects what moved on the wire, not the
    planned :class:`SyncAction`: an ``Upload`` that RomM rejects with a 409 and
    the backstop downgrades to a fresh-head download is attributed as
    ``DOWNLOAD``. A skip, a surfaced conflict, or a failed transfer yields no
    direction (the dispatch returns ``None``).
    """

    UPLOAD = "upload"
    DOWNLOAD = "download"


@dataclass(frozen=True)
class MatrixOutcome:
    """One newest-wins matrix evaluation, ready for sync dispatch or status rendering.

    Yielded by :meth:`MatrixExecutor.iter_matrix_outcomes` for both consumers
    (sync I/O dispatch, status DTO building). All fields are read-only —
    the iterator runs pure compute and consumers drive their own side
    effects.
    """

    filename: str
    action: SyncAction
    local_path: str | None
    local_hash: str | None
    local_mtime_iso: str | None
    local_size: int | None
    local_content_size: int | None
    file_state: FileSyncState
    server_candidates: list[dict[str, Any]]


@dataclass(frozen=True)
class DispatchSink:
    """The two output accumulators a single ROM's sync dispatch appends to.

    Holds the mutable ``errors`` and ``conflicts`` lists that
    :meth:`MatrixExecutor._dispatch_sync_action` (and its sub-dispatchers)
    append onto. The dataclass itself is frozen — only the referenced lists
    are mutated — so it threads both sinks through as one argument without
    becoming a stateful object.
    """

    errors: list[str]
    conflicts: list[dict[str, Any]]


@dataclass(frozen=True)
class SyncRunOptions:
    """The settings-derived values threaded through one ROM's sync dispatch.

    Resolved once per sync run and constant across every file of the ROM, so
    they ride through ``_dispatch_sync_action`` / ``_dispatch_upload`` as one
    argument rather than widening every signature (mirrors ``DispatchSink``).
    ``default_slot`` seeds the active slot on a brand-new ROM's first sync and
    selects the upload slot; ``autocleanup_limit`` caps server-retained versions
    on the POST (create) upload.
    """

    default_slot: str | None = None
    autocleanup_limit: int | None = None


@dataclass(frozen=True)
class RomDispatchContext:
    """The ROM-level constants threaded through one ROM's sync dispatch.

    Built once per ``do_sync_rom_saves`` run and passed to
    ``_dispatch_sync_action`` and its sub-dispatchers, so the remaining
    per-call args stay to the values that actually vary per file (mirrors
    ``DispatchSink`` / ``SyncRunOptions``). ``stop`` is the run's own stop,
    where it can be stopped.
    """

    rom_id: int
    save_state: RomSaveSyncState
    device_id: LiveDeviceId
    rom_name: str
    save_names: tuple[str, ...]
    saves_dir: str
    system: str
    core_so: str | None
    stop: SyncStop | None = None


class MatrixExecutor:
    """Newest-wins matrix executor: decides each save file and dispatches the decision.

    Owns every code path that reads the server save list for a sync, runs
    ``compute_sync_action`` against per-filename inputs, and hands the
    resulting :class:`SyncAction` to the :class:`SaveTransfer` step that
    carries it out. The loaded :class:`RomSaveSyncState` aggregate is threaded
    in by the public rom-level orchestration use cases on :class:`SyncEngine`;
    it is mutated in memory only — the operation entry owns the single write
    Unit of Work.
    """

    def __init__(
        self,
        *,
        rom_info: RomInfoService,
        romm_api: RommSyncApi,
        retry: RetryStrategy,
        resolve_upload_conflict: ResolveUploadConflictFn,
        compute_sync_action: ComputeSyncActionFn,
        logger: logging.Logger,
        clock: Clock,
        save_file_store: SaveFileStore,
        log_debug: DebugLogger,
        transfer: SaveTransfer,
    ) -> None:
        self._rom_info = rom_info
        self._romm_api = romm_api
        self._retry = retry
        self._resolve_upload_conflict = resolve_upload_conflict
        self._compute_sync_action = compute_sync_action
        self._logger = logger
        self._clock = clock
        self._save_file_store = save_file_store
        self._log_debug = log_debug
        self._transfer = transfer

    def _handle_unexpected_error(
        self,
        e: Exception,
        filename: str,
        errors: list[str],
    ) -> None:
        """Handle an unexpected exception by recording an error."""
        _code, _msg = classify_error(e)
        errors.append(f"{filename}: {_msg}")

    def _build_local_input(
        self,
        local_path: str,
        filename: str,
        measure: SaveFileMeasure | None,
        mtime: float | None,
        size_fixed: bool | None,
    ) -> dict[str, Any]:
        """Build the dict shape consumed by ``compute_sync_action``.

        ``size_fixed`` is stated only where the save answer establishes it; left
        out, the core reads it as unknown.
        """
        local_input: dict[str, Any] = {
            "filename": filename,
            "path": local_path,
            "size": measure.size if measure is not None else None,
            "content_size": measure.content_size if measure is not None else None,
            "mtime": mtime,
        }
        if size_fixed is not None:
            local_input["size_fixed"] = size_fixed
        return local_input

    def build_sync_conflict_entry(
        self,
        rom_id: int,
        filename: str,
        server: dict[str, Any],
        local_path: str | None,
        local_hash: str | None,
    ) -> dict[str, Any]:
        """Build a Phase-2 ``sync_conflict`` descriptor for the frontend.

        Raises ``ValueError`` when *server* carries no ``id``: a resolution names
        the server save by the descriptor's ``server_save_id``, and a RomM save
        without an id is a contract violation, not a state to describe.
        """
        server_save_id = server.get("id")
        if server_save_id is None:
            raise ValueError("RomM returned a server save without an id")
        local_mtime = None
        local_size = None
        if local_path and self._save_file_store.is_file(local_path):
            local_mtime = datetime.fromtimestamp(self._save_file_store.get_mtime(local_path), tz=UTC).isoformat()
            local_size = self._save_file_store.get_size(local_path)
        return {
            "type": "sync_conflict",
            "rom_id": rom_id,
            "filename": filename,
            "server_save_id": server_save_id,
            "server_updated_at": server.get("updated_at", ""),
            "server_size": server.get("file_size_bytes"),
            "local_path": local_path,
            "local_hash": local_hash,
            "local_mtime": local_mtime,
            "local_size": local_size,
            "created_at": self._clock.now().isoformat(),
        }

    def _dispatch_skip(
        self,
        action: Skip,
        *,
        ctx: RomDispatchContext,
        filename: str,
        local_path: str | None,
        local_hash: str | None,
        default_slot: str | None,
    ) -> None:
        """Execute a ``Skip``: nothing, or an adopt of the head the local file is identical to.

        An adopt that must be acknowledged goes through
        :meth:`SaveTransfer.adopt_server_head`, any other through
        :meth:`SaveTransfer.record_server_head`; either records nothing for a
        file that no longer hashes to what the decision saw. A failed
        acknowledgement propagates to :meth:`_dispatch_sync_action`, which
        reports it as this file's error with nothing recorded, and the next sync
        decides again. No transfer either way, so no synced count.
        """
        head = action.server_save
        if not action.adopt_baseline or head is None or local_path is None or local_hash is None:
            self._log_debug(f"do_sync_rom_saves({ctx.rom_id}): skip {filename} ({action.reason})")
            return
        self._log_debug(
            f"do_sync_rom_saves({ctx.rom_id}): skip + adopt {filename} as save {head.get('id')}"
            f" (acknowledge={action.acknowledge})"
        )
        if action.acknowledge:
            self._transfer.adopt_server_head(
                ctx.save_state,
                filename,
                head,
                local_path,
                local_hash,
                device_id=ctx.device_id,
                system=ctx.system,
                default_slot=default_slot,
            )
        else:
            self._transfer.record_server_head(
                ctx.save_state, filename, head, local_path, local_hash, system=ctx.system, default_slot=default_slot
            )

    def _dispatch_upload(
        self,
        *,
        ctx: RomDispatchContext,
        filename: str,
        local_path: str | None,
        local_hash: str | None,
        last_sync_hash: str | None,
        last_sync_server_hash: str | None,
        server_candidates: list[dict[str, Any]],
        options: SyncRunOptions,
        sink: DispatchSink,
    ) -> TransferDirection | None:
        """Execute an ``Upload`` action by POSTing a new save, with a 409 backstop.

        Every automatic upload POSTs a new save in the slot (``server_save=None``,
        ``overwrite=False``); ``Upload.target_save_id`` no longer selects a PUT
        (ADR-0017). The POST is planned against a ``list_saves`` snapshot
        (*server_candidates*) that can be stale by the time it lands — another
        device may have moved the slot head past our last sync in between. Two
        signals route to the same backstop: RomM rejects the stale POST with a
        409, and RomM's content-dedup can early-return a pre-existing non-head
        save (:func:`domain.upload_response.dedup_returned_non_head`, #1482) that
        :meth:`SaveTransfer.upload_save_file` turns into a ``RommConflictError``. Either way :meth:`_handle_upload_409`
        re-fetches the slot and lets ``resolve_upload_conflict`` decide from
        hashes alone (download the fresh head when local is provably unchanged,
        else surface a conflict). Any other ``RommApiError`` propagates to the
        generic handler in :meth:`_dispatch_sync_action`. Returns the transfer
        direction that was issued (``UPLOAD`` on a clean POST, ``DOWNLOAD`` when
        the backstop downgrades), or ``None`` when nothing transferred.
        """
        if local_path is None:
            self._logger.warning(
                f"_dispatch_upload({ctx.rom_id}): {filename}: upload requested but no local file on disk"
            )
            sink.errors.append(f"{filename}: upload requested but no local file")
            return None
        try:
            # POST (create) a new save in the slot. The retention cap is POST-only —
            # RomM stacks versions on create, so the limit is sent here alone.
            # ``planned_group`` lets the POST catch a dedup-to-non-head response
            # (#1482) and route it through the same 409 backstop below.
            self._transfer.upload_save_file(
                ctx.rom_id,
                local_path,
                filename,
                ctx.save_state,
                ctx.device_id,
                ctx.system,
                ctx.core_so,
                None,
                options.default_slot,
                autocleanup_limit=options.autocleanup_limit,
                overwrite=False,
                planned_group=server_candidates,
            )
            return TransferDirection.UPLOAD
        except RommConflictError:
            return self._handle_upload_409(
                ctx=ctx,
                filename=filename,
                local_path=local_path,
                local_hash=local_hash,
                last_sync_hash=last_sync_hash,
                last_sync_server_hash=last_sync_server_hash,
                options=options,
                sink=sink,
            )

    def _handle_upload_409(
        self,
        *,
        ctx: RomDispatchContext,
        filename: str,
        local_path: str,
        local_hash: str | None,
        last_sync_hash: str | None,
        last_sync_server_hash: str | None,
        options: SyncRunOptions,
        sink: DispatchSink,
    ) -> TransferDirection | None:
        """Re-decide an upload the POST could not land as a new slot head.

        Reached from two signals that mean the same thing — the slot head is not
        where our POST assumed: RomM's write-time 409 (the head moved past our
        last sync since the ``list_saves`` snapshot that planned the POST), and a
        dedup-to-non-head early-return the POST turned into a ``RommConflictError``
        (#1482, RomM created no new version and an older save came back while a
        newer head still leads). Re-fetch the slot, regroup to
        this file's canonical target (the same grouping
        :meth:`iter_matrix_outcomes` uses), pick the newest, and let
        ``resolve_upload_conflict`` decide purely from hashes: a provably-unchanged
        local downloads the fresh head; anything else is a genuine two-sided
        divergence the user must resolve. An empty regroup (the head vanished
        again between the 409 and the re-fetch) is recorded as a non-fatal error.
        Returns ``DOWNLOAD`` when the backstop pulled the fresh head, else ``None``
        (empty regroup or surfaced conflict — nothing transferred).
        """
        server_saves = self._retry.with_retry(lambda: self._romm_api.list_saves(ctx.rom_id, device_id=ctx.device_id))
        server_in_slot = filter_saves_to_slot(server_saves, ctx.save_state.active_slot)
        group = [
            ss for ss in server_in_slot if local_save_target(ss, ctx.rom_name, known_names=ctx.save_names) == filename
        ]
        if not group:
            self._logger.warning(
                f"_handle_upload_409({ctx.rom_id}): {filename}: 409 on POST but no server save in slot on re-fetch"
            )
            sink.errors.append(f"{filename}: upload rejected by server and no server save found to reconcile")
            return None
        fresh = max(group, key=lambda s: parse_iso_to_epoch(s.get("updated_at")) or 0.0)
        if (
            self._resolve_upload_conflict(local_hash, last_sync_hash, fresh.get("content_hash"), last_sync_server_hash)
            == "download"
        ):
            self._transfer.download_save_file(
                fresh,
                ctx.saves_dir,
                filename,
                ctx.save_state,
                ctx.device_id,
                ctx.system,
                options.default_slot,
                stop=ctx.stop,
            )
            return TransferDirection.DOWNLOAD
        sink.conflicts.append(self.build_sync_conflict_entry(ctx.rom_id, filename, fresh, local_path, local_hash))
        return None

    def _dispatch_sync_action(
        self,
        action: object,
        *,
        ctx: RomDispatchContext,
        filename: str,
        local_path: str | None,
        local_hash: str | None,
        last_sync_hash: str | None,
        last_sync_server_hash: str | None,
        server_candidates: list[dict[str, Any]],
        options: SyncRunOptions,
        sink: DispatchSink,
    ) -> TransferDirection | None:
        """Execute one ``SyncAction`` outcome and report the transfer direction.

        Centralises the I/O dispatch so ``sync_rom_saves`` stays declarative.
        Errors are caught and pushed onto ``sink.errors`` so a single failure
        can't abort the whole rom-level sync; conflicts land on
        ``sink.conflicts``. ``ctx.rom_name`` + the stored baseline pair
        (``last_sync_hash`` / ``last_sync_server_hash``) + this file's
        ``server_candidates`` (the pre-upload snapshot, for the dedup-to-non-head
        guard) are threaded through for the upload 409 backstop (canonical-target
        regroup + hash re-decision). Returns the :class:`TransferDirection` a
        transfer moved, or ``None`` for a skip, a surfaced conflict, or a failed
        transfer. A download its stop ended raises :class:`SyncStopped`.
        """
        try:
            if isinstance(action, Skip):
                self._dispatch_skip(
                    action,
                    ctx=ctx,
                    filename=filename,
                    local_path=local_path,
                    local_hash=local_hash,
                    default_slot=options.default_slot,
                )
                return None
            if isinstance(action, Upload):
                return self._dispatch_upload(
                    ctx=ctx,
                    filename=filename,
                    local_path=local_path,
                    local_hash=local_hash,
                    last_sync_hash=last_sync_hash,
                    last_sync_server_hash=last_sync_server_hash,
                    server_candidates=server_candidates,
                    options=options,
                    sink=sink,
                )
            if isinstance(action, Download):
                self._transfer.download_save_file(
                    action.server_save,
                    ctx.saves_dir,
                    filename,
                    ctx.save_state,
                    ctx.device_id,
                    ctx.system,
                    options.default_slot,
                    stop=ctx.stop,
                )
                return TransferDirection.DOWNLOAD
            if isinstance(action, Conflict):
                sink.conflicts.append(
                    self.build_sync_conflict_entry(ctx.rom_id, filename, action.server_save, local_path, local_hash)
                )
                return None
        except SyncStopped:
            raise
        except RommApiError as e:
            _code, _msg = classify_error(e)
            self._logger.warning(f"_dispatch_sync_action({ctx.rom_id}): {filename} failed: {_msg}")
            sink.errors.append(f"{filename}: {_msg}")
        except Exception as e:
            self._logger.warning(f"_dispatch_sync_action({ctx.rom_id}): {filename} unexpected error: {e}")
            self._handle_unexpected_error(e, filename, sink.errors)
        return None

    def _dispatch_outcome(
        self,
        outcome: MatrixOutcome,
        *,
        ctx: RomDispatchContext,
        options: SyncRunOptions,
        sink: DispatchSink,
    ) -> TransferDirection | None:
        """Dispatch one file's outcome of a sync run, backfilling its content size first.

        Returns the :class:`TransferDirection` it moved, if any. Raises
        :class:`SyncStopped` when the run's stop ends it before or during this
        file.
        """
        if outcome.local_hash is not None and outcome.local_content_size is not None:
            ctx.save_state.backfill_content_size(
                outcome.filename, local_hash=outcome.local_hash, content_size=outcome.local_content_size
            )
        if ctx.stop is not None and ctx.stop.stops_here():
            self._log_debug(f"do_sync_rom_saves({ctx.rom_id}): stopped before {outcome.filename}")
            raise SyncStopped
        origin = "local" if outcome.local_path is not None else "server-only"
        self._log_debug(
            f"do_sync_rom_saves({ctx.rom_id}): {origin} {outcome.filename} -> {type(outcome.action).__name__}"
        )
        try:
            return self._dispatch_sync_action(
                outcome.action,
                ctx=ctx,
                filename=outcome.filename,
                local_path=outcome.local_path,
                local_hash=outcome.local_hash,
                last_sync_hash=outcome.file_state.last_sync_hash,
                last_sync_server_hash=outcome.file_state.last_sync_server_hash,
                server_candidates=outcome.server_candidates,
                options=options,
                sink=sink,
            )
        except SyncStopped:
            self._log_debug(f"do_sync_rom_saves({ctx.rom_id}): stopped before placing {outcome.filename}")
            raise

    def iter_matrix_outcomes(
        self,
        server_in_slot: list[dict[str, Any]],
        *,
        save_state: RomSaveSyncState | None,
        device_id: str | None,
        info: dict[str, Any],
        synced_files: tuple[SaveComponent, ...],
        saves_dir: str | None,
    ) -> Iterator[MatrixOutcome]:
        """Yield one :class:`MatrixOutcome` per save file in the ROM's active slot.

        *synced_files* and *saves_dir* are the save answer's, resolved once by
        the caller: reading them again here would put a second live reading of
        the machine on every ROM of a whole-library sweep. A ``None`` directory
        is the refusing answer's pairing — nothing local is probed for. A file's
        name and its size claim are both read off *synced_files*, so every
        caller decides a file by the same claim; the claim is the one stated in
        *saves_dir*, the directory every name is probed in
        (save-file-sync-architecture.md, "Inputs").

        Walks the local saves directory + server-only canonical targets,
        runs ``compute_sync_action`` against the per-filename inputs, and
        emits :class:`MatrixOutcome` records ready for sync dispatch or
        status rendering. Pure compute — no I/O writes, no state mutation.
        Consumers drive their own side effects from the yielded outcomes.
        """
        rom_name = info["rom_name"]

        files_state: dict[str, FileSyncState] = save_state.files if save_state else {}
        device_id_str = device_id or ""
        save_names = tuple(component.name for component in synced_files)
        size_claims = {c.name: c.size_fixed for c in synced_files if c.directory == saves_dir}

        local_files = self._rom_info.probe_save_files(list(save_names), saves_dir)

        handled_filenames: set[str] = set()
        for lf in local_files:
            filename = lf["filename"]
            handled_filenames.add(filename)
            # Group server saves to this file's own canonical target — symmetric
            # with the server-only loop below — so a multi-file save set never
            # cross-contaminates extensions (#1006). Without this, a sibling
            # extension's newer server record would win max(updated_at) and the
            # file would be evaluated/dispatched against the wrong save.
            group = [ss for ss in server_in_slot if local_save_target(ss, rom_name, known_names=save_names) == filename]
            yield self._local_outcome(
                filename,
                lf["path"],
                group,
                files_state.get(filename, FileSyncState()),
                device_id_str,
                size_claims.get(filename),
            )

        # Group server saves by canonical local target filename. Server-only
        # groups (no local file) get matrix-evaluated against their own group;
        # compute_sync_action picks newest-in-group internally.
        #
        # A target the answer does not name is passed over in BOTH directions.
        # Nothing local was probed for it — the probe walks the answer's names —
        # so without this it would look server-only, and the matrix would
        # download it over a local file it never looked at. Two shapes reach
        # here: a configuration file the rule says is never carried (a Saturn
        # ``.smpc`` Tender uploaded before the resolver decided the role),
        # and a save whose extension this emulator no longer writes at all. The
        # server copy is left exactly as it is; deleting it is not this cut's
        # business, and it is the only copy of something a user may want back.
        carried = frozenset(save_names)
        server_only_groups: dict[str, list[dict[str, Any]]] = {}
        for ss in server_in_slot:
            target = local_save_target(ss, rom_name, known_names=save_names)
            if target in handled_filenames:
                continue
            if target not in carried:
                self._log_debug(
                    f"iter_matrix_outcomes({rom_name!r}): server save {target!r} is not a file this emulator's "
                    f"answer carries — leaving both copies alone"
                )
                continue
            server_only_groups.setdefault(target, []).append(ss)

        for target_filename, group in server_only_groups.items():
            file_state = files_state.get(target_filename, FileSyncState())
            action = self._compute_sync_action(
                local_file=None,
                server_saves_in_slot=group,
                files_state=_file_state_to_dict(file_state),
                device_id=device_id_str,
                local_hash=None,
            )
            yield MatrixOutcome(
                filename=target_filename,
                action=action,
                local_path=None,
                local_hash=None,
                local_mtime_iso=None,
                local_size=None,
                local_content_size=None,
                file_state=file_state,
                server_candidates=group,
            )

    def _local_outcome(
        self,
        filename: str,
        local_path: str,
        group: list[dict[str, Any]],
        file_state: FileSyncState,
        device_id: str,
        size_fixed: bool | None,
    ) -> MatrixOutcome:
        """Measure the local file at *local_path* and decide it against its server *group*."""
        local_exists = self._save_file_store.is_file(local_path)
        # RomM-parity content hash (zip-aware) — the kernel compares this to
        # server.content_hash for its byte-identity checks, so a zip save
        # must be hashed per-entry, not as the whole archive (#1457).
        measure = self._save_file_store.measure(local_path) if local_exists else None
        local_hash = measure.content_hash if measure is not None else None
        local_mtime = self._save_file_store.get_mtime(local_path) if local_exists else None
        action = self._compute_sync_action(
            local_file=self._build_local_input(local_path, filename, measure, local_mtime, size_fixed),
            server_saves_in_slot=group,
            files_state=_file_state_to_dict(file_state),
            device_id=device_id,
            local_hash=local_hash,
        )
        return MatrixOutcome(
            filename=filename,
            action=action,
            local_path=local_path,
            local_hash=local_hash,
            local_mtime_iso=datetime.fromtimestamp(local_mtime, tz=UTC).isoformat()
            if local_mtime is not None
            else None,
            local_size=measure.size if measure is not None else None,
            local_content_size=measure.content_size if measure is not None else None,
            file_state=file_state,
            server_candidates=group,
        )

    def sync_rom_saves(
        self,
        rom_id: int,
        save_state: RomSaveSyncState,
        device_id: LiveDeviceId,
        core_so: str | None,
        default_slot: str | None = None,
        autocleanup_limit: int | None = None,
        save_answer: SaveAnswer | None = None,
        stop: SyncStop | None = None,
    ) -> tuple[int, int, list[str], list[dict[str, Any]]]:
        """Sync saves for a single ROM, mutating *save_state* in memory.

        *save_answer* is the live reading a caller already took for THIS sync —
        the entry points take one to decide whether to refuse at all, and
        handing it down is what keeps a single-ROM sync to one reading of the
        machine rather than one per layer. It is never carried between
        operations: absent it (the whole-library sweep), this takes its own, on
        top of the one that ROM's row already cost the sweep's device-wide
        negotiate inventory.

        Drives :meth:`iter_matrix_outcomes` and dispatches each emitted
        outcome through :meth:`_dispatch_sync_action`. Returns
        ``(uploaded_count, downloaded_count, errors_list, conflicts_list)`` — the
        two directional counts split out of the old aggregate transfer count so
        the completion toast can name which way saves moved (#250); a 409-backstop
        download counts as a download, not an upload. *core_so* is the active core
        resolved once by the caller (for the upload emulator tag); *default_slot*
        seeds the active slot when a brand-new ROM's first sync lands;
        *autocleanup_limit* caps server-retained versions on the POST (create)
        upload; the operation entry owns the surrounding read/write Unit of Work.

        A *stop* that a check of this run sees ends the run there: the counts
        are of what moved before it.
        """
        t_total = self._clock.time()
        rom_id = int(rom_id)

        # One live reading of the machine for the whole run: it says where the
        # save lives, the names group the server's saves onto their canonical
        # targets, AND they say which files to probe for. A whole-library sweep
        # already pays a second one for the device-wide negotiate inventory.
        info = self._rom_info.get_rom_save_info(rom_id, save_answer=save_answer)
        if not info:
            self._log_debug(f"do_sync_rom_saves({rom_id}): no save info, skipping")
            return 0, 0, [], []
        system = info["system"]
        answer = info["save_answer"]
        save_names = answer.synced_names
        answer_dir = answer.sync_directory
        # The backstop every sync path crosses. A refusing answer, and a save
        # beside the content, pair their names with a ``None`` directory, so
        # nothing is probed and no state is written — the sweep has no room in
        # its one result to say which ROM was passed over, and the per-ROM entry
        # points have already named the skip.
        if answer_dir is None:
            self._log_debug(f"do_sync_rom_saves({rom_id}): no per-game save set Tender syncs, skipping")
            return 0, 0, [], []
        saves_dir = answer_dir

        t0 = self._clock.time()
        try:
            server_saves = self._retry.with_retry(lambda: self._romm_api.list_saves(rom_id, device_id=device_id))
        except Exception as e:
            self._logger.error(f"do_sync_rom_saves({rom_id}): failed to list saves: {e}")
            _code, _msg = classify_error(e)
            return 0, 0, [f"Failed to fetch saves: {_msg}"], []
        self._log_debug(f"[TIMING] do_sync_rom_saves({rom_id}): list_saves {self._clock.time() - t0:.3f}s")

        active_slot = save_state.active_slot
        server_in_slot = filter_saves_to_slot(server_saves, active_slot)

        self._log_debug(
            f"do_sync_rom_saves({rom_id}): system={system}, rom_name={info['rom_name']}, "
            f"server_saves={len(server_saves)}, saves_dir={saves_dir}"
        )

        errors: list[str] = []
        conflicts: list[dict[str, Any]] = []
        sink = DispatchSink(errors=errors, conflicts=conflicts)
        options = SyncRunOptions(default_slot=default_slot, autocleanup_limit=autocleanup_limit)
        ctx = RomDispatchContext(
            rom_id=rom_id,
            save_state=save_state,
            device_id=device_id,
            rom_name=info["rom_name"],
            save_names=save_names,
            saves_dir=saves_dir,
            system=system,
            core_so=core_so,
            stop=stop,
        )
        uploaded = 0
        downloaded = 0

        for outcome in self.iter_matrix_outcomes(
            server_in_slot,
            save_state=save_state,
            device_id=device_id,
            info=info,
            synced_files=answer.synced_files,
            # The ANSWER's directory, which is None wherever the answer refuses,
            # so the pairing that makes a refusal probe nothing survives here too.
            saves_dir=answer_dir,
        ):
            try:
                direction = self._dispatch_outcome(outcome, ctx=ctx, options=options, sink=sink)
            except SyncStopped:
                break
            if direction is TransferDirection.UPLOAD:
                uploaded += 1
            elif direction is TransferDirection.DOWNLOAD:
                downloaded += 1

        # Record when this sync check ran (regardless of whether files transferred)
        save_state.mark_sync_evaluated(self._clock.now().isoformat())

        self._log_debug(
            f"[TIMING] do_sync_rom_saves({rom_id}): TOTAL {self._clock.time() - t_total:.3f}s"
            f" uploaded={uploaded} downloaded={downloaded} errors={len(errors)}"
        )
        return uploaded, downloaded, errors, conflicts


def _file_state_to_dict(file_state: FileSyncState) -> dict[str, Any]:
    """Project a :class:`FileSyncState` value object onto the dict shape
    ``compute_sync_action`` consumes (the legacy ``to_dict`` surface)."""
    return {
        "tracked_save_id": file_state.tracked_save_id,
        "last_sync_hash": file_state.last_sync_hash,
        "last_sync_server_hash": file_state.last_sync_server_hash,
        "last_sync_at": file_state.last_sync_at,
        "last_sync_server_updated_at": file_state.last_sync_server_updated_at,
        "last_sync_server_save_id": file_state.last_sync_server_save_id,
        "last_sync_server_size": file_state.last_sync_server_size,
        "last_sync_local_mtime": file_state.last_sync_local_mtime,
        "last_sync_local_size": file_state.last_sync_local_size,
        "last_sync_content_size": file_state.last_sync_content_size,
    }
