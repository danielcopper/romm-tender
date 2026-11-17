"""Save-sync subsystem.

The package's public API is the ``SaveService`` aggregate root — it holds
the save-sync sub-services over the SQLite ``rom_save_sync_states`` aggregate
and exposes the use cases called by the endpoints on ``Endpoints``. Its parts
are built only by the composition root (``bootstrap/saves.py``), from this
package's re-exports and ``sync_engine``'s, and handed to the parts that hold
them. RomM communication goes through Protocol-typed
adapters.
"""

from services.saves._config import SaveServiceConfig
from services.saves.copies import SaveCopyService, SaveCopyServiceConfig
from services.saves.local_saves import LocalSaveFiles, LocalSaveFilesConfig
from services.saves.prune_support import PruneSaveSupport, PruneSaveSupportConfig
from services.saves.rom_info import RomInfoService, RomInfoServiceConfig
from services.saves.save_backup import SaveBackup, SaveBackupConfig
from services.saves.save_transfer import SaveTransfer, SaveTransferConfig
from services.saves.service import SaveService
from services.saves.slots import SetupWizard, SlotDeleter, SlotListing, SlotSwitcher
from services.saves.status import StatusService, StatusServiceConfig
from services.saves.versions import VersionsService, VersionsServiceConfig

__all__ = [
    "LocalSaveFiles",
    "LocalSaveFilesConfig",
    "PruneSaveSupport",
    "PruneSaveSupportConfig",
    "RomInfoService",
    "RomInfoServiceConfig",
    "SaveBackup",
    "SaveBackupConfig",
    "SaveCopyService",
    "SaveCopyServiceConfig",
    "SaveService",
    "SaveServiceConfig",
    "SaveTransfer",
    "SaveTransferConfig",
    "SetupWizard",
    "SlotDeleter",
    "SlotListing",
    "SlotSwitcher",
    "StatusService",
    "StatusServiceConfig",
    "VersionsService",
    "VersionsServiceConfig",
]
