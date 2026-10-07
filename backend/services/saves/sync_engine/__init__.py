"""Newest-wins matrix executor and the I/O orchestrators that perform
sync transfers.

Anything that decides "which side wins for this file" or actually
moves bytes between local saves_dir and the RomM server lives here.
Read-only matrix consumption (status reporting) belongs in
StatusService; persistence is owned by the public use case's narrow
Unit of Work (ADR-0006).
"""

from services.saves.sync_engine._gate import SaveSyncGate, SaveSyncTimeoutError
from services.saves.sync_engine._sweep_stop import SaveSweepIncomplete
from services.saves.sync_engine.engine import MatrixOutcome, SyncEngine, SyncEngineConfig

__all__ = [
    "MatrixOutcome",
    "SaveSweepIncomplete",
    "SaveSyncGate",
    "SaveSyncTimeoutError",
    "SyncEngine",
    "SyncEngineConfig",
]
