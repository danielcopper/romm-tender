"""LaunchGateService — whether a ROM's local save files have drifted from their sync baseline.

The check is purely local: it hashes the save files on disk, compares each
with the baseline the last sync recorded for it, and never asks the server, so
it answers while the server is unreachable. A drifted ROM holds local save
changes no sync has uploaded yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    import logging

    from services.protocols.cross_service import LaunchGateDriftReader
    from services.protocols.files import SaveFileStore


@dataclass(frozen=True)
class LaunchGateServiceConfig:
    """Frozen wiring bundle handed to ``LaunchGateService.__init__``.

    ``drift_reader`` enumerates a ROM's local save files and their persisted
    baselines. ``save_file_store`` hashes each file, and the hashing runs on
    ``loop``'s executor because it blocks. ``logger`` records the internal
    errors :meth:`LaunchGateService.check_local_drift` collapses to not-drifted.
    """

    drift_reader: LaunchGateDriftReader
    save_file_store: SaveFileStore
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class LaunchGateService:
    """The local-drift check: do a ROM's save files on disk differ from their sync baseline."""

    def __init__(self, *, config: LaunchGateServiceConfig) -> None:
        self._drift_reader = config.drift_reader
        self._save_file_store = config.save_file_store
        self._loop = config.loop
        self._logger = config.logger

    async def check_local_drift(self, rom_id: int) -> dict[str, Any]:
        """Report whether the ROM's local save files diverge from their sync baseline.

        Used by the offline launch path: when the server is unreachable we
        cannot run a real sync, so this purely-local probe warns the user that
        an out-of-band local change would otherwise be silently overwritten the
        next time sync succeeds.

        Enumerates the ROM's local save files the same way the sync/status path
        does (``find_local_save_files`` → the shared ``RomInfoService``
        discovery), hashes each present file (the zip-aware RomM-parity
        ``content_hash`` via the injected ``SaveFileStore``, run on the executor
        — the same scheme the sync baseline is written with, so a zip save's
        drift check stays consistent), and compares it to that file's persisted
        ``last_sync_hash``. ``drifted`` is ``True`` when any present
        file's current hash differs from its non-``None`` baseline. A file with
        no baseline yet (``last_sync_hash is None``) is NOT drift — there is no
        recorded state to diverge from. A ROM that is not installed or has no
        tracked files reports ``drifted: False``.

        Never raises: any internal error (file vanished mid-hash, repository
        read failure, …) collapses to ``drifted: False``. A false offline
        warning is worse than skipping it — treat the unknown as not-drifted.
        """
        rom_id = int(rom_id)
        try:
            return await self._loop.run_in_executor(None, self._check_local_drift_io, rom_id)
        except Exception as e:
            self._logger.warning(f"LaunchGate drift check failed for rom_id={rom_id}: {e}")
            return {"drifted": False, "rom_id": rom_id}

    def _check_local_drift_io(self, rom_id: int) -> dict[str, Any]:
        """Synchronous drift worker — runs on the executor thread.

        Enumerates local save files + per-file baselines and hashes each
        present file. Returns the ``{"drifted", "rom_id"}`` shape. Raised
        exceptions propagate to :meth:`check_local_drift`, which collapses them
        to ``drifted: False``.
        """
        local_files = self._drift_reader.find_local_save_files(rom_id)
        if not local_files:
            return {"drifted": False, "rom_id": rom_id}

        baselines = self._drift_reader.last_sync_hashes(rom_id)
        for entry in local_files:
            filename = entry["filename"]
            baseline = baselines.get(filename)
            if baseline is None:
                # No recorded baseline → nothing to diverge from (not drift).
                continue
            current = self._save_file_store.content_hash(entry["path"])
            if current != baseline:
                return {"drifted": True, "rom_id": rom_id}

        return {"drifted": False, "rom_id": rom_id}
