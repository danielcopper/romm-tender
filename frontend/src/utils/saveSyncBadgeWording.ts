/** What the SAVE SYNC badge says; `saveSyncBadge` decides which. */

export const SAVE_SYNC_OFF_LABEL = "Disabled";
export const SAVE_CONFLICT_LABEL = "Save conflict";
export const SLOT_NOT_CONFIRMED_LABEL = "Slot not confirmed";
export const ROMM_UNAVAILABLE_LABEL = "RomM unavailable";
export const NOT_CHECKED_LABEL = "Not checked";
export const COULD_NOT_CHECK_LABEL = "Couldn't check RomM";
export const SAVED_ON_ROMM_LABEL = "Saved on RomM";
export const CANNOT_SYNC_LABEL = "Can't sync";

/** `resolveSaveSyncLabel`'s and the backend's word for a save at rest with no sync recorded. */
export const NOT_SYNCED_LABEL = "Not synced";

/** "Just now" reads "Synced just now", "3d ago" reads "Synced 3d ago"; no sync recorded stays as it is. */
export function syncedLabel(label: string): string {
  if (label === NOT_SYNCED_LABEL) return label;
  return `Synced ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}
