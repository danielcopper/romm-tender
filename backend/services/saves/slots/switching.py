"""The destructive slot-switch flow.

``switch_slot`` synchronises the local saves directory to the new slot's
contents. Slot listing, the setup wizard, and slot deletion belong in their
own sub-modules. Persistence is each operation's own narrow Unit of Work
(ADR-0006).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.save_backup import BACKUP_RETENTION
from domain.save_slot import save_in_slot
from lib.errors import NamedRefused, NotInstalled, Refused
from lib.partial_failure import PartialFailure
from services.saves._helpers import newest_server_saves_by_target
from services.saves._local_changes import changed_since_last_sync
from services.saves._messages import SAVE_SYNC_DISABLED, SAVE_SYNC_IN_CONTENT_DIR
from services.saves._refusals import SavefilesInContentDir, shape_refusal
from services.saves._save_state import read_save_state_or_new, write_save_state
from services.saves._settings import resolve_default_slot, save_sync_enabled

if TYPE_CHECKING:
    import asyncio

    from domain.live_device_id import LiveDeviceId
    from domain.rom_save_sync_state import RomSaveSyncState
    from domain.save_answer import SaveAnswer
    from services.protocols import (
        Clock,
        DebugLogger,
        RetryStrategy,
        RommSaveApi,
        SaveFileStore,
        UnitOfWorkFactory,
    )
    from services.saves.rom_info import RomInfoService
    from services.saves.save_backup import SaveBackup
    from services.saves.save_transfer import SaveTransfer
    from services.saves.status import StatusService
    from services.saves.sync_engine import SyncEngine


class PendingUploads(NamedRefused):
    """Local saves changed since the last sync to the current slot.

    A switch would keep those changes off the server's copy of that slot.
    Raised with ``files``, the names of the changed saves.
    """

    reason = "pending_uploads"


@dataclass(frozen=True)
class SlotSwitchIncomplete(PartialFailure):
    """A slot switch that flipped the slot and persisted it, but downloaded only part of the new slot.

    What was done is the type itself, so it declares no field of its own.
    """


class SlotSwitchBackupFailed(NamedRefused):
    """A slot switch that could not back up a local save and had moved nothing yet.

    The previous slot stays active and every local save stays where it was.
    """

    reason = "backup_failed"


@dataclass(frozen=True)
class SlotSwitchStopped(PartialFailure):
    """A slot switch whose backups stopped partway: the previous slot stays active, nothing was downloaded.

    ``set_aside`` names the local saves already moved into ``.romm-backup``,
    which are no longer tracked.
    """

    set_aside: list[str]


@dataclass(frozen=True)
class _SetAside:
    """What backing up the local saves the new slot does not provide left behind.

    ``moved`` names the files now in ``.romm-backup``. ``failed`` names the
    file whose backup failed and ``error`` why, both ``None`` when every
    backup went through.
    """

    moved: list[str]
    failed: str | None = None
    error: Exception | None = None


class SlotSwitcher:
    """The destructive slot-switch flow.

    Owns ``switch_slot``: the full pre-check + state-sync flow an endpoint
    reaches.
    """

    def __init__(
        self,
        *,
        settings: dict[str, Any],
        uow_factory: UnitOfWorkFactory,
        sync_engine: SyncEngine,
        status_service: StatusService,
        rom_info: RomInfoService,
        romm_api: RommSaveApi,
        retry: RetryStrategy,
        loop: asyncio.AbstractEventLoop,
        clock: Clock,
        save_file_store: SaveFileStore,
        log_debug: DebugLogger,
        save_backup: SaveBackup,
        save_transfer: SaveTransfer,
    ) -> None:
        self._settings = settings
        self._uow_factory = uow_factory
        self._sync_engine = sync_engine
        self._status_service = status_service
        self._rom_info = rom_info
        self._romm_api = romm_api
        self._retry = retry
        self._loop = loop
        self._clock = clock
        self._save_file_store = save_file_store
        self._log_debug = log_debug
        self._save_backup = save_backup
        self._save_transfer = save_transfer

    def _pending_local_changes(self, rom_id: int, save_answer: SaveAnswer, save_state: RomSaveSyncState) -> list[str]:
        """The local saves changed since the last sync to the current slot; empty when there are none.

        A switch over such a file would keep its changes off the server's copy
        of the current slot. Files that were never synced do not count: the
        switch backs them up into ``.romm-backup`` (#965).
        """
        local_files = self._rom_info.find_save_files(rom_id, save_answer=save_answer)
        return list(changed_since_last_sync(local_files, save_state.files, self._save_file_store))

    async def switch_slot(
        self, rom_id: int, new_slot: str
    ) -> dict[str, Any] | SlotSwitchIncomplete | SlotSwitchStopped:
        """Switch the active save slot with immediate state sync.

        Pre-checks (all must pass):
        1. Save sync must be enabled (else ``sync_disabled``).
        2. The target slot name must be real (non-empty). Switching into the
           slot-less legacy bucket is retired (#1276): an empty / whitespace-only
           / ``None`` name refuses with ``invalid_slot_name`` before any lock
           acquisition or I/O.
        3. ROM must be installed (else ``NotInstalled``).
        4. The emulator must not write this game's save beside its content —
           the switch's writes would land where the sync leaves alone
           (``SavefilesInContentDir``). Any other answer a sync would not carry
           — a save inside the game file, a shared card, nothing established —
           raises ``SaveShapeUnsupported`` with that answer's own message.
        5. No local files with pending changes (changed since last sync to
           current slot), else ``PendingUploads`` naming them.
        6. The device id comes from ``SyncEngine.live_device_id``, whose refusal
           is raised; a RomM error fetching the new slot's saves propagates.

        On success the local saves dir and per-file tracking are made coherent
        with the new slot: every local file the new slot does not provide is
        backed up into ``.romm-backup`` (never destroyed — #965) and untracked,
        the newest server save per canonical target is downloaded (#1058), and
        nothing is uploaded — saves are not carried between slots. A partial
        download failure still persists the flipped slot and answers a
        :class:`SlotSwitchIncomplete` so the caller can retry.

        The slot changes only once every one of those backups went through. A
        backup that fails before anything was moved raises
        :class:`SlotSwitchBackupFailed`; one that fails after some files were
        moved persists the previous slot with those files untracked, downloads
        nothing, and answers a :class:`SlotSwitchStopped` naming them.
        """
        rom_id = int(rom_id)

        # 1. Save sync must be enabled
        if not save_sync_enabled(self._settings):
            raise Refused("sync_disabled", SAVE_SYNC_DISABLED)

        # 2. Reject the slot-less legacy bucket as a switch target (#1276): an
        #    empty / whitespace-only / None slot name is rejected before any lock
        #    acquisition or I/O. Legacy saves stay readable via the legacy
        #    bucket, but a ROM is never switched into legacy mode.
        slot_str = str(new_slot).strip() if new_slot else ""
        if not slot_str:
            raise Refused("invalid_slot_name", "Slot name cannot be empty")
        resolved_slot = slot_str

        # 3. ROM must be installed
        info = await self._loop.run_in_executor(None, self._rom_info.get_rom_save_info, rom_id)
        if not info:
            raise NotInstalled("ROM is not installed")

        # 4. The emulator writes this game's save beside its content, which the
        #    sync leaves alone — switching slots would download/delete files
        #    there, so the switch could not take effect. Refuse before any file
        #    write or server fetch; likewise for any answer a sync would not carry.
        save_answer = info["save_answer"]
        if save_answer.in_content_directory:
            self._log_debug(f"switch_slot: rom {rom_id} saves beside its content; refusing")
            raise SavefilesInContentDir(SAVE_SYNC_IN_CONTENT_DIR)
        if save_answer.sync_directory is None:
            self._log_debug(f"switch_slot: rom {rom_id} has no save a sync could carry; refusing")
            raise shape_refusal(save_answer)

        saves_dir = info["saves_dir"]
        system = info["system"]

        # The read→mutate→write of the RomSaveSyncState aggregate must serialise
        # against every other path that touches this ROM's state (sync, status,
        # the other slot mutations). Hold the per-ROM lock across the whole
        # critical section — never around the tail ``get_save_status`` below,
        # which re-acquires the same non-reentrant lock (see SyncEngine.rom_lock).
        async with self._sync_engine.rom_lock(rom_id):
            # A moved save directory is followed before the pending-changes
            # check, or that check would look where the files no longer are.
            await self._sync_engine.follow_save_directory(rom_id, save_answer)
            save_state = await self._loop.run_in_executor(None, read_save_state_or_new, self._uow_factory, rom_id)

            # 5. Check for pending local changes (hashing — run in executor)
            pending = await self._loop.run_in_executor(
                None, self._pending_local_changes, rom_id, save_answer, save_state
            )
            self._log_debug(f"switch_slot: rom={rom_id} new_slot={new_slot!r} pending={pending}")
            if pending:
                raise PendingUploads("Pending local changes — upload or discard first", files=pending)

            # 6. Fetch server saves for the new slot (also proves server is reachable)
            device_id = await self._sync_engine.live_device_id()
            all_server_saves: list[dict[str, Any]] = await self._loop.run_in_executor(
                None,
                lambda: self._retry.with_retry(
                    lambda: self._romm_api.list_saves(rom_id, device_id=device_id),
                ),
            )

            # Filter to the target slot client-side. The fetch above omits the
            # ``slot=`` param (RomM can't address ``slot:null``), so this is the
            # legacy-safe path: route membership through the single
            # ``domain.save_slot.save_in_slot`` funnel (#1061) so the legacy
            # bucket stays isolated from every named switch target — a slot:null
            # save is never pulled into a named slot (#877).
            slot_saves = [s for s in all_server_saves if save_in_slot(s, resolved_slot)]
            self._log_debug(
                f"switch_slot: fetch rom={rom_id} resolved_slot={resolved_slot!r} "
                f"server_all={[(s.get('id'), s.get('slot')) for s in all_server_saves]} "
                f"slot_saves_ids={[s.get('id') for s in slot_saves]}"
            )

            # 7. Back up + untrack every local file the new slot does not provide.
            default_slot = resolve_default_slot(self._settings)
            targets = newest_server_saves_by_target(slot_saves, info["rom_name"], known_names=save_answer.synced_names)
            set_aside = await self._loop.run_in_executor(
                None, self._set_aside_local_saves, rom_id, saves_dir, save_answer, save_state, set(targets)
            )
            if set_aside.failed is not None:
                stopped = self._backup_stopped(set_aside)
                save_state.mark_sync_evaluated(self._clock.now().isoformat())
                await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)
                return stopped

            # 8. Flip the active slot and download the new slot's saves.
            save_state.switch_active_slot(resolved_slot)
            switch_errors = await self._loop.run_in_executor(
                None, self._download_targets, saves_dir, system, save_state, device_id, targets, default_slot
            )

            # 9. Persist the coherent state regardless of partial download failures.
            save_state.mark_sync_evaluated(self._clock.now().isoformat())
            await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)

            if switch_errors:
                return SlotSwitchIncomplete(
                    reason="switch_incomplete",
                    message=f"Switched to slot but {len(switch_errors)} save(s) failed to download — retry",
                )

        # 10. Return fresh status. MUST stay outside the lock above —
        # get_save_status re-acquires rom_lock(rom_id), which would self-deadlock.
        save_status = await self._status_service.get_save_status(rom_id)
        return {"success": True, "save_status": save_status}

    def _set_aside_local_saves(
        self,
        rom_id: int,
        saves_dir: str,
        save_answer: SaveAnswer,
        save_state: RomSaveSyncState,
        target_names: set[str],
    ) -> _SetAside:
        """Back up and untrack every local save the new slot does not provide.

        No stale file is left to upload into the new slot, and no never-synced
        save is destroyed without a backup. The backup folder is made ready
        before the switch's first move — a backup here, or a download that
        will back up the local file it overwrites — and this raises
        :class:`SlotSwitchBackupFailed` when the folder cannot take backups;
        where nothing will be moved, the folder is neither checked nor
        created. The first backup that fails stops the loop, and only the
        files moved before it are untracked; when every backup went through,
        tracked files with no local file left are untracked too. Mutates
        *save_state* in memory; the caller owns the write Unit of Work. Runs
        synchronously — call via ``run_in_executor``.
        """
        local_now = self._rom_info.find_save_files(rom_id, save_answer=save_answer)
        to_move = [lf["filename"] for lf in local_now if lf["filename"] not in target_names]
        to_overwrite = [lf["filename"] for lf in local_now if lf["filename"] in target_names]
        self._log_debug(
            f"switch_slot: apply rom={rom_id} targets={list(target_names)} "
            f"local_files={[lf['filename'] for lf in local_now]} tracked={list(save_state.files)}"
        )

        if to_move or to_overwrite:
            try:
                self._save_backup.prepare_save_backup_folder(saves_dir)
            except (OSError, ValueError) as e:
                self._log_debug(f"switch_slot: backup folder of {saves_dir} cannot take backups: {e}")
                raise SlotSwitchBackupFailed(f"Could not use the .romm-backup folder ({e}). Nothing was moved.") from e
        moved: list[str] = []
        for filename in to_move:
            try:
                was_moved = self._save_backup.back_up_save_file(saves_dir, filename, keep=BACKUP_RETENTION)
            except (OSError, ValueError) as e:
                return _SetAside(moved=moved, failed=filename, error=e)
            self._log_debug(f"switch_slot: back up {filename} moved={was_moved}")
            if was_moved:
                save_state.delete_file_tracking(filename)
                moved.append(filename)

        # Drop stale baseline entries that have no local file. Snapshot the
        # keys first — delete_file_tracking mutates save_state.files.
        stale_tracked = [fn for fn in save_state.files if fn not in target_names]
        for fn in stale_tracked:
            save_state.delete_file_tracking(fn)
        return _SetAside(moved=moved)

    def _backup_stopped(self, set_aside: _SetAside) -> SlotSwitchStopped:
        """Report a switch whose backups stopped, naming what was already set aside.

        Raises :class:`SlotSwitchBackupFailed`, naming the error, where nothing
        was moved yet. Once files are in ``.romm-backup`` the answer names them,
        and the error goes to the log only.
        """
        self._log_debug(
            f"switch_slot: stopped after backing up {len(set_aside.moved)} file(s): "
            f"{set_aside.failed}: {set_aside.error}"
        )
        if not set_aside.moved:
            raise SlotSwitchBackupFailed(
                f"Could not move {set_aside.failed} into .romm-backup ({set_aside.error}). Nothing was moved."
            )
        return SlotSwitchStopped(
            reason="switch_stopped",
            message=(
                f"Could not move {set_aside.failed} into .romm-backup, so the slot was not switched. "
                f"These were already moved to .romm-backup: {', '.join(set_aside.moved)}."
            ),
            set_aside=list(set_aside.moved),
        )

    def _download_targets(
        self,
        saves_dir: str,
        system: str,
        save_state: RomSaveSyncState,
        device_id: LiveDeviceId,
        targets: dict[str, dict[str, Any]],
        default_slot: str | None,
    ) -> list[str]:
        """Download the newest server save per target of the new slot.

        Mutates *save_state* in memory; the caller owns the write Unit of Work.
        Runs synchronously — call via ``run_in_executor``. Returns the filenames
        whose download failed (empty when all succeeded).
        """
        errors: list[str] = []
        for target_name, server_save in targets.items():
            self._log_debug(
                f"switch_slot: download target={target_name} save_id={server_save.get('id')} "
                f"slot={server_save.get('slot')!r}"
            )
            try:
                self._save_transfer.download_save_file(
                    server_save, saves_dir, target_name, save_state, device_id, system, default_slot
                )
            except Exception as e:
                self._log_debug(f"switch_slot: failed to download {target_name}: {e}")
                errors.append(target_name)
        return errors
