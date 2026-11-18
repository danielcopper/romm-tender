"""User-facing message constants for the saves package.

All status and error message strings carried in the ``message`` of
save-sync answers and refusals live here so they stay
consistent across modules. Add to this file rather than inlining new
literals in service code.
"""

SAVE_SYNC_DISABLED = "Save sync is disabled"
DEVICE_NOT_REGISTERED = "Device not registered with RomM"
# The wizard's legacy migration (``confirm_slot_choice``).
MIGRATION_LOCAL_FILES_FAILED = (
    "The saves could not be migrated: a save file on this device could not be read or written,"
    " or its folder could not be created."
)
DEVICE_SYNC_DISABLED = "Save sync is disabled for this device on the RomM server"
SAVE_SYNC_BUSY = "Another save sync is still running"
ROM_GONE_FROM_ROMM = "RomM no longer has this game — its saves stay on this device"
# Sync All Saves Now's clause naming the games RomM no longer has: one, then several.
GONE_FROM_ROMM_ONE = "{name} is no longer on RomM — its saves stay on this device"
GONE_FROM_ROMM_SEVERAL = "{names} are no longer on RomM — their saves stay on this device"
# The clause naming the games a run over several ROMs passed over because they were running: one, then several.
RUNNING_SKIPPED_ONE = "{name} was skipped because it is running"
RUNNING_SKIPPED_SEVERAL = "{names} were skipped because they are running"
# A save Tender could otherwise sync sits beside the content file
# (``SaveAnswer.in_content_directory``; RetroArch's ``savefiles_in_content_dir``
# is the usual cause), outside what Tender syncs. Neutral phrasing that
# names no emulator — the frontend treats this as a benign skip.
SAVE_SYNC_IN_CONTENT_DIR = "Save sync is unavailable: saves are written to the game's content directory."
