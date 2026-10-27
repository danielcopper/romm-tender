"""Copy a specific server save into another slot.

Owns the per-save "Copy to slot…" flow: it takes one server save (from a
named slot or the read-only legacy no-slot bucket) and copies its content
into a target slot, which becomes the ROM's active/confirmed slot with the
copied save as its current save. The source save is never deleted — this is
a copy, not a move. The actual file/server writes go through SaveTransfer;
this class owns only the orchestration (pre-flight sync, make-current,
POST into the target). Persistence is the operation's own narrow Unit of
Work (ADR-0006).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.save_answer import (
    SAVE_SHAPE_UNSUPPORTED_REASON,
    SAVE_SYNC_CONTENT_DIR_REASON,
    save_finding_details,
    save_shape_message,
)
from domain.save_slot import save_in_slot
from domain.save_status import compute_multi_file_slot
from lib.errors import RommApiError, RommConflictError, RommNotFoundError, classify_error, with_system_text
from services.saves._helpers import local_save_target, newest_server_saves_by_target
from services.saves._save_state import read_save_state_or_new, write_save_state
from services.saves._settings import resolve_default_slot

if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import Callable

    from domain.live_device_id import LiveDeviceId
    from domain.rom_save_sync_state import RomSaveSyncState
    from services.protocols import DebugLogger, RetryStrategy, RommSaveApi, UnitOfWorkFactory
    from services.saves.rom_info import RomInfoService
    from services.saves.save_transfer import SaveTransfer
    from services.saves.sync_engine import SyncEngine


@dataclass(frozen=True)
class SaveCopyServiceConfig:
    """Frozen wiring bundle handed to ``SaveCopyService.__init__``.

    Mirrors :class:`VersionsServiceConfig` field for field — the copy flow
    reuses the same orchestration (per-ROM lock, pre-flight sync). Holds the
    live ``settings.json`` dict (default-slot seeding), the Unit-of-Work
    factory (the transactional seam over the SQLite repositories), the peer
    save sub-services consumed during orchestration (sync_engine, whose
    ``live_device_id`` answers the device id, and rom_info), the core resolver
    used to stamp the upload emulator tag, the Protocol-typed RomM adapter and
    retry strategy, the backend's event loop, the standard-library logger, the
    ``DebugLogger`` seam, and the save transfer every file write of the copy
    goes through.
    """

    settings: dict[str, Any]
    uow_factory: UnitOfWorkFactory
    sync_engine: SyncEngine
    rom_info: RomInfoService
    resolve_core: Callable[[int], str | None]
    romm_api: RommSaveApi
    retry: RetryStrategy
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger
    save_transfer: SaveTransfer


class SaveCopyService:
    """Aggregate root for the copy-save-to-slot flow.

    Per-ROM lock acquisition is delegated to the injected ``SyncEngine``;
    save-state persistence is the operation's own narrow Unit of Work. This
    class owns the copy orchestration on top of them.
    """

    def __init__(self, *, config: SaveCopyServiceConfig) -> None:
        self._config = config
        self._settings = config.settings
        self._uow_factory = config.uow_factory
        self._sync_engine = config.sync_engine
        self._rom_info = config.rom_info
        self._resolve_core = config.resolve_core
        self._romm_api = config.romm_api
        self._retry = config.retry
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug
        self._save_transfer = config.save_transfer

    def _local_component_filenames(self, rom_id: int) -> list[str]:
        """Distinct local save filenames on disk for the ROM (one per extension).

        More than one distinct filename means the current slot's save is an
        N-file set (e.g. Saturn ``.bkr``/``.bcr``/``.smpc``), not a single file
        — the interim #908 guard refuses a copy in that case. Reads only the
        local saves directory, no network: a copy target is always an
        *installed* ROM. Returns an empty list when the ROM is not installed.
        """
        return [lf["filename"] for lf in self._rom_info.find_save_files(rom_id)]

    @staticmethod
    def _find_already_present(server_saves: list[dict[str, Any]], save_id: int, target_slot: str) -> int | None:
        """The id of a save in *target_slot* whose content matches *save_id*, or None.

        Compares the chosen save's ``content_hash`` (RomM's own digest of the
        bytes) against every save already in the target slot. Returns the first
        match's id — meaning the copy would be a content-identical no-op that
        RomM would dedup server-side. Returns None when the chosen save is absent
        (a later ``version_deleted``), carries no ``content_hash`` (older servers;
        the copy falls through to RomM's own dedup), or has no content twin in the
        slot.
        """
        source = next((s for s in server_saves if s.get("id") == save_id), None)
        if source is None:
            return None
        source_hash = source.get("content_hash")
        if not source_hash:
            return None
        match = next(
            (s for s in server_saves if save_in_slot(s, target_slot) and s.get("content_hash") == source_hash),
            None,
        )
        return match.get("id") if match is not None else None

    def _adopt_target_head_as_baseline(
        self,
        save_state: RomSaveSyncState,
        server_saves: list[dict[str, Any]],
        target_slot: str,
        canonical: str,
        rom_name: str,
        save_names: tuple[str, ...],
    ) -> None:
        """Record the target slot's newest save for *canonical* as the file's baseline.

        Called after an upload that failed, so that the next sync reads the copy
        on disk as a change against the save the target slot holds; what that
        sync then does is the copy section of
        ``docs/architecture/save-file-sync-architecture.md``. A slot with no such
        save keeps the baseline the download recorded. The baseline, and the
        dropped tracking of a head without a ``content_hash`` or an id, are
        :meth:`RomSaveSyncState.adopt_head_with_local_change`'s.
        """
        in_slot = [s for s in server_saves if save_in_slot(s, target_slot)]
        head = newest_server_saves_by_target(in_slot, rom_name, known_names=save_names).get(canonical)
        if head is None:
            return
        if not save_state.adopt_head_with_local_change(canonical, head):
            self._logger.warning(
                "_copy_save_to_slot_io: newest save %r in slot %r carries no content_hash or id; "
                "dropped the tracking of %r",
                head.get("id"),
                target_slot,
                canonical,
            )

    # ------------------------------------------------------------------
    # Copy-to-slot API
    # ------------------------------------------------------------------

    def _copy_save_to_slot_io(
        self,
        rom_id: int,
        save_state: RomSaveSyncState,
        device_id: LiveDeviceId,
        core_so: str | None,
        save_id: int,
        target_slot: str,
        info: dict[str, Any],
        server_saves: list[dict[str, Any]],
        default_slot: str,
    ) -> dict[str, Any]:
        """Blocking I/O portion of the copy flow — runs in executor.

        By the time this runs the caller has already brought the *current* slot
        in sync (the matrix pre-flight) so no dirty local is lost when the
        download below overwrites the canonical local file. This function is the
        copy proper:

        1. Locate the chosen save in the (unfiltered) server list; a missing id
           is ``version_deleted``.
        2. Download the chosen save's content onto the canonical local file
           (backs the current local file up first, #965). Where this device
           cannot write it, the copy is ``copy_failed``; a RomM error raises on.
        3. Make the target the ROM's active/confirmed slot
           (:meth:`RomSaveSyncState.confirm_slot`) — the upload resolves its slot
           purely from ``active_slot`` (:meth:`RomSaveSyncState.upload_slot`), so this must
           precede the POST for the copy to land in the target.
        4. POST that content into the now-active target slot
           (``server_save=None`` → a new save, ``overwrite=false`` → the 409
           backstop guards a busy target). A 409 is ``target_slot_busy`` (the
           target has newer changes from another device — resolve it first); any
           other upload error is ``upload_failed``: the copy is complete on this
           device, the target stays active, and
           :meth:`_adopt_target_head_as_baseline` records the baseline the next
           sync compares the copy with.

        The source save is never touched. Mutates *save_state* in memory; the
        caller owns the write UoW.
        """
        target_save = next((s for s in server_saves if s.get("id") == save_id), None)
        if target_save is None:
            return {"status": "version_deleted"}

        saves_dir = info["saves_dir"]
        system = info["system"]
        rom_name = info["rom_name"]
        save_names = info["save_answer"].synced_names
        canonical = local_save_target(target_save, rom_name, known_names=save_names)
        local_path = os.path.join(saves_dir, canonical)

        # Bring the chosen save's content down onto the canonical local file. The
        # existing local file (the current slot's save) is backed up into
        # ``.romm-backup`` first, so nothing that lives only on disk is lost.
        try:
            self._save_transfer.download_save_file(
                target_save, saves_dir, canonical, save_state, device_id, system, default_slot
            )
        except OSError as e:
            self._logger.error(
                "_copy_save_to_slot_io: save %s could not be written for rom=%s: %s",
                save_id,
                rom_id,
                e,
            )
            return {"status": "copy_failed", "message": with_system_text("it could not be written on this device", e)}

        # Never before the download: the caller persists this state however the
        # copy ends, so a download that fails must leave the active slot as it was.
        save_state.confirm_slot(target_slot)

        try:
            self._save_transfer.upload_save_file(
                rom_id,
                local_path,
                canonical,
                save_state,
                device_id,
                system,
                core_so,
                server_save=None,
                default_slot=default_slot,
                overwrite=False,
            )
        except RommConflictError as e:
            # The target slot has a newer server version this device hasn't
            # synced — an overwrite=false POST 409s rather than stack on it.
            # Surface it so the user resolves/syncs the target first, then retries.
            self._logger.warning(
                "_copy_save_to_slot_io: target slot %r busy for rom=%s save=%s: %s",
                target_slot,
                rom_id,
                save_id,
                e,
            )
            return {"status": "target_slot_busy", "message": str(e)}
        except Exception as e:
            self._logger.error(
                "_copy_save_to_slot_io: upload into slot %r failed for rom=%s save=%s: %s",
                target_slot,
                rom_id,
                save_id,
                e,
            )
            self._adopt_target_head_as_baseline(save_state, server_saves, target_slot, canonical, rom_name, save_names)
            return {"status": "upload_failed", "message": str(e)}

        return {"status": "ok"}

    async def copy_save_to_slot(self, rom_id: int, save_id: int, target_slot: str) -> dict[str, Any]:
        """Copy one server save into *target_slot*, making it the active slot.

        The source save (``save_id``, from any slot including the legacy no-slot
        bucket) is copied — never moved or deleted — into ``target_slot``, which
        becomes the ROM's active/confirmed slot with the copied save as its
        current save.

        Flow:

        1. Validate ``target_slot`` (strip; empty/whitespace/None →
           ``invalid_slot_name``).
        2. Acquire ``rom_lock`` — every ``RomSaveSyncState`` read-mutate-write
           runs under it — and raise ``game_running`` while the game runs.
        3. Refuse an unconfigured ROM (``active_slot`` None / slot not confirmed)
           with ``not_configured``. The Copy action is only reachable on a
           configured ROM; this is the defensive backstop, not a UI-gate reliance.
        4. ``rom_not_installed`` when the ROM has no install record.
        5. Multi-file (#908) saves, saves beside the game file (#239) and any other
           answer a sync would not carry are ``unsupported``.
        6. Take the device id from ``SyncEngine.live_device_id``, which registers
           or heals the device first; its refusal (``device_not_registered`` or
           ``server_unreachable``) is raised, before anything is synced.
        7. Pre-flight ``do_sync_rom_saves`` on the current (confirmed) slot,
           unconditionally, to protect its dirty local before the copy overwrites
           it — ``conflict_blocked`` / ``preflight_failed`` on a bad pre-flight.
        8. ``list_saves`` with no slot filter (the source may be in any slot);
           a transport failure is ``server_unreachable``, a definitive 404 (the
           server answered that it has no such ROM or device id) is ``not_found``
           — distinct so the copy surface never reports an answering server
           offline (#1570) — and any other RomM answer ``classify_error`` does
           not call unreachable is ``copy_failed`` with its message.
        9. The copy proper runs in :meth:`_copy_save_to_slot_io`.

        A dedup pre-check between the ``list_saves`` and the copy short-circuits
        with ``already_present{existing_id}`` when the chosen save's content is
        already in the target slot — copying it would only churn the tracked save
        (RomM dedups server-side).

        Returns a discriminated-status dict (mirrors ``RollbackStatus``):
        ``ok | already_present{existing_id} | not_configured | invalid_slot_name |
        rom_not_installed | version_deleted | unsupported{reason?} |
        server_unreachable{message} | not_found{message} | conflict_blocked{conflicts} |
        preflight_failed{errors} | target_slot_busy{message} | copy_failed{message} |
        upload_failed{message}``.
        """
        rom_id = int(rom_id)
        save_id = int(save_id)

        target_slot = str(target_slot).strip()
        if not target_slot:
            return {"status": "invalid_slot_name"}

        async with self._sync_engine.rom_lock(rom_id):
            await self._sync_engine.refuse_while_running(rom_id)
            save_state = await self._loop.run_in_executor(None, read_save_state_or_new, self._uow_factory, rom_id)

            # Configured ROMs only: the current slot must be a confirmed named
            # slot. An unconfirmed slot, ``active_slot`` None, or the legacy
            # no-slot mode ("") is refused — the copy would otherwise land its
            # pre-flight/upload without a real source-slot context (#1529 lesson).
            if not (save_state.slot_confirmed and save_state.active_slot):
                self._log_debug(f"copy_save_to_slot: rom {rom_id} not configured; refusing")
                return {"status": "not_configured"}

            info = await self._loop.run_in_executor(None, self._rom_info.get_rom_save_info, rom_id)
            if not info:
                return {"status": "rom_not_installed"}
            await self._sync_engine.follow_save_directory(rom_id, info["save_answer"])

            # Interim #908 guard: a multi-file slot (e.g. Saturn .bkr/.bcr/.smpc)
            # is one game state across N files — copying a single save into a slot
            # would produce an incoherent set. Local-only (no network): a copy
            # source is always installed, so its component files are on disk.
            component_files = await self._loop.run_in_executor(None, self._local_component_filenames, rom_id)
            if compute_multi_file_slot(component_files).is_multi_file:
                self._log_debug(f"copy_save_to_slot: multi-file slot for rom {rom_id} ({component_files}); refusing")
                return {"status": "unsupported"}

            # The emulator writes this game's save beside its content, which the
            # sync leaves alone, so the copy's download/POST could never take
            # effect. Refuse before any preflight or destructive I/O.
            if info["save_answer"].in_content_directory:
                self._log_debug(f"copy_save_to_slot: rom {rom_id} saves beside its content; refusing")
                return {"status": "unsupported", "reason": SAVE_SYNC_CONTENT_DIR_REASON}
            # Any other answer a sync would not carry is refused for what it is:
            # a copy written where a sync never reads is where the emulator may
            # never look, or — for a save inside the game file — into the ROM's
            # own folder.
            if info["save_answer"].sync_directory is None:
                self._log_debug(f"copy_save_to_slot: rom {rom_id} has no save a sync could carry; refusing")
                return {
                    "status": "unsupported",
                    "reason": SAVE_SHAPE_UNSUPPORTED_REASON,
                    "message": save_shape_message(info["save_answer"]),
                    **save_finding_details(info["save_answer"]),
                }

            core_so = await self._loop.run_in_executor(None, self._resolve_core, rom_id)
            default_slot = resolve_default_slot(self._settings)
            device_id = await self._sync_engine.live_device_id()

            # Matrix pre-flight on the CURRENT slot: get its tracked save in sync
            # first (or surface a conflict), so the download below never overwrites
            # a dirty local that lives nowhere else.
            _uploaded, _downloaded, errors, conflicts = await self._loop.run_in_executor(
                None, self._sync_engine.do_sync_rom_saves, rom_id, save_state, device_id, core_so, default_slot
            )
            if conflicts:
                await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)
                return {"status": "conflict_blocked", "conflicts": list(conflicts)}
            if errors:
                await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)
                return {"status": "preflight_failed", "errors": errors}

            # No slot filter: the chosen save may live in ANY slot (a named slot or
            # the legacy no-slot bucket), so list everything and find it by id.
            try:
                server_saves: list[dict[str, Any]] = await self._loop.run_in_executor(
                    None,
                    lambda: self._retry.with_retry(lambda: self._romm_api.list_saves(rom_id, device_id=device_id)),
                )
            except RommNotFoundError as e:
                # The server ANSWERED: it has no such ROM (or no such device id).
                # ``list_saves`` is rom- AND device-scoped, so this is the #1560
                # family. Not a connectivity verdict — the copy surface must not
                # report the server offline (#1570).
                self._log_debug(f"copy_save_to_slot: server has no such entity: {e}")
                # Persist whatever the pre-flight mutated before bailing.
                await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)
                return {"status": "not_found", "message": str(e)}
            except RommApiError as e:
                self._log_debug(f"copy_save_to_slot: failed to list saves: {e}")
                # Persist whatever the pre-flight mutated before bailing.
                await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)
                reason, message = classify_error(e)
                if reason == "server_unreachable":
                    return {"status": "server_unreachable", "message": str(e)}
                return {"status": "copy_failed", "message": message}
            except Exception as e:
                self._log_debug(f"copy_save_to_slot: failed to list saves: {e}")
                # Persist whatever the pre-flight mutated before bailing.
                await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)
                return {"status": "server_unreachable", "message": str(e)}

            # Dedup pre-check: if the chosen save's content is already present in
            # the target slot, a copy would make RomM dedup server-side and
            # the upload would adopt the pre-existing save as current —
            # churning the tracked save for no gain (which surfaced the current
            # save in its own version history). Detect it up front via the
            # content_hash RomM populates on each save and refuse without touching
            # any copy state. A missing source content_hash skips the check; the
            # copy then falls through and RomM's own server-side dedup applies.
            existing_id = self._find_already_present(server_saves, save_id, target_slot)
            if existing_id is not None:
                self._log_debug(
                    f"copy_save_to_slot: rom {rom_id} save {save_id} already present in slot "
                    f"{target_slot!r} as {existing_id}; skipping copy"
                )
                # The pre-flight may have performed real uploads/downloads on the
                # current slot; persist those baselines even though the copy is a
                # no-op (#1012). No copy state was mutated.
                await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)
                return {"status": "already_present", "existing_id": existing_id}

            try:
                result = await self._loop.run_in_executor(
                    None,
                    self._copy_save_to_slot_io,
                    rom_id,
                    save_state,
                    device_id,
                    core_so,
                    save_id,
                    target_slot,
                    info,
                    server_saves,
                    default_slot,
                )
            finally:
                # The pre-flight (and the copy's own download/upload) mutate the
                # in-memory aggregate; persist regardless of how the copy ends or
                # the next sync mis-classifies (#1012).
                await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)

            return result
