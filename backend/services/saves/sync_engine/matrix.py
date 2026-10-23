"""Newest-wins matrix executor and per-file sync I/O dispatch.

The decision layer for "which side wins for this file" plus the I/O
helpers that actually move bytes between the local saves directory and
the RomM server. Read-only matrix consumption (status reporting) lives
in StatusService; the loaded :class:`RomSaveSyncState` aggregate is threaded
in by the operation entry, which owns the Unit-of-Work read/write
bracketing this executor's in-memory mutations (ADR-0006). Rom-level
lock coordination and public use-case orchestration live on
:class:`services.saves.sync_engine.engine.SyncEngine`.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from domain.emulator_tag import build_emulator_tag
from domain.iso_time import parse_iso_to_epoch
from domain.rom_save_sync_state import FileSyncState, RomSaveSyncState
from domain.save_backup import BACKUP_DIR_NAME, backup_name, select_backups_to_prune
from domain.save_slot import filter_saves_to_slot
from domain.sync_action import Conflict, Download, Skip, SyncAction, Upload
from lib.errors import DeviceWriteError, RommApiError, RommConflictError, classify_error
from services.saves._helpers import local_save_target

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterator

    from domain.live_device_id import LiveDeviceId
    from domain.save_answer import SaveAnswer
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
    from services.saves.rom_info import RomInfoService


_BACKUP_RETENTION = 10  # max .romm-backup copies kept per save file (#974)


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
    ``DispatchSink`` / ``SyncRunOptions``).
    """

    rom_id: int
    save_state: RomSaveSyncState
    device_id: LiveDeviceId
    rom_name: str
    save_names: tuple[str, ...]
    saves_dir: str
    system: str
    core_so: str | None


class MatrixExecutor:
    """Newest-wins matrix executor + per-file sync I/O dispatch.

    Owns every code path that reads the server save list, runs
    ``compute_sync_action`` against per-filename inputs, and dispatches
    the resulting :class:`SyncAction` to disk / server I/O. The loaded
    :class:`RomSaveSyncState` aggregate is threaded in by the public rom-level
    orchestration use cases on :class:`SyncEngine`; this executor mutates
    it in memory via the aggregate's verb methods and never persists —
    the operation entry owns the single write Unit of Work.
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

    # ------------------------------------------------------------------
    # Server Save Hash Helper
    # ------------------------------------------------------------------

    def get_server_save_hash(self, server_save: dict[str, Any]) -> str | None:
        """Download a server save to temp and compute its RomM content hash.

        Used for slow-path conflict detection when the listed save carries no
        ``content_hash`` field. Hashes the downloaded copy with the zip-aware
        :meth:`SaveFileStore.content_hash` (never the whole-archive MD5) so the
        digest is comparable to a local ``content_hash`` for zip saves too —
        otherwise the keep_local adopt-without-upload check could never match a
        zip. Returns the hash string or None on non-retryable error. Raises on
        retryable errors so the caller can retry, and on a
        :class:`DeviceWriteError`, which is this device's to report rather than
        a hash the server could not give.
        """
        save_id = server_save.get("id")
        if not save_id:
            return None
        tmp_path: str | None = None
        try:
            tmp_path = self._save_file_store.make_temp_path(suffix=".tmp")
            self._romm_api.download_save(save_id, tmp_path)
            return self._save_file_store.content_hash(tmp_path)
        except Exception as e:
            self._log_debug(f"Failed to hash server save {save_id}: {e}")
            if isinstance(e, DeviceWriteError) or self._retry.is_retryable(e):
                raise
            return None
        finally:
            if tmp_path:
                with contextlib.suppress(OSError):
                    self._save_file_store.remove_file(tmp_path)

    def update_file_sync_state(
        self,
        save_state: RomSaveSyncState,
        filename: str,
        server_response: dict[str, Any],
        local_path: str,
        system: str,
        *,
        default_slot: str | None = None,
        emulator_tag: str | None = None,
        core_so: str | None = None,
    ) -> None:
        """Update per-file sync tracking on *save_state* after a successful sync op.

        Mutates the passed aggregate in memory via its verb methods; the
        operation entry owns the surrounding write Unit of Work. Seeds the ROM's
        state through :meth:`RomSaveSyncState.reach_sync_boundary`, measures the
        local file — its hash, size and content size from one open, and its
        mtime — and hands the measure to
        :meth:`RomSaveSyncState.record_transfer`, which composes the baseline; a
        response or file with nothing to record is logged.
        """
        save_state.reach_sync_boundary(system, default_slot, emulator_tag=emulator_tag, core=core_so)
        local_exists = self._save_file_store.is_file(local_path)
        # RomM-parity content hash (zip-aware) so a zip save's baseline is on the
        # same scheme the matrix compares against server.content_hash — never the
        # whole-archive MD5, which no server content_hash could match (#1457).
        measure = self._save_file_store.measure(local_path) if local_exists else None
        mtime = self._save_file_store.get_mtime(local_path) if local_exists else None
        if not save_state.record_transfer(
            filename, server_response, measure, mtime, synced_at=self._clock.now().isoformat()
        ):
            # A sync response missing the id, or a save file that vanished before
            # we could hash it, is genuinely untrackable — log and skip the
            # baseline rather than abort the whole rom-level sync.
            self._logger.warning(
                "update_file_sync_state: skipping untrackable baseline for %s (id=%r, has_hash=%s)",
                filename,
                server_response.get("id"),
                bool(measure and measure.content_hash),
            )

    def adopt_head_as_baseline(
        self,
        save_state: RomSaveSyncState,
        filename: str,
        head: dict[str, Any],
        local_path: str,
        local_hash: str,
        *,
        system: str,
        default_slot: str | None = None,
    ) -> bool:
        """Record the local file, identical to *head*, as *filename*'s baseline for that head.

        The adopt a ``Skip(adopt_baseline=True)`` asks for, with no transfer:
        the baseline is :meth:`RomSaveSyncState.adopt_head`'s, and as the adopt
        stands in for a download of the same bytes, *save_state* is seeded
        through :meth:`RomSaveSyncState.reach_sync_boundary` as a download seeds
        it, except that no core is recorded and the emulator tag stays the one
        already recorded. *local_hash* is the hash
        the decision was made on; when the file no longer hashes to it, nothing
        is recorded and ``False`` is returned, so a file written since the
        decision is never recorded as the head's bytes. Mutates *save_state* in
        memory only.
        """
        measured = self._measure_unchanged(filename, local_path, local_hash)
        if measured is None:
            return False
        self._record_adopt(save_state, filename, head, measured, system=system, default_slot=default_slot)
        return True

    def _measure_unchanged(
        self, filename: str, local_path: str, local_hash: str
    ) -> tuple[SaveFileMeasure, float] | None:
        """Measure *local_path*, or ``None`` when it no longer hashes to *local_hash*, the decision's hash."""
        measure = self._save_file_store.measure(local_path)
        if measure.content_hash != local_hash:
            self._log_debug(f"adopt {filename}: the file changed since the decision, nothing recorded")
            return None
        return measure, self._save_file_store.get_mtime(local_path)

    def _record_adopt(
        self,
        save_state: RomSaveSyncState,
        filename: str,
        head: dict[str, Any],
        measured: tuple[SaveFileMeasure, float],
        *,
        system: str,
        default_slot: str | None,
    ) -> None:
        """Seed *save_state* at the boundary (no core; the recorded emulator tag) and record *head*.

        *head* becomes *filename*'s baseline, composed from *measured*.
        """
        save_state.reach_sync_boundary(system, default_slot)
        save_state.adopt_head(filename, head, *measured, synced_at=self._clock.now().isoformat())

    # ------------------------------------------------------------------
    # Sync Helpers
    # ------------------------------------------------------------------

    def do_download_save(
        self,
        server_save: dict[str, Any],
        saves_dir: str,
        filename: str,
        save_state: RomSaveSyncState,
        device_id: LiveDeviceId,
        system: str,
        default_slot: str | None = None,
    ) -> None:
        """Download a save file from server. Backs up existing local file first.

        The content lands in ``<filename>.tmp`` and replaces the local file only
        once complete; any failure up to that replacement tries to remove the
        ``.tmp`` (a removal that fails is not reported) and raises the
        download's own failure. Mutates *save_state* in memory (per-file baseline); the
        operation entry owns the surrounding write Unit of Work.
        """
        local_path = os.path.join(saves_dir, filename)
        self._save_file_store.make_dirs(saves_dir)
        tmp_path = local_path + ".tmp"

        try:
            self._retry.with_retry(
                lambda: self._romm_api.download_save_content(
                    server_save["id"],
                    tmp_path,
                    device_id=device_id,
                    optimistic=True,
                ),
            )

            # Back up the existing local save before the download overwrites it.
            self.quarantine_local_file(saves_dir, filename)

            self._save_file_store.rename(tmp_path, local_path)
        except Exception:
            with contextlib.suppress(OSError):
                self._save_file_store.remove_file(tmp_path)
            raise
        self.update_file_sync_state(save_state, filename, server_save, local_path, system, default_slot=default_slot)
        self._log_debug(f"Downloaded save: {filename}")

    def quarantine_local_file(self, saves_dir: str, filename: str) -> bool:
        """Move a local save file aside into ``.romm-backup`` before it is destroyed.

        The single source of truth for the save-file backup discipline: both the
        download-overwrite path (:meth:`do_download_save`) and the slot-switch
        removal path route through here, so no local save is ever destroyed
        without a recoverable copy (#965). The backup lands at
        ``<saves_dir>/.romm-backup/<name>_<ts>[_<n>]<ext>`` (``<ts>`` from the
        injected clock). A same-second collision appends a ``_<n>`` counter so a
        multi-file slot quarantined within one second never overwrites an earlier
        backup, and the newest ``_BACKUP_RETENTION`` backups per save file are
        kept — older ones are pruned to bound the recovery net's disk use (#974).
        The backup written by this call is never pruned in its own call, so under
        sustained same-second churn the folder may briefly hold one extra copy
        (``_BACKUP_RETENTION + 1``) — destroying the just-saved file to honour the
        cap would defeat the backup. Returns ``True`` when a file was moved,
        ``False`` when there was nothing at *filename* to back up.
        """
        local_path = os.path.join(saves_dir, filename)
        if not self._save_file_store.is_file(local_path):
            return False
        backup_dir = os.path.join(saves_dir, BACKUP_DIR_NAME)
        if self._save_file_store.is_symlink(backup_dir) or not self._save_file_store.is_within(backup_dir, saves_dir):
            raise ValueError(f"Unsafe save backup directory: {backup_dir}")
        self._save_file_store.make_dirs(backup_dir)
        ts = self._clock.now().strftime("%Y%m%d_%H%M%S")
        existing = set(self._save_file_store.listdir(backup_dir))
        backup = backup_name(filename, ts, existing)
        self._save_file_store.rename(local_path, os.path.join(backup_dir, backup))
        # Bound the recovery net: keep only the newest N backups per save file (#974).
        existing.add(backup)
        for stale in select_backups_to_prune(filename, list(existing), _BACKUP_RETENTION):
            if stale == backup:
                continue  # never prune the backup just created this call (#974 — would destroy the save)
            self._save_file_store.remove_file(os.path.join(backup_dir, stale))
        return True

    @staticmethod
    def _resolve_upload_slot(save_state: RomSaveSyncState, default_slot: str | None = None) -> str | None:
        """The slot field to send with an upload; ``None`` only for an explicit-legacy save.

        An upload always carries a live ``device_id`` (``do_upload_save`` takes
        a :class:`LiveDeviceId`), so the slot resolves purely from the
        aggregate's slot state. A named
        ``active_slot`` uploads to that slot; an ``active_slot`` of ``None`` is
        ambiguous and is disambiguated by the ``slots`` map (the same signal
        :meth:`update_file_sync_state` uses to seed the active slot):

        - **Explicit legacy** (``active_slot`` None but ``slots`` populated — the
          state after switching to / confirming the legacy slot) → ``None`` so the
          save is POSTed as ``slot:null``. Returning ``"default"`` here misfiled a
          legacy save into the default slot (#1061).
        - **Brand-new ROM** (``active_slot`` None and ``slots`` empty — never
          configured) → the configured ``default_slot`` so its first sync lands in
          the default slot, matching the active-slot seeding.
        """
        if save_state.active_slot is not None:
            return save_state.active_slot
        if save_state.slots:
            return None
        return default_slot or "autosave"

    def _confirm_upload_sync(self, response: dict[str, Any], device_id: LiveDeviceId) -> None:
        """Ack the uploaded save on the server's DeviceSaveSync row, unless redundant.

        Skipped when *response* already shows this device ``is_current`` (#1458);
        otherwise sent. When RomM's answers make the ack redundant and when it is
        the only writer of the row: docs/architecture/save-file-sync-architecture.md,
        "POST and PUT both upsert the calling device's sync row". Fail-open — any
        non-current / missing / unexpected response confirms. Non-fatal: a failed
        ack is debug-logged and swallowed.
        """
        upload_id = response.get("id")
        if not upload_id:
            return
        if _upload_response_proves_current(response, device_id):
            self._log_debug(f"confirm_download after upload skipped for save {upload_id} (already current)")
            return
        try:
            self._romm_api.confirm_download(upload_id, device_id)
        except Exception:
            self._log_debug(f"confirm_download after upload failed for save {upload_id} (non-fatal)")

    def do_upload_save(
        self,
        rom_id: int,
        file_path: str,
        filename: str,
        save_state: RomSaveSyncState,
        device_id: LiveDeviceId,
        system: str,
        core_so: str | None,
        server_save: dict[str, Any] | None = None,
        default_slot: str | None = None,
        autocleanup_limit: int | None = None,
        overwrite: bool = False,
        planned_group: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Upload a local save file to server.

        Mutates *save_state* in memory (per-file baseline, own-upload
        attribution, local→server slot promotion); the operation entry owns
        the surrounding write Unit of Work. *core_so* is the active core
        resolved once by the caller so this worker stays free of installed-rom
        reads. *autocleanup_limit* caps server-retained versions — the adapter
        honors it on the POST (create) path only, so PUT callers leave it None.
        *overwrite* forces RomM's ``add_save`` to skip its write-time currency
        (409) gate — used only when the caller has already decided our content
        must win (the ``keep_local`` conflict resolution); the automatic sync
        dispatch leaves it ``False`` so the 409 backstop can catch a stale-current
        race (ADR-0017).

        *planned_group* is the automatic POST path's pre-upload ``list_saves``
        snapshot for this file; every other caller passes ``None``, and the guard
        is inert for it. When the POST response deduped to a pre-existing
        non-head save (:func:`_dedup_returned_non_head`, #1482), this raises
        ``RommConflictError`` BEFORE any baseline / own-upload / confirm write so
        the same 409 backstop that catches a write-time stale-current race
        surfaces the true state — no false "synced", no currency stamped on the
        non-head response.
        """
        save_id = server_save.get("id") if server_save else None
        emulator = build_emulator_tag(core_so)

        slot = self._resolve_upload_slot(save_state, default_slot)

        result = self._retry.with_retry(
            lambda: self._romm_api.upload_save(
                int(rom_id),
                file_path,
                emulator,
                save_id,
                device_id=device_id,
                slot=slot,
                overwrite=overwrite,
                autocleanup_limit=autocleanup_limit,
            )
        )

        if planned_group is not None and _dedup_returned_non_head(result, planned_group):
            # Dedup to a non-head save: RomM created no new version and the
            # foreign head still leads the slot, so recording this response as a
            # synced baseline would falsely report the upload landed (#1482).
            # Route it through the write-time 409 backstop — the raise reaches
            # ``_dispatch_upload``'s ``except RommConflictError`` before any
            # baseline / confirm / own-upload write runs, so nothing stamps
            # currency on the non-head response.
            raise RommConflictError(
                "add_save deduped to a non-head save; the slot head is newer",
                method="POST",
            )

        self.update_file_sync_state(
            save_state,
            filename,
            result,
            file_path,
            system,
            default_slot=default_slot,
            emulator_tag=emulator,
            core_so=core_so,
        )

        new_id = result.get("id")
        if new_id is not None:
            save_state.track_own_upload(new_id)

        if slot:
            save_state.promote_slot_to_server(slot)

        self._confirm_upload_sync(result, device_id)

        self._log_debug(f"Uploaded save: {filename} (emulator={emulator})")
        return result

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
        self, local_path: str, filename: str, measure: SaveFileMeasure | None, mtime: float | None
    ) -> dict[str, Any]:
        """Build the dict shape consumed by ``compute_sync_action``.

        ``size_fixed`` is left out, which the core reads as unknown: nothing
        Tender reads states whether a save's size is fixed.
        """
        return {
            "filename": filename,
            "path": local_path,
            "size": measure.size if measure is not None else None,
            "content_size": measure.content_size if measure is not None else None,
            "mtime": mtime,
        }

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

        The file is measured first: one that no longer hashes to what the
        decision saw is neither acknowledged nor recorded. An adopt that must be
        acknowledged then confirms the head to RomM, through the retry strategy,
        and records the baseline from that measure only once that succeeded; a
        failure propagates to :meth:`_dispatch_sync_action`, which reports it as
        this file's error with nothing recorded, and the next sync decides
        again. No transfer either way, so no synced count.
        """
        head = action.server_save
        if not action.adopt_baseline or head is None or local_path is None or local_hash is None:
            self._log_debug(f"do_sync_rom_saves({ctx.rom_id}): skip {filename} ({action.reason})")
            return
        self._log_debug(
            f"do_sync_rom_saves({ctx.rom_id}): skip + adopt {filename} as save {head.get('id')}"
            f" (acknowledge={action.acknowledge})"
        )
        measured = self._measure_unchanged(filename, local_path, local_hash)
        if measured is None:
            return
        if action.acknowledge:
            self._retry.with_retry(lambda: self._romm_api.confirm_download(int(head["id"]), ctx.device_id))
        self._record_adopt(ctx.save_state, filename, head, measured, system=ctx.system, default_slot=default_slot)

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
        save (:func:`_dedup_returned_non_head`, #1482) that ``do_upload_save``
        turns into a ``RommConflictError``. Either way :meth:`_handle_upload_409`
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
            self.do_upload_save(
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
            self.do_download_save(
                fresh, ctx.saves_dir, filename, ctx.save_state, ctx.device_id, ctx.system, options.default_slot
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
        transfer.
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
                self.do_download_save(
                    action.server_save,
                    ctx.saves_dir,
                    filename,
                    ctx.save_state,
                    ctx.device_id,
                    ctx.system,
                    options.default_slot,
                )
                return TransferDirection.DOWNLOAD
            if isinstance(action, Conflict):
                sink.conflicts.append(
                    self.build_sync_conflict_entry(ctx.rom_id, filename, action.server_save, local_path, local_hash)
                )
                return None
        except RommApiError as e:
            _code, _msg = classify_error(e)
            self._logger.warning(f"_dispatch_sync_action({ctx.rom_id}): {filename} failed: {_msg}")
            sink.errors.append(f"{filename}: {_msg}")
        except Exception as e:
            self._logger.warning(f"_dispatch_sync_action({ctx.rom_id}): {filename} unexpected error: {e}")
            self._handle_unexpected_error(e, filename, sink.errors)
        return None

    def iter_matrix_outcomes(
        self,
        server_in_slot: list[dict[str, Any]],
        *,
        save_state: RomSaveSyncState | None,
        device_id: str | None,
        info: dict[str, Any],
        save_names: tuple[str, ...],
        saves_dir: str | None,
    ) -> Iterator[MatrixOutcome]:
        """Yield one :class:`MatrixOutcome` per save file in the ROM's active slot.

        *save_names* and *saves_dir* are the save answer's, resolved once by the
        caller: reading them again here would put a second live reading of the
        machine on every ROM of a whole-library sweep. A ``None`` directory is
        the refusing answer's pairing — nothing local is probed for.

        Walks the local saves directory + server-only canonical targets,
        runs ``compute_sync_action`` against the per-filename inputs, and
        emits :class:`MatrixOutcome` records ready for sync dispatch or
        status rendering. Pure compute — no I/O writes, no state mutation.
        Consumers drive their own side effects from the yielded outcomes.
        """
        rom_name = info["rom_name"]

        files_state: dict[str, FileSyncState] = save_state.files if save_state else {}
        device_id_str = device_id or ""

        local_files = self._rom_info.probe_save_files(list(save_names), saves_dir)

        handled_filenames: set[str] = set()
        for lf in local_files:
            filename = lf["filename"]
            local_path = lf["path"]
            handled_filenames.add(filename)
            local_exists = self._save_file_store.is_file(local_path)
            # RomM-parity content hash (zip-aware) — the kernel compares this to
            # server.content_hash for its byte-identity checks, so a zip save
            # must be hashed per-entry, not as the whole archive (#1457).
            measure = self._save_file_store.measure(local_path) if local_exists else None
            local_hash = measure.content_hash if measure is not None else None
            file_state = files_state.get(filename, FileSyncState())
            local_mtime = self._save_file_store.get_mtime(local_path) if local_exists else None
            local_mtime_iso = (
                datetime.fromtimestamp(local_mtime, tz=UTC).isoformat() if local_mtime is not None else None
            )
            # Group server saves to this file's own canonical target — symmetric
            # with the server-only loop below — so a multi-file save set never
            # cross-contaminates extensions (#1006). Without this, a sibling
            # extension's newer server record would win max(updated_at) and the
            # file would be evaluated/dispatched against the wrong save.
            group = [ss for ss in server_in_slot if local_save_target(ss, rom_name, known_names=save_names) == filename]
            action = self._compute_sync_action(
                local_file=self._build_local_input(local_path, filename, measure, local_mtime),
                server_saves_in_slot=group,
                files_state=_file_state_to_dict(file_state),
                device_id=device_id_str,
                local_hash=local_hash,
            )
            yield MatrixOutcome(
                filename=filename,
                action=action,
                local_path=local_path,
                local_hash=local_hash,
                local_mtime_iso=local_mtime_iso,
                local_size=measure.size if measure is not None else None,
                local_content_size=measure.content_size if measure is not None else None,
                file_state=file_state,
                server_candidates=group,
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

    def sync_rom_saves(
        self,
        rom_id: int,
        save_state: RomSaveSyncState,
        device_id: LiveDeviceId,
        core_so: str | None,
        default_slot: str | None = None,
        autocleanup_limit: int | None = None,
        save_answer: SaveAnswer | None = None,
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
        )
        uploaded = 0
        downloaded = 0

        for outcome in self.iter_matrix_outcomes(
            server_in_slot,
            save_state=save_state,
            device_id=device_id,
            info=info,
            save_names=save_names,
            # The ANSWER's directory, which is None wherever the answer refuses,
            # so the pairing that makes a refusal probe nothing survives here too.
            saves_dir=answer_dir,
        ):
            if outcome.local_hash is not None and outcome.local_content_size is not None:
                save_state.backfill_content_size(
                    outcome.filename, local_hash=outcome.local_hash, content_size=outcome.local_content_size
                )
            origin = "local" if outcome.local_path is not None else "server-only"
            self._log_debug(
                f"do_sync_rom_saves({rom_id}): {origin} {outcome.filename} -> {type(outcome.action).__name__}"
            )
            direction = self._dispatch_sync_action(
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


def _dedup_returned_non_head(response: dict[str, Any], planned_group: list[dict[str, Any]]) -> bool:
    """Whether an ``add_save`` POST deduped to a pre-existing NON-head save (#1482).

    RomM's ``add_save`` content-dedup can early-return an EXISTING save whose
    content matches the upload instead of creating a new version. When that
    returned save is not the slot head the run planned against — an older save
    while a newer, different head still leads the slot — the upload intent (make
    our content the slot head) went silently unfulfilled: no new version, the
    foreign head stays authoritative, yet the response looks like a successful
    upload. Recording it as a synced baseline would falsely report "synced".

    Detected purely from ids against *planned_group* (the pre-upload
    ``list_saves`` snapshot for this file, the same group ``compute_sync_action``
    evaluated): the response is a member of that snapshot (an existing save, not
    a freshly minted version whose id is absent from it) that is not the newest
    save in it. Benign — today's baseline write stands — for an empty planned
    slot (no head to bypass), a freshly created version, or the head coming back.
    """
    response_id = response.get("id")
    if response_id is None or not planned_group:
        return False
    head = max(planned_group, key=lambda s: parse_iso_to_epoch(s.get("updated_at")) or 0.0)
    group_ids = {s.get("id") for s in planned_group}
    return response_id in group_ids and response_id != head.get("id")


def _upload_response_proves_current(response: dict[str, Any], device_id: str) -> bool:
    """Whether *response* proves *device_id* already holds the current save.

    Reads the same ``device_syncs`` shape ``domain.sync_action`` reads. Fail-open:
    only an entry for *device_id* whose ``is_current`` is exactly ``True`` counts;
    any missing / non-list / otherwise-unexpected shape returns ``False`` so the
    caller still confirms.
    """
    device_syncs = response.get("device_syncs")
    if not isinstance(device_syncs, list):
        return False
    return any(
        isinstance(ds, dict) and ds.get("device_id") == device_id and ds.get("is_current") is True
        for ds in device_syncs
    )


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
