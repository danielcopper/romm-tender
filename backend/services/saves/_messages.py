"""User-facing message constants for the saves package.

All status and error message strings carried in the ``message`` of
save-sync answers and refusals live here so they stay
consistent across modules. Add to this file rather than inlining new
literals in service code.
"""

SAVE_SYNC_DISABLED = "Save sync is disabled"
DEVICE_NOT_REGISTERED = "Device not registered"
# Wizard legacy-migration precheck: no device is registered yet, so the migration
# can't upload into the slot. Refused before any local/server mutation so the
# wizard just stays open and the user can retry (#1498 review).
MIGRATION_DEVICE_NOT_REGISTERED = (
    "This device isn't registered with RomM yet — retry in a moment (it registers automatically on the next save sync)."
)
# Wizard legacy migration: reading or writing the save files on this device
# failed before anything was confirmed, so the wizard stays open for a retry.
MIGRATION_LOCAL_FILES_FAILED = (
    "The saves could not be migrated: a save file on this device could not be read or written."
)
DEVICE_SYNC_DISABLED = "Save sync is disabled for this device on the RomM server"
SAVE_SYNC_BUSY = "Another save sync is still running"
# A save Tender could otherwise sync sits beside the content file
# (``SaveAnswer.in_content_directory``; RetroArch's ``savefiles_in_content_dir``
# is the usual cause), outside what Tender syncs. Neutral phrasing that
# names no emulator — the frontend treats this as a benign skip.
SAVE_SYNC_IN_CONTENT_DIR = "Save sync is unavailable: saves are written to the game's content directory."
