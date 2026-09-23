"""Save-sync subsystem.

The package's public API is the ``SaveService`` aggregate root — composes
the save-sync sub-services (sync_engine, status, versions, slots, rom_info)
over the SQLite ``rom_save_sync_states`` aggregate and exposes the callable
surface consumed by the callables on ``Plugin``. RomM communication goes
through Protocol-typed adapters.
"""

from services.saves._config import SaveServiceConfig
from services.saves.service import SaveService

__all__ = ["SaveService", "SaveServiceConfig"]
