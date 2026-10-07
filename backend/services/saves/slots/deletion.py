"""Slot deletion: local state cleanup + server-side save removal.

Anything that tears down an existing slot — surfacing what the delete
will do to the confirmation modal, deleting the slot's server-side
saves, and cleaning the local file-tracking entries that point at them
— lives here. Slot listing, active-slot switching, and the first-sync
setup wizard belong in their own sub-modules. Persistence is the
operation's own narrow Unit of Work (ADR-0006).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.save_slot import save_in_slot, slot_query_param
from lib.errors import NotInstalled, Refused
from services.saves._messages import SAVE_SYNC_DISABLED
from services.saves._save_state import read_save_state, write_save_state
from services.saves._settings import save_sync_enabled

if TYPE_CHECKING:
    import asyncio

    from domain.rom_save_sync_state import RomSaveSyncState
    from services.protocols import (
        DebugLogger,
        RetryStrategy,
        RommSaveApi,
        UnitOfWorkFactory,
    )
    from services.saves.rom_info import RomInfoService
    from services.saves.sync_engine import SyncEngine
    from services.saves.sync_engine.devices import DeviceRegistry


class SlotDeleter:
    """Slot deletion: validates the request, deletes server saves, cleans local state."""

    def __init__(
        self,
        *,
        settings: dict[str, Any],
        uow_factory: UnitOfWorkFactory,
        device_registry: DeviceRegistry,
        rom_info: RomInfoService,
        romm_api: RommSaveApi,
        retry: RetryStrategy,
        loop: asyncio.AbstractEventLoop,
        log_debug: DebugLogger,
        sync_engine: SyncEngine,
    ) -> None:
        self._settings = settings
        self._uow_factory = uow_factory
        self._device_registry = device_registry
        self._rom_info = rom_info
        self._romm_api = romm_api
        self._retry = retry
        self._loop = loop
        self._log_debug = log_debug
        self._sync_engine = sync_engine

    def _validate_slot_operation(self, rom_id: int, slot: str) -> tuple[RomSaveSyncState, dict[str, dict[str, Any]]]:
        """Shared validation for slot delete operations.

        Refuses with ``disabled`` while save sync is off, ``not_installed``, or
        ``not_found`` when the ROM has no save state or no such slot; otherwise
        returns the loaded aggregate and its slots. Callers that mutate the
        aggregate must persist it via :func:`write_save_state`.
        """
        if not save_sync_enabled(self._settings):
            raise Refused("disabled", SAVE_SYNC_DISABLED)
        if not self._rom_info.is_content_installed(rom_id):
            raise NotInstalled("ROM is not installed")
        save_state = read_save_state(self._uow_factory, rom_id)
        if save_state is None:
            raise Refused("not_found", "No save state found")
        slots_dict: dict[str, dict[str, Any]] = save_state.slots
        if slot not in slots_dict:
            raise Refused("not_found", "Slot not found")
        return save_state, slots_dict

    async def get_slot_delete_info(self, rom_id: int, slot: str) -> dict[str, Any]:
        """Return info about what deleting a slot would do, for the confirmation modal.

        Refuses as :meth:`_validate_slot_operation` does. A RomM error listing a
        server slot's saves propagates.
        """
        rom_id = int(rom_id)
        slot = str(slot).strip() if slot else ""

        save_state, slots_dict = await self._loop.run_in_executor(None, self._validate_slot_operation, rom_id, slot)

        slot_info = slots_dict[slot]
        source = slot_info.get("source", "server")
        active_slot = save_state.active_slot
        is_active = slot == (active_slot or "")

        # Server save count
        server_save_ids: list[int] = []
        if source == "server":
            device_id = await self._loop.run_in_executor(None, self._device_registry.get_device_id)
            # Legacy slot ("" → null on the server) is addressed by omitting
            # ``slot=`` and filtering client-side (#1061), so a legacy delete
            # inspects ONLY the null saves. Named slots filter server-side and
            # re-filter for safety. A failed listing is never answered as "0
            # server saves": the confirmation modal would then offer a
            # destructive wipe of a slot nobody inspected.
            server_saves: list[dict[str, Any]] = await self._loop.run_in_executor(
                None,
                lambda: self._retry.with_retry(
                    lambda: self._romm_api.list_saves(rom_id, device_id=device_id, slot=slot_query_param(slot)),
                ),
            )
            server_save_ids = [s["id"] for s in server_saves if save_in_slot(s, slot)]

        # Local tracked files pointing to server saves in this slot
        files_state = save_state.files
        local_filenames: list[str] = []
        if server_save_ids:
            id_set = set(server_save_ids)
            for filename, fstate in files_state.items():
                if fstate.tracked_save_id in id_set:
                    local_filenames.append(filename)

        return {
            "success": True,
            "slot": slot,
            "source": source,
            "server_save_count": len(server_save_ids),
            "server_save_ids": server_save_ids,
            "local_file_count": len(local_filenames),
            "local_filenames": local_filenames,
            "is_active": is_active,
        }

    async def _delete_server_slot_saves(self, rom_id: int, slot: str) -> list[int]:
        """Delete all server saves in a slot and return their ids; a RomM error propagates."""
        device_id = await self._loop.run_in_executor(None, self._device_registry.get_device_id)
        # Legacy slot ("" → null on the server) deletes ONLY the null saves:
        # omit ``slot=`` and filter client-side (#1061). Named slots filter
        # server-side and re-filter for safety.
        server_saves: list[dict[str, Any]] = await self._loop.run_in_executor(
            None,
            lambda: self._retry.with_retry(
                lambda: self._romm_api.list_saves(rom_id, device_id=device_id, slot=slot_query_param(slot)),
            ),
        )
        save_ids = [s["id"] for s in server_saves if save_in_slot(s, slot)]
        if save_ids:
            await self._loop.run_in_executor(
                None,
                lambda: self._retry.with_retry(
                    lambda: self._romm_api.delete_server_saves(save_ids),
                ),
            )
        return save_ids

    async def delete_slot(self, rom_id: int, slot: str) -> dict[str, Any]:
        """Delete a save slot and all its saves (local state + server if applicable).

        The slot-less legacy bucket is read-only (#1478): an empty /
        whitespace-only / ``None`` slot name is the slot-less bucket — a manual
        upload or an older web player's save — which is managed in the RomM web
        app and must never be torn down from here.
        It is refused up front with ``invalid_slot_name`` — before any lock
        acquisition, server I/O, or state change — mirroring the switch-target
        refusal in :class:`SlotSwitcher` (#1276). The active slot refuses with
        ``active_slot``; otherwise it refuses as :meth:`_validate_slot_operation`
        does, and a RomM error deleting the server saves propagates with the
        slot left as it was.
        """
        rom_id = int(rom_id)
        slot = str(slot).strip() if slot else ""

        # Refuse the legacy bucket before touching the lock or the wire (#1478):
        # deleting "" would wipe the game's entire slot-less bucket. Named slots
        # fall through to the normal delete flow.
        if not slot:
            raise Refused("invalid_slot_name", "The legacy bucket is read-only and cannot be deleted")

        # The read→delete-server→mutate→write of the RomSaveSyncState aggregate must
        # serialise against every other path that touches this ROM's state.
        # _delete_server_slot_saves does NOT acquire rom_lock, so calling it
        # inside the held lock is safe (no re-entry). There is no tail status
        # call, so no deadlock risk.
        async with self._sync_engine.rom_lock(rom_id):
            save_state, slots_dict = await self._loop.run_in_executor(None, self._validate_slot_operation, rom_id, slot)

            effective_active = save_state.active_slot or ""
            if slot == effective_active:
                raise Refused("active_slot", "Cannot delete the active slot. Switch to a different slot first.")

            slot_info = slots_dict[slot]
            source = slot_info.get("source", "server")

            deleted_server_saves = 0
            cleaned_files = 0
            deleted_ids: set[int] = set()

            if source == "server":
                deleted_save_ids = await self._delete_server_slot_saves(rom_id, slot)
                deleted_server_saves = len(deleted_save_ids)
                deleted_ids = set(deleted_save_ids)

            # Clean up tracked file entries pointing to deleted saves
            files_state = save_state.files
            if deleted_ids:
                to_remove = [fn for fn, fs in files_state.items() if fs.tracked_save_id in deleted_ids]
                for fn in to_remove:
                    save_state.delete_file_tracking(fn)
                    cleaned_files += 1

            save_state.delete_slot_tracking(slot)
            await self._loop.run_in_executor(None, write_save_state, self._uow_factory, rom_id, save_state)

            return {
                "success": True,
                "deleted_server_saves": deleted_server_saves,
                "cleaned_files": cleaned_files,
            }
