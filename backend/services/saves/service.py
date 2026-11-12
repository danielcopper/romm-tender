"""Save-sync aggregate root and facade for the save endpoints.

Holds the save-sync sub-services the composition root built over the SQLite
``rom_save_sync_states`` aggregate (reached through the injected Unit-of-Work
factory) and exposes the use cases the endpoints call. The five save-sync
feature toggles live in ``settings.json`` and are read/written here directly;
the device label beside them is ``DeviceRegistry``'s. Most methods are thin
delegations; orchestration that genuinely spans multiple sub-services lives
here, single-sub-service logic does not.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.rom_save_sync_state import RomSaveSyncState
from lib.background_tasks import BackgroundTasks
from lib.partial_failure import PartialFailure
from services.saves._config import SaveServiceConfig
from services.saves._refusals import game_running
from services.saves._settings import (
    ALLOWED_SETTINGS_KEYS,
    sanitize_setting,
    save_sync_settings_view,
)
from services.saves.slots.switching import SlotSwitchBackupFailed, SlotSwitchBackupFolderUnusable, SlotSwitchStopped
from services.saves.sync_engine._running_game import RunningSkips

# kv_config marker for the one-time pass that records the installed ROMs'
# answered save directories: present once the pass finished.
_KV_SAVE_DIRECTORIES_RECORDED = "save_directories_recorded"

if TYPE_CHECKING:
    from domain.save_answer import SaveAnswer
    from services.protocols import UnitOfWorkFactory
    from services.saves.slots.switching import SlotSwitchIncomplete
    from services.saves.sync_engine import SaveSweepIncomplete


@dataclass(frozen=True)
class SaveDeletionIncomplete(PartialFailure):
    """A save deletion that removed only part of the files it found."""

    deleted_count: int


class SaveService:
    """Aggregate root for bidirectional save file sync between RetroDECK and RomM.

    A use case an endpoint calls checks its conflict rules at its entry, under that
    endpoint's name, and raises the rule's refusal when one holds
    (GLOSSARY.md → Conflict rules). Bulk local-save deletion's orchestration
    lives directly on the aggregate root because it spans
    :class:`RomInfoService` (file discovery), the on-disk save files (via the
    injected ``SaveFileStore``), and the ``rom_save_sync_states`` repository
    (file-tracking state hygiene) without belonging to any single sub-service.

    Parameters
    ----------
    config:
        Construction-time wiring bundle. See :class:`SaveServiceConfig` for
        the per-field rationale.
    """

    def __init__(self, *, config: SaveServiceConfig) -> None:
        self._config = config
        self._logger = config.logger
        self._settings = config.settings
        self._save_file_store = config.save_file_store
        self._uow_factory: UnitOfWorkFactory = config.uow_factory
        self._settings_persister = config.settings_persister
        self._rules = config.conflict_rules
        self._loop = config.loop
        self._tasks = BackgroundTasks(logger=config.logger)
        self._rom_info = config.rom_info
        self._sync_engine = config.sync_engine
        self._status = config.status
        self._local_saves = config.local_saves
        self._versions = config.versions
        self._copies = config.copies
        self._slot_listing = config.slot_listing
        self._slot_switcher = config.slot_switcher
        self._setup_wizard = config.setup_wizard
        self._slot_deleter = config.slot_deleter

    # ------------------------------------------------------------------
    # Device registration (delegated to SyncEngine)
    # ------------------------------------------------------------------

    async def ensure_device_registered(self) -> dict[str, Any]:
        """Register or heal this device with the RomM server, whether save sync is on or off.

        Satisfies the ``DeviceRegisterFn`` seam ConnectionService calls after a
        successful sign-in.
        """
        return await self._sync_engine.ensure_device_registered()

    async def list_devices(self) -> dict[str, Any]:
        """List all devices registered with the RomM server for this user."""
        return await self._sync_engine.list_devices()

    # ------------------------------------------------------------------
    # Status (delegated to StatusService)
    # ------------------------------------------------------------------

    async def get_save_status(self, rom_id: int) -> dict[str, Any]:
        """Get save sync status for a ROM (local files, server saves, conflict state)."""
        async with self._rules.hold("get_save_status", prune=True):
            return await self._status.get_save_status(rom_id)

    async def refresh_save_status(self, rom_id: int) -> dict[str, Any]:
        """Start a background save-status check for a ROM and answer at once.

        The check emits ``save_status_updated`` with the status answer; if the
        read raises, it logs and emits nothing. It holds an operation on the
        prune conflicts until it ends, so a cleanup cannot start under it.
        """
        async with self._rules.hold("refresh_save_status", prune=True):
            task = self._tasks.spawn(self.check_save_status_background(rom_id), "refresh_save_status")
            await self._rules.hold_until_done(task, "refresh_save_status")
            return {"success": True}

    async def check_save_status_background(self, rom_id: int) -> None:
        """Run full save status check in background and emit result to frontend."""
        await self._status.check_save_status_background(rom_id)

    def check_core_change(self, rom_id: int) -> dict[str, Any]:
        """Check if emulator core changed since last sync for a ROM."""
        return self._status.check_core_change(rom_id)

    async def get_local_save_files(self, rom_id: int) -> dict[str, Any]:
        """This device's save files for a ROM, or the refusal its sync is refused with, asking RomM nothing."""
        return await self._local_saves.get_local_save_files(rom_id)

    async def check_local_drift(self, rom_id: int) -> dict[str, Any]:
        """Report how the ROM's local save files stand against their last recorded sync.

        Answers ``{rom_id, drifted, only_here, stranding}``. Satisfies the
        ``SaveDriftProbeFn`` seam.
        """
        return await self._status.check_local_drift(rom_id)

    # ------------------------------------------------------------------
    # Sync orchestration (delegated to SyncEngine)
    # ------------------------------------------------------------------

    async def pre_launch_sync(self, rom_id: int) -> dict[str, Any]:
        """Sync this ROM's saves with the server before the game starts."""
        async with self._rules.hold(
            "pre_launch_sync", update=True, migration=True, prune=True, refusal_details={"synced": 0}
        ):
            return await self._sync_engine.pre_launch_sync(rom_id)

    def pre_launch_sync_state(self, rom_id: int) -> dict[str, Any]:
        """What the sync before a start of *rom_id* is doing, and whom it waits for."""
        return self._sync_engine.pre_launch_sync_state(rom_id)

    def stop_pre_launch_sync(self, rom_id: int) -> dict[str, Any]:
        """Stop the sync before a start of *rom_id*, and no other sync."""
        return self._sync_engine.stop_pre_launch_sync(rom_id)

    async def post_exit_sync(self, rom_id: int) -> dict[str, Any]:
        """Upload changed saves after game exit."""
        return await self._sync_engine.post_exit_sync(rom_id)

    def syncs_after_exit(self) -> bool:
        """The answer ``SessionPostExitSync.syncs_after_exit`` states (``services/protocols/cross_service.py``)."""
        return self._sync_engine.syncs_after_exit()

    async def sync_rom_saves(self, rom_id: int) -> dict[str, Any]:
        """Bidirectional sync for a single ROM (manual trigger from game detail)."""
        async with self._rules.hold(
            "sync_rom_saves", update=True, migration=True, prune=True, refusal_details={"synced": 0}
        ):
            return await self._sync_engine.sync_rom_saves(rom_id)

    async def sync_all_saves(self) -> dict[str, Any] | SaveSweepIncomplete:
        """Manual full sync of all ROMs with shortcuts (both directions)."""
        async with self._rules.hold(
            "sync_all_saves", update=True, migration=True, prune=True, refusal_details={"synced": 0, "conflicts": 0}
        ):
            return await self._sync_engine.sync_all_saves()

    async def record_save_directories_once(self) -> None:
        """Record the installed ROMs' answered save directories, once per database.

        The first start after the record existed has no record for any game, so
        a directory that moved before Tender next touched that game's saves
        would be taken for a first sight and its files left behind. This pass
        closes that: it asks the resolver about each installed ROM, serially,
        and records the answer where nothing is recorded yet and the follow
        would act on it. The ``kv_config`` marker is written only once the pass
        finished over a detected emulator installation with no ROM failing, so
        a pass cut short by a shutdown, one with nothing to ask yet, or one in
        which a ROM failed runs again; recording where nothing is recorded is
        safe to repeat.
        """
        if await self._loop.run_in_executor(None, self._kv_marker_set, _KV_SAVE_DIRECTORIES_RECORDED):
            return
        if not await self._loop.run_in_executor(None, self._config.save_locations.installation_detected):
            # Every answer would refuse, so the pass would record nothing and
            # its marker would stop it running once there is something to ask.
            self._config.logger.info("No emulator installation detected; recording the save directories next start")
            return
        try:
            all_recorded = await self._sync_engine.record_save_directories()
        except Exception:
            # A background task nobody awaits: an exception left on it is never
            # reported. The next start runs the pass again.
            self._config.logger.exception("Recording the answered save directories failed; retrying next start")
            return
        if not all_recorded:
            self._config.logger.warning("Recording some save directories failed; retrying next start")
            return
        await self._loop.run_in_executor(None, self._set_kv_marker, _KV_SAVE_DIRECTORIES_RECORDED)

    async def rerecord_save_directories(self) -> None:
        """Record each installed ROM's answered save directory afresh.

        Satisfies ``SaveDirectoriesRecorderFn``: the RetroDECK home migration
        calls it once it has moved the files, so no record is left naming the
        old home — each is replaced by today's answer, or dropped where the
        follow would not act on that answer.
        """
        await self._sync_engine.rerecord_save_directories()

    def _kv_marker_set(self, key: str) -> bool:
        with self._uow_factory() as uow:
            return uow.kv_config.get(key) is not None

    def _set_kv_marker(self, key: str) -> None:
        with self._uow_factory() as uow:
            uow.kv_config.set(key, "1")

    async def resolve_sync_conflict(
        self,
        rom_id: int,
        filename: str,
        server_save_id: int,
        action: str,
        before_start: bool = False,
    ) -> dict[str, Any]:
        """Resolve a pending sync conflict (true two-sided divergence); *before_start* from the launch check only."""
        async with self._rules.hold("resolve_sync_conflict", update=True, migration=True, prune=True):
            return await self._sync_engine.resolve_sync_conflict(
                rom_id, filename, server_save_id, action, before_start=before_start
            )

    # ------------------------------------------------------------------
    # Slots (delegated to the slot sub-modules)
    # ------------------------------------------------------------------

    async def get_save_slots(self, rom_id: int) -> dict[str, Any]:
        """List available save slots for a ROM."""
        async with self._rules.hold("get_save_slots", prune=True):
            return await self._slot_listing.get_save_slots(rom_id)

    async def get_slot_saves(self, rom_id: int, slot: str) -> dict[str, Any]:
        """Fetch server save files for a specific slot."""
        return await self._slot_listing.get_slot_saves(rom_id, slot)

    async def switch_slot(
        self, rom_id: int, new_slot: str
    ) -> dict[str, Any] | SlotSwitchIncomplete | SlotSwitchStopped:
        """Switch the active save slot with immediate state sync.

        A switch refused or stopped by its backups is logged as a warning; its
        refusal is raised and its answer returned unchanged.
        """
        async with self._rules.hold("switch_slot", update=True, migration=True, prune=True):
            try:
                result = await self._slot_switcher.switch_slot(rom_id, new_slot)
            except (SlotSwitchBackupFailed, SlotSwitchBackupFolderUnusable) as refusal:
                self._logger.warning(
                    f"Slot switch of rom {rom_id} to {new_slot!r} refused ({refusal.reason}): {refusal.message}"
                )
                raise
        if isinstance(result, SlotSwitchStopped):
            self._logger.warning(
                f"Slot switch of rom {rom_id} to {new_slot!r} stopped ({result.reason}): {result.message}"
            )
        return result

    def is_save_tracking_configured(self, rom_id: int) -> dict[str, Any]:
        """Check if save slot tracking is configured for a game."""
        return self._setup_wizard.is_save_tracking_configured(rom_id)

    async def get_save_setup_info(self, rom_id: int) -> dict[str, Any]:
        """Get info needed for the first-sync setup wizard."""
        return await self._setup_wizard.get_save_setup_info(rom_id)

    async def get_save_sync_availability(self, rom_id: int) -> dict[str, Any]:
        """Whether this game's saves sync, with no device registration and no save listing."""
        return await self._setup_wizard.get_save_sync_availability(rom_id)

    async def confirm_slot_choice(
        self,
        rom_id: int,
        chosen_slot: str | None,
        migrate: bool = False,
        migrate_from_slot: str | None = None,
        use_server_on_conflict: bool = False,
    ) -> dict[str, Any]:
        """Confirm which slot to use for a game's save sync.

        ``chosen_slot`` is the slot name. Migration runs only when ``migrate`` is
        true, copying the newest legacy save per canonical target from
        ``migrate_from_slot`` (``None`` = the legacy no-slot source) into
        ``chosen_slot`` (#1498). A differing local save is held for the user's
        decision unless ``use_server_on_conflict`` resolves it in the server's
        favour.
        """
        async with self._rules.hold("confirm_slot_choice", update=True, migration=True, prune=True):
            return await self._setup_wizard.confirm_slot_choice(
                rom_id, chosen_slot, migrate, migrate_from_slot, use_server_on_conflict
            )

    async def get_slot_delete_info(self, rom_id: int, slot: str) -> dict[str, Any]:
        """Return info about what deleting a slot would do, for the confirmation modal."""
        return await self._slot_deleter.get_slot_delete_info(rom_id, slot)

    async def delete_slot(self, rom_id: int, slot: str) -> dict[str, Any]:
        """Delete a save slot and all its saves (local state + server if applicable)."""
        async with self._rules.hold("delete_slot", update=True, migration=True, prune=True):
            return await self._slot_deleter.delete_slot(rom_id, slot)

    # ------------------------------------------------------------------
    # Versions (delegated to VersionsService)
    # ------------------------------------------------------------------

    async def list_file_versions(self, rom_id: int, slot: str, filename: str) -> dict[str, Any]:
        """List server-side versions of *filename* in the active slot."""
        return await self._versions.list_file_versions(rom_id, slot, filename)

    async def rollback_to_version(self, rom_id: int, slot: str, save_id: int) -> dict[str, Any]:
        """Switch the local + tracked save to a chosen older server version."""
        async with self._rules.hold("saves_rollback_to_version", update=True, migration=True, prune=True):
            return await self._versions.rollback_to_version(rom_id, slot, save_id)

    # ------------------------------------------------------------------
    # Copy-to-slot (delegated to SaveCopyService)
    # ------------------------------------------------------------------

    async def copy_save_to_slot(self, rom_id: int, save_id: int, target_slot: str) -> dict[str, Any]:
        """Copy a specific server save into another slot (which becomes active)."""
        async with self._rules.hold("copy_save_to_slot", update=True, migration=True, prune=True):
            return await self._copies.copy_save_to_slot(rom_id, save_id, target_slot)

    # ------------------------------------------------------------------
    # Work in flight (read by the update install)
    # ------------------------------------------------------------------

    def is_save_sync_in_flight(self) -> bool:
        """Whether a save-sync run is under way — pre-launch, post-exit, one ROM or all of them."""
        return self._sync_engine.is_sync_in_flight()

    def is_save_directory_move_in_flight(self) -> bool:
        """Whether a game's save files are being carried to the directory their emulator moved them to."""
        return self._sync_engine.is_following_save_directory()

    # ------------------------------------------------------------------
    # Settings (settings.json — read/written directly)
    # ------------------------------------------------------------------

    def get_save_sync_settings(self) -> dict[str, Any]:
        """Return current save sync settings as the frontend dict shape."""
        return save_sync_settings_view(self._settings)

    async def update_save_sync_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Update save sync settings (sync toggles, slot, etc.) in settings.json."""
        async with self._rules.hold("update_save_sync_settings", update=True, migration=True):
            for key, value in settings.items():
                if key not in ALLOWED_SETTINGS_KEYS:
                    continue
                coerced, skip = sanitize_setting(key, value)
                if skip:
                    continue
                self._settings[key] = coerced

            self._settings_persister.save_settings()
            return {"success": True, "settings": save_sync_settings_view(self._settings)}

    def forget_device(self) -> None:
        """Drop the registered server device id on a server-origin change.

        Delegates to the sync engine, which holds the DeviceRegistry — the single
        owner of ``kv_config["device_id"]``. The composition root wires this as the
        ``DeviceForgetFn`` handed to ConnectionService, which invokes it after
        a successful sign-in to a different origin so the stale id cannot 404
        against the new server's negotiate.
        """
        self._sync_engine.forget_device()

    def get_device_id(self) -> str | None:
        """Return the registered server device id (``None`` when unregistered).

        Delegates to the sync engine, which holds the DeviceRegistry — the single
        owner of ``kv_config["device_id"]``. The composition root wires this as the
        ``DeviceIdProvider`` handed to PlaytimeService, which reads the id to
        attribute native play-session ingests and gate the offline outbox. No
        save operation reads it: each takes ``SyncEngine.live_device_id``'s.
        """
        return self._sync_engine.get_device_id()

    # ------------------------------------------------------------------
    # Bulk local-save deletion
    # ------------------------------------------------------------------

    async def _read_and_follow(self, rom_id: int) -> SaveAnswer | None:
        """This ROM's live reading, after a moved save directory has been followed with it.

        The caller holds ``rom_lock``; the same reading is then handed on, so the
        files a delete or a count sees are where the emulator now looks.
        """
        answer = await self._sync_engine.read_save_answer(rom_id)
        await self._sync_engine.follow_save_directory(rom_id, answer)
        return answer

    async def _delete_saves_for_roms(self, rom_ids: list[int], skips: RunningSkips) -> tuple[int, list[str]]:
        """Delete local save files for the given ROM IDs and clear file tracking state.

        For each ROM ID, under its ``rom_lock``, passes a ROM whose game is
        running over into *skips*, touching nothing of it; otherwise follows a
        moved save directory first and then removes the files the answer names
        on disk (counting successes and collecting per-file error strings), and
        clears the ROM's per-file tracking dict via the aggregate's
        ``clear_baselines`` verb. Slot config (``active_slot``,
        ``slot_confirmed``, ``emulator``, ``last_synced_core``,
        ``own_upload_ids``, ``slots``, ``system``) is preserved. Each ROM's
        state is persisted in its own short write UoW.

        Returns a ``(total_deleted, errors)`` tuple.
        """
        total_deleted = 0
        errors: list[str] = []
        for rom_id in rom_ids:
            async with self._sync_engine.rom_lock(rom_id):
                if await self._sync_engine.is_game_running(rom_id):
                    skips.skip(rom_id, await self._sync_engine.game_name(rom_id))
                    continue
                answer = await self._read_and_follow(rom_id)
                deleted, rom_errors = await self._loop.run_in_executor(
                    None, self._delete_saves_for_rom_io, rom_id, answer
                )
            total_deleted += deleted
            errors.extend(rom_errors)
        return total_deleted, errors

    def _delete_saves_for_rom_io(self, rom_id: int, answer: SaveAnswer | None) -> tuple[int, list[str]]:
        deleted = 0
        errors: list[str] = []
        files = self._rom_info.find_save_files(rom_id, save_answer=answer)
        for f in files:
            try:
                self._save_file_store.remove_file(f["path"])
                deleted += 1
            except Exception as e:
                errors.append(f"{f['filename']}: {e}")
        with self._uow_factory() as uow:
            save_state = uow.rom_save_sync_states.get(rom_id)
            # Nothing to clear when the ROM has neither tracked save state
            # nor any local save files (e.g. a non-installed ROM with no
            # roms row — persisting an empty aggregate would violate the FK).
            if save_state is None and not files:
                return deleted, errors
            if save_state is None:
                save_state = RomSaveSyncState()
            save_state.clear_baselines()
            uow.rom_save_sync_states.save(rom_id, save_state)
        return deleted, errors

    async def delete_local_saves(self, rom_id: int) -> dict[str, Any] | SaveDeletionIncomplete:
        """Delete this ROM's local save files.

        A deletion that could not remove every file it found answers
        :class:`SaveDeletionIncomplete`; one whose game is running is refused
        with ``game_running``.
        """
        skips = RunningSkips()
        async with self._rules.hold("delete_local_saves", update=True, migration=True, prune=True):
            deleted, errors = await self._delete_saves_for_roms([int(rom_id)], skips)
        if skips.skipped:
            raise game_running(skips.skipped[0][1])

        if deleted == 0 and not errors:
            return {"success": True, "deleted_count": 0, "message": "No local save files found"}

        if errors:
            return SaveDeletionIncomplete(
                reason="delete_incomplete",
                message=f"Deleted {deleted} file(s), {len(errors)} error(s)",
                deleted_count=deleted,
            )
        return {
            "success": True,
            "deleted_count": deleted,
            "message": f"Deleted {deleted} save file(s)",
        }

    def _installed_rom_ids_on_platform(self, platform_folder: str) -> list[int]:
        """The installed ROMs whose game is on the platform with ``fs_slug`` *platform_folder*.

        An install is found through its game's ``roms.platform_fs_slug``, so a
        game with none recorded is on no platform (#2452 D9, D17).
        """
        with self._uow_factory() as uow:
            on_platform = {rom.rom_id for rom in uow.roms.iter_by_platform_folder(platform_folder)}
            return [install.rom_id for install in uow.rom_installs.iter_all() if install.rom_id in on_platform]

    async def count_platform_saves(self, platform_folder: str) -> dict[str, Any]:
        """How many local save files the installed ROMs on the platform *platform_folder* hold.

        The read half of :meth:`delete_platform_saves`, over the same two steps
        in the same order — the platform's installed ROM ids, then each one's
        save files — so the number a button offers is the number the delete would
        remove, but for a game running when the delete reaches it, whose saves
        the delete leaves alone and names. It deletes nothing, but it is not a
        pure read: like the delete, it follows each ROM's moved save directory
        first — but a running game's — which can move files, back up a
        collision and write the ROM's answered-directory record.

        The Library page's platform detail asks it once per selected platform,
        beside the core read, and puts the count on Delete _N_ save files. That
        button disables at zero rather than disappearing, so a platform whose
        shortcuts are gone and whose saves remain still offers the one action
        that can reach them.

        **The walk is per INSTALLED ROM and each one asks the save resolver**,
        which reads the machine — a full read per ROM, so the count grows with
        the platform's installed games. The answer is deliberately not cached:
        this count exists so the number the button offers equals the number the
        delete removes, and caching one side of that pair breaks the guarantee
        the sentence above makes. A cached answer on a destructive path is worse
        than a slow one.
        """
        rom_ids = await self._loop.run_in_executor(None, self._installed_rom_ids_on_platform, platform_folder)
        # The file walk runs after the id read's UoW has closed, for the reason
        # the delete does the same: a Unit of Work never spans file I/O. Each ROM
        # follows a moved directory first, as the delete does, so the two agree.
        count = 0
        for rom_id in rom_ids:
            async with self._sync_engine.rom_lock(rom_id):
                answer = await self._read_and_follow(rom_id)
                files = await self._loop.run_in_executor(
                    None, functools.partial(self._rom_info.find_save_files, rom_id, save_answer=answer)
                )
            count += len(files)
        return {"count": count}

    async def delete_platform_saves(self, platform_folder: str) -> dict[str, Any] | SaveDeletionIncomplete:
        """Delete the local save files of every installed ROM on this platform.

        A deletion that could not remove every file it found answers
        :class:`SaveDeletionIncomplete`. A game that is running keeps its saves,
        and the answer names it.
        """
        skips = RunningSkips()
        async with self._rules.hold("delete_platform_saves", update=True, migration=True, prune=True):
            rom_ids = await self._loop.run_in_executor(None, self._installed_rom_ids_on_platform, platform_folder)
            total_deleted, total_errors = await self._delete_saves_for_roms(rom_ids, skips)

        rom_count = len(rom_ids) - len(skips.skipped)

        if total_errors:
            return SaveDeletionIncomplete(
                reason="delete_incomplete",
                message=skips.annotate(
                    f"Deleted {total_deleted} file(s) from {rom_count} ROM(s), {len(total_errors)} error(s)"
                ),
                deleted_count=total_deleted,
            )
        return {
            "success": True,
            "deleted_count": total_deleted,
            "message": skips.annotate(f"Deleted {total_deleted} save file(s) from {rom_count} ROM(s)"),
            "skipped_running": skips.as_list(),
        }


__all__ = ["SaveDeletionIncomplete", "SaveService", "SaveServiceConfig"]
