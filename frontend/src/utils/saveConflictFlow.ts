/**
 * Resolving save conflicts: one resolution, a list of them, and the conflict a
 * surface is already showing. The dialog that asks is the surface's, injected.
 */

import { getSaveStatus, isEndpointFailure, logError, debugLog, resolveSyncConflict } from "../api/backend";
import { reportServerReachable } from "./connectionState";
import { detach } from "./detach";
import { formatBytes, formatTimestamp } from "./formatters";
import { showToast } from "./toast";
import type { SyncConflict } from "../types";

export type SyncConflictAction = "keep_local" | "use_server";
export type SyncConflictResolution = SyncConflictAction | "cancel";

// ── Wording ──

export function conflictTitle(conflict: SyncConflict): string {
  return `Save conflict for ${conflict.filename}`;
}

export const CONFLICT_EXPLANATION =
  "Both your local save and the server save have changed since the last sync. Pick which version to keep — the " +
  "other will be overwritten.";

/** "unknown" when bytes is null or 0 — otherwise the shared byte formatter output. */
export function formatConflictSize(bytes: number | null): string {
  if (bytes == null || bytes === 0) return "unknown";
  return formatBytes(bytes);
}

export function localSaveDetail(conflict: SyncConflict): string {
  return `${formatConflictSize(conflict.local_size)} · modified ${formatTimestamp(conflict.local_mtime)}`;
}

export function serverSaveLabel(conflict: SyncConflict): string {
  return `Server save (id=${conflict.server_save_id})`;
}

export function serverSaveDetail(conflict: SyncConflict): string {
  return `${formatConflictSize(conflict.server_size)} · uploaded ${formatTimestamp(conflict.server_updated_at)}`;
}

export const STALE_CONFLICT_MESSAGE =
  "The server save has been updated by another device. Please cancel and retry sync to get the latest version.";

// ── One resolution ──

/**
 * `toast` is the confirmation to show once the dialog closes. `message` is for
 * the dialog to show while it stays open, so the user can retry or cancel.
 */
export type ConflictResolveOutcome = { ok: true; toast: string } | { ok: false; message: string };

/**
 * Resolve one conflict with the user's chosen side. Never throws: a refusal or a
 * throw comes back as `{ ok: false }`, already logged.
 *
 * - Keep Local → the backend POSTs the local save to the server (overwrite=true)
 * - Use Server → the backend downloads the server save over the local one
 */
export async function resolveOneConflict(
  conflict: SyncConflict,
  action: SyncConflictAction,
): Promise<ConflictResolveOutcome> {
  const call = `resolveSyncConflict(${conflict.rom_id}, ${conflict.filename}, ${action})`;
  try {
    const result = await resolveSyncConflict(conflict.rom_id, conflict.filename, conflict.server_save_id, action);
    if (!result.success) {
      if (result.reason === "stale_conflict") {
        logError(`${call} stale: ${result.message ?? ""}`);
        return { ok: false, message: STALE_CONFLICT_MESSAGE };
      }
      const msg = result.message ?? "Failed to resolve conflict";
      logError(`${call} failed: ${msg}`);
      return { ok: false, message: msg };
    }
    return {
      ok: true,
      toast:
        action === "keep_local"
          ? "Conflict resolved — kept your local save (uploaded to server)."
          : "Conflict resolved — used the server save · your local was backed up.",
    };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    logError(`${call} threw: ${msg}`);
    return { ok: false, message: msg || "Failed to resolve conflict" };
  }
}

// ── A list of them ──

/**
 * Walk a list of conflicts sequentially, asking `showOne` for each. Bails on the
 * first cancel so the caller can decide what to do (e.g. not relaunch). Returns
 * "resolved" once every conflict was resolved (or the list was empty), "cancel"
 * on the first dismissal.
 */
export async function resolveConflictsSequentially(
  conflicts: SyncConflict[],
  showOne: (conflict: SyncConflict) => Promise<SyncConflictResolution>,
): Promise<"cancel" | "resolved"> {
  for (const conflict of conflicts) {
    if ((await showOne(conflict)) === "cancel") return "cancel";
  }
  return "resolved";
}

/** Tell sibling components a ROM's save state changed, so they re-read it. */
export function announceSaveSync(romId: number): void {
  globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: romId } }));
}

// ── The conflict a surface is already showing ──

/**
 * - `resolved` — nothing is in conflict any more; siblings have been told.
 * - `cancelled` — the user dismissed a conflict dialog.
 * - `failed` — the conflict could not be read; the reason has been toasted.
 */
export type KnownConflictOutcome = "resolved" | "cancelled" | "failed";

/**
 * Resolve the conflict a surface is already showing. This is not a re-sync: it
 * pulls the already-known conflict via `getSaveStatus` and hands it to
 * `resolveAll`. `getSaveStatus` uploads and downloads nothing, but it may
 * follow a moved save directory first and so move local files. Re-running the
 * act-capable `preLaunchSync` here (the pre-#1276 behavior) could
 * upload/download OTHER files in the ROM as a side effect and re-derive the
 * conflict through a different path than the one that put the surface in
 * conflict — so the launch path keeps `preLaunchSync`, but conflict resolution
 * must not act.
 */
export async function resolveKnownConflicts(
  romId: number,
  resolveAll: (conflicts: SyncConflict[]) => Promise<"cancel" | "resolved">,
  logContext: string,
): Promise<KnownConflictOutcome> {
  try {
    const result = await Promise.race([
      getSaveStatus(romId),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error("timeout")), 15000)),
    ]);

    if (isEndpointFailure(result)) {
      detach(debugLog(`${logContext}: resolve conflict deferred: ${result.message}`));
      showToast(result.message);
      return "failed";
    }

    // A failed status read leaves every file "unknown" and an empty server
    // list; treating that as "resolved" would drop the user back to Play
    // believing the conflict was cleared. Surface it and stay in conflict,
    // exactly like the network-throw catch below (#1276).
    if (result.server_query_failed) {
      // Only an explicit unreachable verdict is a connectivity signal — for
      // the store AND the copy. Off the bare flag both blamed the connection
      // for a ROM the server merely no longer has (#1570).
      const unreachable = result.server_query_reason === "server_unreachable";
      if (unreachable) {
        reportServerReachable(false);
      }
      detach(debugLog(`${logContext}: resolve conflict — server query failed for rom ${romId}`));
      showToast(
        unreachable
          ? "Couldn't reach server to resolve conflict"
          : "RomM couldn't find this game's save data — conflict left unresolved",
      );
      return "failed";
    }
    // A clean status read proves the server is reachable again (#1345).
    reportServerReachable(true);

    if (result.conflicts && result.conflicts.length > 0) {
      if ((await resolveAll(result.conflicts)) === "cancel") return "cancelled";
    }
    // Resolved here, or the conflict was already cleared elsewhere (empty/
    // absent conflicts) — notify siblings.
    announceSaveSync(romId);
    return "resolved";
  } catch (e) {
    detach(debugLog(`${logContext}: resolve conflict failed: ${e}`));
    showToast("Couldn't reach server to resolve conflict");
    return "failed";
  }
}
