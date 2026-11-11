"""The save sync's engine: the newest-wins matrix that decides each file and
dispatches the decision, and the rom-level orchestration around it.
"""

from services.saves.sync_engine._gate import SaveSyncGate, SaveSyncTimeoutError
from services.saves.sync_engine._pre_launch import PreLaunchSyncStopped
from services.saves.sync_engine._sweep_stop import SaveSweepIncomplete
from services.saves.sync_engine.devices import DeviceRegistry
from services.saves.sync_engine.engine import MatrixOutcome, SyncEngine, SyncEngineConfig
from services.saves.sync_engine.matrix import MatrixExecutor

__all__ = [
    "DeviceRegistry",
    "MatrixExecutor",
    "MatrixOutcome",
    "PreLaunchSyncStopped",
    "SaveSweepIncomplete",
    "SaveSyncGate",
    "SaveSyncTimeoutError",
    "SyncEngine",
    "SyncEngineConfig",
]
