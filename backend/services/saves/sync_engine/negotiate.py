"""RomM's ``negotiate`` session around a save sync, and the inventory it sends.

Anything that opens or closes the transport-only ``negotiate`` session lives
here: the session one confirmed ROM's sync opens for itself, the one
whole-device session a sweep opens, and the local-save inventory either POSTs.
The session is an envelope and never a gate (ADR-0017): the local
``compute_sync_action`` matrix decides every file without it, the planned
``operations`` RomM answers with are discarded, and a session that cannot open
degrades to a sync without one. The one exception is RomM's per-device
sync-disabled answer (:class:`RommSyncDisabledError`), which is re-raised so
the run stops with that policy reason.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.iso_time import epoch_to_iso
from lib.errors import RommSyncDisabledError

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.live_device_id import LiveDeviceId
    from domain.save_answer import SaveAnswer
    from models.sync import ClientSaveState
    from services.protocols import (
        DebugLogger,
        RetryStrategy,
        RommSyncApi,
        SaveFileStore,
        UnitOfWorkFactory,
    )
    from services.saves.rom_info import RomInfoService


__all__ = ["NEGOTIATE_ROM_IDS_CAP", "NegotiateEnvelope", "NegotiateEnvelopeConfig"]

# RomM 5.3.0's ``POST /api/sync/negotiate`` takes at most this many ``rom_ids``
# and answers a longer list with 422 (rommapp/romm#4304).
NEGOTIATE_ROM_IDS_CAP = 500


@dataclass(frozen=True)
class NegotiateEnvelopeConfig:
    """Frozen wiring bundle handed to ``NegotiateEnvelope.__init__``.

    Holds the Unit-of-Work factory the inventory reads the confirmed slots
    through, the peer rom_info that finds each ROM's local save files, the
    Protocol-typed filesystem adapter that measures them, the Protocol-typed
    RomM adapter and retry strategy the session requests go through, and
    runtime infrastructure (loop, logger, ``DebugLogger``).
    """

    uow_factory: UnitOfWorkFactory
    rom_info: RomInfoService
    save_file_store: SaveFileStore
    romm_api: RommSyncApi
    retry: RetryStrategy
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    log_debug: DebugLogger


class NegotiateEnvelope:
    """Opens and closes RomM's negotiate session around a sync, and builds the inventory it sends."""

    def __init__(self, *, config: NegotiateEnvelopeConfig) -> None:
        self._uow_factory = config.uow_factory
        self._rom_info = config.rom_info
        self._save_file_store = config.save_file_store
        self._romm_api = config.romm_api
        self._retry = config.retry
        self._loop = config.loop
        self._logger = config.logger
        self._log_debug = config.log_debug

    def build_inventory(
        self, rom_id: int | None = None, *, save_answer: SaveAnswer | None = None
    ) -> list[ClientSaveState]:
        """Build the negotiate inventory of this device's local save files.

        Gathers one :class:`ClientSaveState` per local save file belonging to a
        ROM whose slot is **confirmed** and whose ``active_slot`` is a real
        (non-legacy) slot — ``slot_confirmed`` is true and ``active_slot`` is
        truthy (excludes both ``None`` and the legacy ``""``). A confirmed ROM
        with no local files contributes nothing; per-file granularity means
        each local file yields its own entry.

        ``rom_id`` scopes the inventory: ``None`` (the default) builds the
        whole-device inventory for the sweep's session; a concrete id restricts
        it to that one ROM for its own session. The in-scope predicate is
        unchanged either way.

        ``save_answer`` is that one ROM's save reading where the caller already
        took one, and it spares this build a second live reading of the machine.
        It applies to the scoped form only: the whole-device inventory runs
        BEFORE the sweep's per-ROM loop, so no answer exists yet for any of the
        ROMs it walks, and one ROM's answer says nothing about another's.

        ``content_hash`` is always set via :meth:`SaveFileStore.content_hash`
        (the zip-aware RomM-parity hash — never ``checksum_md5``), and
        ``updated_at`` is the local file's mtime rendered as a UTC ISO-8601
        string.

        Single-file-first: the multi-file-per-slot collision case (several local
        files mapping to one slot) is not handled.
        """
        with self._uow_factory() as uow:
            confirmed = [
                (rid, state)
                for rid, state in uow.rom_save_sync_states.iter_all()
                if state.slot_confirmed and state.active_slot and (rom_id is None or rid == rom_id)
            ]

        inventory: list[ClientSaveState] = []
        for rid, state in confirmed:
            scoped_answer = save_answer if rom_id is not None and rid == rom_id else None
            for f in self._rom_info.find_save_files(rid, save_answer=scoped_answer):
                path = f["path"]
                entry: ClientSaveState = {
                    "rom_id": rid,
                    "file_name": f["filename"],
                    "slot": state.active_slot,
                    "emulator": state.emulator,
                    "content_hash": self._save_file_store.content_hash(path),
                    "updated_at": epoch_to_iso(self._save_file_store.get_mtime(path)),
                    "file_size_bytes": self._save_file_store.get_size(path),
                }
                inventory.append(entry)
        return inventory

    async def open_rom_session(
        self, rom_id: int, device_id: LiveDeviceId, save_answer: SaveAnswer | None = None
    ) -> int | None:
        """Open the session one confirmed ROM's sync runs in; ``None`` when it could not open.

        POSTs the ROM-scoped inventory to ``negotiate``, with that one ROM as its
        ``rom_ids`` scope, and keeps only the ``session_id``. Any failure
        degrades to ``None`` — the sync run proceeds without a session envelope
        rather than aborting — EXCEPT :class:`RommSyncDisabledError`, which is
        re-raised. An unclosed session lingers harmlessly until this device's
        next ``negotiate`` cancels it, so a missed close is harmless.

        *save_answer* is the reading the run already took, handed on so the
        inventory does not take a second one. Without it a ROM whose slot the
        user has confirmed costs two live readings of the machine per sync
        rather than one.
        """
        try:
            inventory = await self._loop.run_in_executor(
                None, functools.partial(self.build_inventory, rom_id, save_answer=save_answer)
            )
            response = await self._loop.run_in_executor(
                None,
                lambda: self._retry.with_retry(
                    lambda: self._romm_api.negotiate_sync(device_id, inventory, rom_ids=[rom_id])
                ),
            )
            return response["session_id"]
        except RommSyncDisabledError:
            raise
        except Exception as e:
            self._log_debug(f"_run_rom_sync({rom_id}): negotiate session open failed ({e}) — syncing without a session")
            return None

    async def open_device_session(self, device_id: LiveDeviceId) -> int | None:
        """Open the one whole-device session a sweep runs in.

        Builds the full inventory (every confirmed non-legacy ROM with local
        saves) and POSTs ``negotiate`` once, keeping only the ``session_id``.
        Returns it, or ``None`` on an empty inventory (nothing to negotiate) or
        any negotiate failure. When ``None``, each confirmed non-legacy ROM
        opens its own session (:meth:`open_rom_session`) — the sweep degrades,
        never aborts; only the session envelope (one shared vs. per-ROM)
        differs. :class:`RommSyncDisabledError` is re-raised, so the whole sweep
        stops.

        The POST names the inventory's distinct ROM ids, in first-seen order, as
        its ``rom_ids`` scope. Over :data:`NEGOTIATE_ROM_IDS_CAP` of them it goes
        out unscoped and says so in the debug log: splitting it is no way out,
        because each negotiate cancels this device's prior sessions, the earlier
        parts included.
        """
        full_inventory = await self._loop.run_in_executor(None, self.build_inventory, None)
        if not full_inventory:
            return None
        rom_ids: list[int] | None = list(dict.fromkeys(save["rom_id"] for save in full_inventory))
        if len(rom_ids) > NEGOTIATE_ROM_IDS_CAP:
            self._log_debug(
                f"sync_all_saves: the inventory names {len(rom_ids)} ROMs, over negotiate's cap of "
                f"{NEGOTIATE_ROM_IDS_CAP}; negotiating without a rom_ids scope"
            )
            rom_ids = None
        try:
            response = await self._loop.run_in_executor(
                None,
                lambda: self._retry.with_retry(
                    lambda: self._romm_api.negotiate_sync(device_id, full_inventory, rom_ids=rom_ids)
                ),
            )
            # Read the key INSIDE the guard: a 200 body missing session_id must
            # degrade like a failure (session_id None → per-ROM sessions), not
            # abort the whole sweep.
            return response["session_id"]
        except RommSyncDisabledError:
            raise
        except Exception as e:
            self._logger.warning("sync_all_saves: negotiate session open failed (%s) — per-ROM sessions", e)
            return None

    async def close_session(self, session_id: int, completed: int, failed: int) -> None:
        """Close a negotiate session, reporting op counts (non-fatal).

        Swallows any failure: a session the server never hears closed lingers
        until this device's next ``negotiate`` cancels it, so a failed close
        must never fail the sync run.
        """
        try:
            await self._loop.run_in_executor(
                None,
                lambda: self._romm_api.complete_sync_session(
                    session_id, operations_completed=completed, operations_failed=failed
                ),
            )
        except Exception as e:
            self._log_debug(f"complete_sync_session({session_id}) failed (non-fatal): {e}")
