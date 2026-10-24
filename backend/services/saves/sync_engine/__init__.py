"""The save sync's engine: the newest-wins matrix that decides each file and
dispatches the decision, and the rom-level orchestration around it.

Moving a file between saves_dir and RomM is ``services/saves/save_transfer.py``'s,
backing one up ``services/saves/save_backup.py``'s. Read-only matrix
consumption (status reporting) belongs in StatusService; persistence is owned
by the public use case's narrow Unit of Work (ADR-0006).
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
