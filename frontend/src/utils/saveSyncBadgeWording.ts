/** What the SAVE SYNC badge says; `saveSyncBadge` decides which. */

export const SAVE_SYNC_OFF_LABEL = "Disabled";
export const SAVE_CONFLICT_LABEL = "Save conflict";
export const SLOT_NOT_CONFIRMED_LABEL = "Slot not confirmed";
export const ROMM_UNAVAILABLE_LABEL = "RomM unavailable";
export const NOT_CHECKED_LABEL = "Not checked";
export const COULD_NOT_CHECK_LABEL = "Couldn't check RomM";
export const SAVED_ON_ROMM_LABEL = "Saved on RomM";
export const CANNOT_SYNC_LABEL = "Can't sync";

/** The badge's word for an offline local save with no sync recorded on its rows. */
export const NOT_SYNCED_LABEL = "Not synced";

/** "Just now" reads "Synced just now", "3d ago" reads "Synced 3d ago". */
export function syncedLabel(label: string): string {
  return `Synced ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}
