/**
 * Pure formatters and selectors for the SavesTab UI. Anything that takes
 * inputs and returns outputs without touching component state or React
 * belongs here; rendering helpers live alongside their components.
 */

import type {
  DeviceSyncInfo,
  RollbackStatus,
  SaveStatus,
  SyncConflict,
  SlotDeleteInfo,
  SaveSlotSummary,
  CopySaveToSlotStatus,
} from "../types";

export const MUTED_COLOR = "#8f98a0";

/** Display a slot name, labelling the null/empty slot with RomM's own name for a
 *  save uploaded without a slot: `upload-slot-hint` in RomM's
 *  `frontend/src/locales/en_US/rom.json`, at 5.3.1. */
export function displaySlot(slot: string | null | undefined): string {
  if (slot === null || slot === undefined || slot === "") return "Manual archive";
  return slot;
}

/** Format a relative time string (e.g. "5m ago", "2h ago") from an ISO string */
export function formatRelativeTime(isoStr: string | null): string {
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
    // Intentionally no device name — lastSyncer is our own sync record, not the actual uploader
    return `${label}${check}`;
  }
  if (label === null) {
    if (deviceName) return `${deviceName}${check}`;
    return showCheck ? "✓" : null;
  }
  return null;
}

/** The toast for a restore refused as unsupported — the backend's own explanation where it gave one. */
export function unsupportedRestoreMessage(result: Extract<RollbackStatus, { status: "unsupported" }>): string {
  if (result.reason === "savefiles_in_content_dir") {
    return "Save sync is off for this game: its saves are written beside the game file.";
  }
  if (result.reason === "save_shape_unsupported" && result.message) {
    return result.message;
  }
  return "Restoring isn't available for multi-file saves yet.";
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
 * green ✓ (#1334).
 */
export function computeSyncSummary(
  isActive: boolean,
  saveStatus: SaveStatus | null,
  conflicts: SyncConflict[],
): { syncSummaryText: string | null; syncSummaryColor: string } {
  if (!isActive || !saveStatus) return { syncSummaryText: null, syncSummaryColor: MUTED_COLOR };

  if (saveStatus.server_query_failed) {
    const text =
      saveStatus.server_query_reason === "server_unreachable" ? "Server unreachable" : "Save status unavailable";
    return { syncSummaryText: text, syncSummaryColor: MUTED_COLOR };
  }

  const hasConflict = conflicts.length > 0;
  const files = saveStatus.files;

  if (hasConflict) return { syncSummaryText: "Conflict detected", syncSummaryColor: "#d94126" };
  if (files.length === 0) return { syncSummaryText: "No saves found", syncSummaryColor: MUTED_COLOR };
  if (files.some((f) => f.status === "upload")) {
    return { syncSummaryText: "Local changes", syncSummaryColor: "#d4a72c" };
  }
  if (files.some((f) => f.status === "download")) {
    return { syncSummaryText: "Server newer", syncSummaryColor: "#1a9fff" };
  }
  if (saveStatus.last_sync_check_at) {
    const rel = formatRelativeTime(saveStatus.last_sync_check_at);
    return { syncSummaryText: rel === "just now" ? "Synced just now" : `Synced ${rel}`, syncSummaryColor: "#5ba32b" };
  }
  return { syncSummaryText: "Not synced", syncSummaryColor: MUTED_COLOR };
}

/** The toast for a copy refused as unsupported — the backend's own explanation where it gave one. */
export function unsupportedCopyMessage(result: Extract<CopySaveToSlotStatus, { status: "unsupported" }>): string {
  if (result.reason === "savefiles_in_content_dir") {
    return "Save sync is off for this game: its saves are written beside the game file.";
  }
  if (result.reason === "save_shape_unsupported" && result.message) {
    return result.message;
  }
  return "Copying isn't available for multi-file saves yet.";
}

export type CopySaveToSlotFeedback = { kind: "toast"; message: string } | { kind: "conflict"; conflict: SyncConflict };

/** Map a CopySaveToSlotStatus to user-facing toast feedback or conflict modal requirement. */
export function formatCopySaveToSlotFeedback(result: CopySaveToSlotStatus, targetSlot: string): CopySaveToSlotFeedback {
  switch (result.status) {
    case "ok":
      return { kind: "toast", message: `Save copied to slot '${displaySlot(targetSlot)}'` };
    case "already_present":
      return { kind: "toast", message: `Already in slot '${displaySlot(targetSlot)}' as #${result.existing_id}` };
    case "conflict_blocked": {
      const first = result.conflicts[0];
      if (first) {
        return { kind: "conflict", conflict: first };
      }
      return { kind: "toast", message: "Copy blocked by a sync conflict. Sync this save, then try again." };
    }
    case "target_slot_busy":
      return {
        kind: "toast",
        message: `Slot '${displaySlot(targetSlot)}' has newer changes on another device — sync it first, then copy again.`,
      };
    case "preflight_failed":
      return {
        kind: "toast",
        message: `Sync failed before copy: ${result.errors[0] ?? "preflight error"}`,
      };
    case "server_unreachable":
      return { kind: "toast", message: "Couldn't reach RomM. Check your connection and try again." };
    case "not_found":
      return { kind: "toast", message: "RomM couldn't find this game's save data — nothing was copied." };
    case "version_deleted":
      return { kind: "toast", message: "This save no longer exists on the server." };
    case "rom_not_installed":
      return { kind: "toast", message: "ROM is no longer installed locally. Reinstall and try again." };
    case "unsupported":
      return { kind: "toast", message: unsupportedCopyMessage(result) };
    case "not_configured":
      return { kind: "toast", message: "Set up save slots for this game first, then copy." };
    case "copy_failed":
      return { kind: "toast", message: `Couldn't copy the save: ${result.message}` };
    case "invalid_slot_name":
      return { kind: "toast", message: "Enter a valid slot name." };
  }
}

export type RollbackFeedback = { kind: "toast"; message: string } | { kind: "conflict"; conflict: SyncConflict };

/** Map a RollbackStatus to user-facing toast feedback or conflict modal requirement. */
export function formatRollbackFeedback(result: RollbackStatus, versionUpdatedAt: string | null): RollbackFeedback {
  switch (result.status) {
    case "ok":
      return { kind: "toast", message: `Save restored from ${formatRelativeTime(versionUpdatedAt)}` };
    case "conflict_blocked": {
      const first = result.conflicts[0];
      if (first) {
        return { kind: "conflict", conflict: first };
      }
      return { kind: "toast", message: "Restore blocked by a sync conflict. Sync this save, then try again." };
    }
    case "preflight_failed": {
      const detail = result.errors[0] ?? "preflight error";
      return { kind: "toast", message: `Sync failed before restore: ${detail}` };
    }
    case "put_failed":
      return {
        kind: "toast",
        message:
          "Restored locally, but the server didn't update. Other devices will see the previous version until you retry.",
      };
    case "rom_not_installed":
      return { kind: "toast", message: "ROM is no longer installed locally. Reinstall and try again." };
    case "version_deleted":
      return { kind: "toast", message: "This version no longer exists on the server" };
    case "server_unreachable":
      return { kind: "toast", message: "Couldn't reach RomM. Check your connection and try again." };
    case "not_found":
      return { kind: "toast", message: "RomM couldn't find this game's save data — nothing was restored." };
    case "unsupported":
      return { kind: "toast", message: unsupportedRestoreMessage(result) };
  }
}

/**
 * Error message for a switchSlot failure: a known reason's own wording, the
 * backend's explanation for `save_shape_unsupported`, and `fallback` otherwise.
 */
export function switchSlotFailureMessage(
  reason: string | undefined,
  message?: string,
  fallback = "Failed to switch slot",
): string {
  if (reason === "pending_uploads") {
    return "Sync your saves first — local changes haven't been uploaded";
  }
  if (reason === "server_unreachable") {
    return "Can't switch — RomM server is not reachable";
  }
  if (reason === "not_installed") {
    return "Can't switch — download the game first";
  }
  if (reason === "savefiles_in_content_dir") {
    return "Can't switch — this game's saves are written beside the game file";
  }
  if (reason === "save_shape_unsupported" && message) {
    return message;
  }
  return fallback;
}

/** Build confirmation text lines for deleting a slot. */
export function formatSlotDeleteLines(info: SlotDeleteInfo): string[] {
  const lines: string[] = [];
  if (info.source === "server" && info.server_save_count > 0) {
    const n = info.server_save_count;
    lines.push(
      `This will permanently delete ${n} save${n === 1 ? "" : "s"} from slot '${info.slot}' on the RomM server.`,
    );
  } else {
    lines.push(`This will remove slot '${info.slot}' from your local configuration.`);
  }
  if (info.local_file_count > 0) {
    const n = info.local_file_count;
    lines.push(`${n} tracked file${n === 1 ? "" : "s"} will be unlinked.`);
  }
  return lines;
}

/** Build the multi-line confirmation description for deleting a slot. */
export function formatSlotDeleteDescription(info: SlotDeleteInfo): string {
  return [...formatSlotDeleteLines(info), "This cannot be undone."].join("\n\n");
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
