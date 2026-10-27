"""The save transfer: moving one save file between this device and RomM, and recording what moved.

Every step that downloads a server save, uploads a local one, acknowledges a
head to RomM or fetches a server copy lives here, and so does the record of
the sync boundary a transfer or an adopt reaches: the ``RomSaveSyncState``
verbs are called in the same step that moved the file, so no caller can
transfer without recording. Deciding WHICH transfer a file needs is the sync
matrix's, not this module's.

The steps run synchronously, under a ``rom_lock`` and outside any Unit of
Work the caller holds; each mutates the aggregate it is handed in memory and
never persists it.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.emulator_tag import build_emulator_tag
from domain.save_backup import BACKUP_RETENTION
from domain.upload_response import dedup_returned_non_head, upload_response_proves_current
from lib.errors import DeviceWriteError, RommConflictError
from services.saves._sync_stop import SyncStopped

if TYPE_CHECKING:
    import logging

    from domain.live_device_id import LiveDeviceId
    from domain.rom_save_sync_state import RomSaveSyncState
    from domain.save_measure import SaveFileMeasure
    from services.protocols import Clock, DebugLogger, RetryStrategy, RommSaveApi, SaveFileStore
    from services.saves._sync_stop import SyncStop
    from services.saves.save_backup import SaveBackup


@dataclass(frozen=True)
class SaveTransferConfig:
    """Frozen wiring bundle handed to ``SaveTransfer.__init__``.

    Holds the Protocol-typed RomM adapter and the retry strategy its requests
    run under, the save-file store, the clock baselines are stamped by, the
    :class:`SaveBackup` a download backs the local file up through, the
    standard-library logger and the ``DebugLogger`` seam.
    """

    romm_api: RommSaveApi
    retry: RetryStrategy
    save_file_store: SaveFileStore
    clock: Clock
    backup: SaveBackup
    logger: logging.Logger
    log_debug: DebugLogger


class SaveTransfer:
    """Moves one save file between this device and RomM and records the sync boundary it reaches.

    The device id a step names to RomM is a :class:`LiveDeviceId` its caller
    took from ``SyncEngine.live_device_id``; this class holds none. It takes no
    lock: the caller holds the ROM's ``rom_lock``.
    """

    def __init__(self, *, config: SaveTransferConfig) -> None:
        self._romm_api = config.romm_api
        self._retry = config.retry
        self._save_file_store = config.save_file_store
        self._clock = config.clock
        self._backup = config.backup
        self._logger = config.logger
        self._log_debug = config.log_debug

    # ------------------------------------------------------------------
    # Server copies
    # ------------------------------------------------------------------

    def fetch_server_copy(self, save_id: int, tmp_path: str) -> str:
        """Fetch server save *save_id* into *tmp_path* and answer its RomM content hash.

        Names no device to RomM and records no baseline: the copy is something
        to compare or to place, not a download this device is now in step
        with. The request runs under the retry strategy. The hash is the
        zip-aware :meth:`SaveFileStore.content_hash` (never the whole-archive
        MD5), so it is comparable to a local file's. Any failure removes
        *tmp_path* (a removal that fails is not reported) and raises the
        fetch's own failure; on success the file is the caller's to place or
        remove.
        """
        try:
            self._retry.with_retry(lambda: self._romm_api.download_save(save_id, tmp_path))
            return self._save_file_store.content_hash(tmp_path)
        except Exception:
            with contextlib.suppress(OSError):
                self._save_file_store.remove_file(tmp_path)
            raise

    def hash_server_copy(self, server_save: dict[str, Any]) -> str | None:
        """Fetch *server_save* into a temporary file, hash it and remove the file — Keep Local's identity check.

        Returns the hash, or ``None`` for a save without an id or a fetch that
        failed in a way no retry would mend. Raises a failure the retry strategy
        counts as retryable, so the retry around the call can try again, and a
        :class:`DeviceWriteError`, which is this device's to report rather than a
        hash the server could not give.
        """
        save_id = server_save.get("id")
        if not save_id:
            return None
        tmp_path = self._save_file_store.make_temp_path(suffix=".tmp")
        try:
            return self.fetch_server_copy(save_id, tmp_path)
        except Exception as e:
            self._log_debug(f"Failed to hash server save {save_id}: {e}")
            if isinstance(e, DeviceWriteError) or self._retry.is_retryable(e):
                raise
            return None
        finally:
            with contextlib.suppress(OSError):
                self._save_file_store.remove_file(tmp_path)

    # ------------------------------------------------------------------
    # Download and upload
    # ------------------------------------------------------------------

    def download_save_file(
        self,
        server_save: dict[str, Any],
        saves_dir: str,
        filename: str,
        save_state: RomSaveSyncState,
        device_id: LiveDeviceId,
        system: str,
        default_slot: str | None = None,
        *,
        stop: SyncStop | None = None,
    ) -> None:
        """Download *server_save* onto *filename* in *saves_dir*, backing up the file it replaces.

        The content lands in ``<filename>.tmp`` and replaces the local file only
        once complete, after :class:`SaveBackup` has moved the old one aside;
        any failure up to that replacement tries to remove the ``.tmp`` (a
        removal that fails is not reported) and raises the download's own
        failure. A *stop* asked by the time the content is complete is such a
        failure: :class:`SyncStopped` is raised, nothing is moved and nothing
        recorded. Then records the sync boundary and *filename*'s baseline on
        *save_state*, in memory; the caller owns the write Unit of Work.
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
            if stop is not None and stop.stops_here():
                raise SyncStopped
            self._backup.back_up_save_file(saves_dir, filename, keep=BACKUP_RETENTION)
            self._save_file_store.rename(tmp_path, local_path)
        except Exception:
            with contextlib.suppress(OSError):
                self._save_file_store.remove_file(tmp_path)
            raise
        self._record_transferred_file(save_state, filename, server_save, local_path, system, default_slot=default_slot)
        self._log_debug(f"Downloaded save: {filename}")

    def upload_save_file(
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
        """Upload *file_path* to RomM and record it as *filename*'s baseline.

        Mutates *save_state* in memory (per-file baseline, own-upload
        attribution, local→server slot promotion); the caller owns the write
        Unit of Work. *core_so* is the active core the caller resolved, for the
        emulator tag. *server_save* names the save to PUT; ``None`` POSTs a new
        one. *autocleanup_limit* caps server-retained versions — the adapter
        honors it on the POST (create) path only, so PUT callers leave it None.
        *overwrite* forces RomM's ``add_save`` to skip its write-time currency
        (409) gate — used only when the caller has already decided our content
        must win (the ``keep_local`` conflict resolution); the automatic sync
        dispatch leaves it ``False`` so the 409 backstop can catch a
        stale-current race (ADR-0017).

        *planned_group* is the automatic POST path's pre-upload ``list_saves``
        snapshot for this file; every other caller passes ``None``, and the guard
        is inert for it. When the POST response deduped to a pre-existing
        non-head save (:func:`domain.upload_response.dedup_returned_non_head`),
        this raises ``RommConflictError`` BEFORE any baseline / own-upload /
        acknowledgement write so the same 409 backstop that catches a write-time
        stale-current race surfaces the true state — no false "synced", no
        currency stamped on the non-head response. The upload's own
        acknowledgement follows the record (:meth:`_acknowledge_upload`).
        """
        save_id = server_save.get("id") if server_save else None
        emulator = build_emulator_tag(core_so)
        slot = save_state.upload_slot(default_slot)

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

        if planned_group is not None and dedup_returned_non_head(result, planned_group):
            # RomM created no new version and the foreign head still leads the
            # slot. The raise reaches the sync dispatch's 409 backstop before any
            # baseline, acknowledgement or own-upload write stamps currency on
            # the non-head response.
            raise RommConflictError(
                "add_save deduped to a non-head save; the slot head is newer",
                method="POST",
            )

        self._record_transferred_file(
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

        self._acknowledge_upload(result, device_id)

        self._log_debug(f"Uploaded save: {filename} (emulator={emulator})")
        return result

    def _acknowledge_upload(self, response: dict[str, Any], device_id: LiveDeviceId) -> None:
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
        if upload_response_proves_current(response, device_id):
            self._log_debug(f"confirm_download after upload skipped for save {upload_id} (already current)")
            return
        try:
            self._romm_api.confirm_download(upload_id, device_id)
        except Exception:
            self._log_debug(f"confirm_download after upload failed for save {upload_id} (non-fatal)")

    # ------------------------------------------------------------------
    # A head adopted without a transfer
    # ------------------------------------------------------------------

    def acknowledge_server_head(self, head: dict[str, Any], device_id: LiveDeviceId) -> None:
        """Confirm *head* to RomM as synced by this device, through the retry strategy.

        For a head adopted without a transfer, where nothing else tells RomM
        this device is current on it. A failure propagates once the retries are
        spent.
        """
        self._retry.with_retry(lambda: self._romm_api.confirm_download(int(head["id"]), device_id))

    def adopt_server_head(
        self,
        save_state: RomSaveSyncState,
        filename: str,
        head: dict[str, Any],
        local_path: str,
        local_hash: str,
        *,
        device_id: LiveDeviceId,
        system: str,
        default_slot: str | None = None,
    ) -> bool:
        """Acknowledge *head* to RomM, then record the local file, identical to it, as *filename*'s baseline.

        The file is measured first: one that no longer hashes to *local_hash*,
        the hash the identity was decided on, is neither acknowledged nor
        recorded, and ``False`` is returned. Otherwise :meth:`acknowledge_server_head`
        runs and the record follows only once it succeeded; a failure propagates
        with nothing recorded. The record is :meth:`record_server_head`'s.
        Mutates *save_state* in memory only.
        """
        measured = self._measure_unchanged(filename, local_path, local_hash)
        if measured is None:
            return False
        self.acknowledge_server_head(head, device_id)
        self._record_adopt(save_state, filename, head, measured, system=system, default_slot=default_slot)
        return True

    def record_server_head(
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
        """Record the local file, identical to *head*, as *filename*'s baseline for that head, telling RomM nothing.

        For an adopt that needs no acknowledgement. The baseline is
        :meth:`RomSaveSyncState.adopt_head`'s, and as the adopt stands in for a
        download of the same bytes, *save_state* is seeded through
        :meth:`RomSaveSyncState.reach_sync_boundary` as a download seeds it: no
        core is recorded — an adopt transfers nothing, so which core wrote the
        bytes is unknown — and the emulator tag is the one already recorded, else
        ``retroarch``. When the file no longer hashes to *local_hash*, nothing is
        recorded and ``False`` is returned, so a file written since the decision
        is never recorded as the head's bytes. Mutates *save_state* in memory only.
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
    # The boundary a transfer reaches
    # ------------------------------------------------------------------

    def _record_transferred_file(
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
        """Record on *save_state* the sync boundary a download or an upload of *filename* reached.

        Seeds the ROM's state through :meth:`RomSaveSyncState.reach_sync_boundary`,
        measures the local file — its hash, size and content size from one open,
        and its mtime — and hands the measure to
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
                "Recording a transfer: skipping untrackable baseline for %s (id=%r, has_hash=%s)",
                filename,
                server_response.get("id"),
                bool(measure and measure.content_hash),
            )
