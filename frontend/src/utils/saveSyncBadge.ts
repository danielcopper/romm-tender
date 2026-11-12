/** What the SAVE SYNC badge says about a game's saves, and in which tone. */

import type { SaveSetupInfo, SaveStatus, SaveSyncAvailability, SaveSyncDisplay } from "../types";
import { formatTimeAgo } from "./formatters";
import { hasAnySaveConflict } from "./saveStatus";
import { refusesDownloadedGame } from "./saveSyncStatement";
import {
  CANNOT_SYNC_LABEL,
  COULD_NOT_CHECK_LABEL,
  NOT_CHECKED_LABEL,
  NOT_SYNCED_LABEL,
  ROMM_UNAVAILABLE_LABEL,
  SAVE_CONFLICT_LABEL,
  SAVED_ON_ROMM_LABEL,
  SAVE_SYNC_OFF_LABEL,
  SLOT_NOT_CONFIRMED_LABEL,
  syncedLabel,
} from "./saveSyncBadgeWording";

export type SaveSyncBadgeTone = "off" | "ok" | "attention" | "unavailable";

interface SaveSyncBadge {
  tone: SaveSyncBadgeTone;
  label: string;
}

interface SaveSyncBadgeInput {
  enabled: boolean;
  offline: boolean;
  installed: boolean;
  /** The game detail store's `saveSyncStatus`, `saveSyncLabel` and `saveSyncCheckedAt`. */
  status: SaveSyncDisplay["status"] | null;
  label: string;
  checkedAt: string | null;
  /** Each is this ROM's, or `null`: another version's answer must not reach this. */
  saveStatus: SaveStatus | null;
  setupInfo: SaveSetupInfo | null;
  saveSyncAnswer: SaveSyncAvailability | null;
}

function holdsLocalSave({ installed, setupInfo, saveStatus }: SaveSyncBadgeInput): boolean {
  if (!installed) return false;
  if (setupInfo?.has_local_saves) return true;
  return saveStatus?.files.some((f) => Boolean(f.local_path || f.local_size || f.local_mtime)) ?? false;
}

/** When a file of this save last really synced: `last_sync_check_at` is stamped whether or not one transferred. */
function lastSyncedAt(saveStatus: SaveStatus | null): string | null {
  const times = saveStatus?.files.flatMap((f) => f.last_sync_at ?? []) ?? [];
  return times.reduce<string | null>(
    (newest, time) => (newest === null || Date.parse(time) > Date.parse(newest) ? time : newest),
    null,
  );
}

function offlineLabel({ status, label, checkedAt, saveStatus }: SaveSyncBadgeInput): string {
  if (status === "pending") return label;
  if (status === "synced") return checkedAt === null ? label : syncedLabel(label);
  // A status read that could not reach RomM carries no verdict, but its rows still know when this save last synced.
  const lastSync = lastSyncedAt(saveStatus);
  const ago = lastSync ? formatTimeAgo(lastSync) : null;
  return ago === null ? NOT_SYNCED_LABEL : syncedLabel(ago);
}

function offlineBadge(input: SaveSyncBadgeInput): SaveSyncBadge {
  const { installed } = input;
  if (holdsLocalSave(input)) return { tone: "attention", label: offlineLabel(input) };
  return { tone: installed ? "unavailable" : "off", label: ROMM_UNAVAILABLE_LABEL };
}

function reachableBadge({ installed, status, label, checkedAt, saveStatus }: SaveSyncBadgeInput): SaveSyncBadge {
  if (status === "synced") {
    return checkedAt === null ? { tone: "attention", label } : { tone: "ok", label: syncedLabel(label) };
  }
  if (status === "pending") return { tone: "attention", label };
  if (saveStatus?.server_query_failed) return { tone: "attention", label: COULD_NOT_CHECK_LABEL };
  if (saveStatus?.server_has_saves) return { tone: installed ? "attention" : "ok", label: SAVED_ON_ROMM_LABEL };
  if (status === null || saveStatus?.savefiles_in_content_dir) return { tone: "off", label: NOT_CHECKED_LABEL };
  return { tone: "ok", label };
}

export function saveSyncBadge(input: SaveSyncBadgeInput): SaveSyncBadge {
  const { enabled, offline, installed, status, saveStatus, setupInfo, saveSyncAnswer } = input;
  if (!enabled) return { tone: "off", label: SAVE_SYNC_OFF_LABEL };
  if (status === "conflict" || hasAnySaveConflict(saveStatus)) {
    return { tone: "attention", label: SAVE_CONFLICT_LABEL };
  }
  if (offline) return offlineBadge(input);

  // Only `server_unreachable`, or a failure with no reason, is RomM unreachable; a 404 or a refused registration is
  // no outage.
  const queryFailed = saveStatus?.server_query_failed ?? false;
  const reason = saveStatus?.server_query_reason;
  if (queryFailed && (reason === "server_unreachable" || reason === null || reason === undefined)) {
    return offlineBadge(input);
  }

  if (saveSyncAnswer !== null && refusesDownloadedGame(saveSyncAnswer)) {
    return { tone: "off", label: saveSyncAnswer.short_message ?? CANNOT_SYNC_LABEL };
  }
  // The save status says so too, which is what stands where the availability read failed.
  if (installed && saveStatus?.savefiles_in_content_dir) return { tone: "off", label: CANNOT_SYNC_LABEL };

  const slotToChoose =
    setupInfo !== null && !setupInfo.slot_confirmed && setupInfo.recommended_action === "show_wizard";
  if (holdsLocalSave(input) && slotToChoose) return { tone: "attention", label: SLOT_NOT_CONFIRMED_LABEL };

  return reachableBadge(input);
}
