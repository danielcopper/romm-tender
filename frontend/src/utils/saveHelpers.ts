/**
 * Pure formatters and selectors for the save rows and the slot lists: anything
 * that takes inputs and returns outputs without touching component state or
 * React. Rendering stays with the components; the slot surfaces' feedback,
 * failure and confirmation sentences are `saveSlotWording.ts`'s.
 */

import type { DeviceSyncInfo, SaveStatus, SyncConflict, SaveSlotSummary } from "../types";
import { NO_SAVE_YET } from "./saveSyncStatement";

export const MUTED_COLOR = "#8f98a0";

/** Display a slot name, labelling the null/empty slot with RomM's own name for a
 *  save uploaded without a slot: `upload-slot-hint` in RomM's
 *  `frontend/src/locales/en_US/rom.json`, at 5.3.1. */
export function displaySlot(slot: string | null | undefined): string {
  if (slot === null || slot === undefined || slot === "") return "Manual archive";
  return slot;
}

/**
 * A save-sync time as the Saves tab and its toasts show it — a save's, a
 * version's, or the last sync's: "just now", "5m ago", "2h ago", then a date
 * ("5 Oct"); "" for no time or a bad one. Elsewhere, `formatTimeAgo`
 * (`formatters.ts`).
 */
export function formatSaveTimeAgo(isoStr: string | null): string {
  if (!isoStr) return "";
  const date = new Date(isoStr);
  if (Number.isNaN(date.getTime())) return "";
  const diffMs = Date.now() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffMin < 1440) return `${Math.floor(diffMin / 60)}h ago`;
  const d = date.getDate();
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[date.getMonth()]}`;
}

/** Pick the most recently synced device from a device_syncs array, or null */
export function pickLastSyncer(syncs: DeviceSyncInfo[] | undefined): DeviceSyncInfo | null {
  if (!syncs || syncs.length === 0) return null;
  return syncs.reduce<DeviceSyncInfo | null>((latest, ds) => {
    if (!latest) return ds;
    if (!ds.last_synced_at) return latest;
    if (!latest.last_synced_at) return ds;
    return ds.last_synced_at > latest.last_synced_at ? ds : latest;
  }, null);
}

/**
 * Return an attribution label based on the uploaded_by_us flag, or null if unknown.
 * NOTE: "this device" is really "this Tender installation": the flag asks
 * whether this install's recorded upload ids (`own_upload_ids`) include the
 * save, so a data root copied to another machine would claim local ownership
 * there too.
 */
export function attributionLabel(uploadedByUs: boolean | null | undefined): string | null {
  if (uploadedByUs === true) return "(this device)";
  if (uploadedByUs === false) return "(not this device)";
  return null;
}

/**
 * Format the per-save attribution+checkmark segment for the sync-time line.
 *
 * Combines device name, attribution label, and an optional trailing checkmark
 * into one ready-to-render string, or null when nothing meaningful can be shown.
 * Reused between `renderSaveFileRow` and `renderVersionRow`. The checkmark marks
 * an actually-synced file, so `renderSaveFileRow` passes `showCheck=false` for a
 * file with pending upload/download work — a failed post-exit sync must not
 * render a ✓ (#1334). Version rows keep the default (`showCheck=true`): the ✓
 * there is the per-version attribution marker, not a live sync verdict.
 */
export function formatAttributionSegment(
  uploadedByUs: boolean | null | undefined,
  deviceName: string | null | undefined,
  showCheck = true,
): string | null {
  const check = showCheck ? " ✓" : "";
  const label = attributionLabel(uploadedByUs);
  if (uploadedByUs === true) {
    return deviceName ? `${deviceName} ${label}${check}` : `${label}${check}`;
  }
  if (uploadedByUs === false) {
    // Intentionally no device name — the last device to sync it need not be the uploader (it is this device once it
    // has downloaded the save).
    return `${label}${check}`;
  }
  if (label === null) {
    if (deviceName) return `${deviceName}${check}`;
    return showCheck ? "✓" : null;
  }
  return null;
}

/** Map a save file status to color and label */
export function statusLabel(status: string, lastSyncAt: string | null): { color: string; label: string } {
  switch (status) {
    case "synced":
    case "skip":
      return { color: "#5ba32b", label: "Synced" };
    case "upload":
      return { color: "#d4a72c", label: "Local changes" };
    case "download":
      return { color: "#1a9fff", label: "Server newer" };
    case "conflict":
      return { color: "#d94126", label: "Conflict" };
    case "unknown":
      return { color: "#8f98a0", label: "Status unknown" };
    default:
      if (lastSyncAt) return { color: "#5ba32b", label: "Synced" };
      return { color: "#8f98a0", label: "Not synced" };
  }
}

/**
 * Build the active slot's sync-summary header line.
 *
 * Returns the empty (null) state for inactive slots or missing status. When
 * the backend signals `server_query_failed`, short-circuits instead of running
 * the matrix-derived classification (which would read an empty server list as
 * "ready to upload"). Only an explicit `server_query_reason` of
 * `server_unreachable` may say so in the text — on an answered 404 that would
 * be false (#1570).
 *
 * The summary is derived from the per-file matrix statuses so it can never
 * disagree with the per-file badges (`statusLabel`): any file pending upload
 * shows yellow "Local changes", any file the server has newer shows blue
 * "Server newer", and only an all-synced slot shows a green "Synced …". A
 * failed post-exit upload therefore reads as "Local changes", not a false
 * green ✓ (#1334). A slot holding no save reads *noSaveLine* (#2149 D3).
 */
export function computeSyncSummary(
  isActive: boolean,
  saveStatus: SaveStatus | null,
  conflicts: SyncConflict[],
  noSaveLine: string = NO_SAVE_YET,
): { syncSummaryText: string | null; syncSummaryColor: string } {
  if (!isActive || !saveStatus) return { syncSummaryText: null, syncSummaryColor: MUTED_COLOR };

  if (saveStatus.server_query_failed) {
    let text = "Save status unavailable";
    if (saveStatus.server_query_reason === "server_unreachable") {
      text = "Server unreachable";
    } else if (saveStatus.server_query_reason === "device_not_registered") {
      text = "Device not registered with RomM";
    }
    return { syncSummaryText: text, syncSummaryColor: MUTED_COLOR };
  }

  const hasConflict = conflicts.length > 0;
  const files = saveStatus.files;

  if (hasConflict) return { syncSummaryText: "Conflict detected", syncSummaryColor: "#d94126" };
  if (files.length === 0) return { syncSummaryText: noSaveLine, syncSummaryColor: MUTED_COLOR };
  if (files.some((f) => f.status === "upload")) {
    return { syncSummaryText: "Local changes", syncSummaryColor: "#d4a72c" };
  }
  if (files.some((f) => f.status === "download")) {
    return { syncSummaryText: "Server newer", syncSummaryColor: "#1a9fff" };
  }
  if (saveStatus.last_sync_check_at) {
    const rel = formatSaveTimeAgo(saveStatus.last_sync_check_at);
    return { syncSummaryText: rel === "just now" ? "Synced just now" : `Synced ${rel}`, syncSummaryColor: "#5ba32b" };
  }
  return { syncSummaryText: "Not synced", syncSummaryColor: MUTED_COLOR };
}

/**
 * Sort save slots: active slot first, named slots alphabetically, legacy "" bucket last.
 * Synthesizes an entry for a known active slot missing from the list.
 */
export function sortSaveSlots(
  slots: SaveSlotSummary[],
  activeSlot: string | null | undefined,
  activeSlotKnown: boolean,
): SaveSlotSummary[] {
  const slotRank = (s: SaveSlotSummary): number => {
    if (s.slot === activeSlot) return 0;
    if (s.slot === "") return 2;
    return 1;
  };
  const sorted = [...slots].sort((a, b) => {
    const rankDiff = slotRank(a) - slotRank(b);
    if (rankDiff !== 0) return rankDiff;
    return a.slot.localeCompare(b.slot);
  });
  // An active slot the list doesn't carry still gets a panel — it is a slot the
  // user is on. Only for a slot something ANSWERED, though: synthesised off the
  // panel's placeholder it puts a slot name on screen that nothing ever said
  // (#1747), and `default` is a real name here, so it reads as a fact.
  if (activeSlot && activeSlotKnown && !sorted.some((s) => s.slot === activeSlot)) {
    sorted.unshift({ slot: activeSlot, source: "local", count: 0, latest_updated_at: null });
  }
  return sorted;
}

/**
 * Filter slots for panel display: in legacy mode (activeSlot === null),
 * omit the redundant legacy "" panel.
 */
export function filterSaveSlotsForDisplay(
  slots: SaveSlotSummary[],
  activeSlot: string | null | undefined,
): SaveSlotSummary[] {
  return slots.filter((s) => activeSlot !== null || s.slot !== "");
}
