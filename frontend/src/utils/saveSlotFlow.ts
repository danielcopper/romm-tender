/**
 * Pure async operations for save-slot and version management: probing inactive
 * versions for stranded local saves, switching or deleting slots, listing or
 * restoring file versions, copying a version into a slot, and deleting local saves.
 * Each surface keeps its own state, timers and dialogs; the sentences are
 * `saveSlotWording.ts`'s.
 */

import {
  checkLocalDrift,
  copySaveToSlot,
  deleteLocalSaves,
  deleteSlot,
  getSlotDeleteInfo,
  getVersionList,
  isEndpointFailure,
  logWarn,
  savesListFileVersions,
  savesRollbackToVersion,
  switchSlot,
  type SaveStranding,
} from "../api/backend";
import { reportServerReachable } from "./connectionState";
import { noteSaveSyncDisplay } from "./gameDetailStore";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { announceSaveSync } from "./saveSyncEvents";
import { showToast } from "./toast";
import { withTimeout } from "./withTimeout";
import {
  COPY_SAVE_ERROR_TOAST,
  CREATE_SLOT_ERROR_MESSAGE,
  DELETE_LOCAL_SAVES_ERROR_TOAST,
  DELETE_SLOT_ERROR_MESSAGE,
  RESTORE_CONFIRM_ERROR_TOAST,
  SLOT_DELETE_INFO_ERROR_MESSAGE,
  SWITCH_SLOT_ERROR_MESSAGE,
  copyToSlotFeedback,
  createSlotFailureMessage,
  partialSlotSwitchMessage,
  rollbackFeedback,
  slotDeletedMessage,
  switchSlotFailureMessage,
  versionHistoryLoadError,
} from "./saveSlotWording";
import type { SaveStatus, SaveVersionEntry, SlotDeleteInfo, SyncConflict, WireReason } from "../types";

/** A version the stranded-version check found, by its display name, and the check's verdict on it. */
export interface StrandedVersion {
  name: string;
  stranding: Exclude<SaveStranding, "none">;
}

/** The local-save check's verdict for `romId`; a call that rejects or gets no answer in time could not check. */
async function strandingOf(romId: number): Promise<SaveStranding> {
  try {
    return (await withTimeout(checkLocalDrift(romId), LOCAL_CALL_LIMIT_MS)).stranding;
  } catch (e) {
    logWarn(`saveSlotFlow: local-save check failed for rom ${romId}: ${e}`);
    return "unchecked";
  }
}

/**
 * The first inactive installed version in `appId`'s group whose verdict is not
 * `none`, or `null` when there is none. `isCancelled` is asked after every round
 * trip, and a `true` ends the probing with `null`; the caller still checks it
 * before using the answer. Throws what the version list throws.
 */
export async function findStrandedVersion(appId: number, isCancelled: () => boolean): Promise<StrandedVersion | null> {
  const list = await getVersionList(appId);
  if (isCancelled()) return null;
  const inactiveInstalled = list.multi_version
    ? (list.versions ?? []).filter((v) => v.installed && !v.active && !v.vanished)
    : [];
  // Any inactive-installed sibling can hold stranded saves; answer the first
  // one the verdict holds for.
  for (const v of inactiveInstalled) {
    const stranding = await strandingOf(v.rom_id);
    if (isCancelled()) return null;
    if (stranding !== "none") return { name: v.label || v.name || String(v.rom_id), stranding };
  }
  return null;
}

/** Whether the switch activates an existing slot or creates a new one; it picks the failure sentences. */
export type SlotSwitchPurpose = "activate" | "create";

/**
 * `reason` is the failure answer's, and `null` when the call threw. `partial`
 * marks a switch that stopped partway after changing what the save surface shows.
 */
type SlotSwitchOutcome =
  { ok: true; saveStatus: SaveStatus } | { ok: false; reason: WireReason | null; message: string; partial?: true };

/**
 * Switch `romId` into `slot`, telling the connection store what the answer
 * showed about RomM's reachability. A switch that stopped partway also
 * announces the ROM's save sync, so the save surface reads back the slot and the
 * files the backend saved. Never throws: a throw is logged and comes back as
 * `{ ok: false, reason: null }`.
 */
export async function executeSlotSwitch(
  romId: number,
  slot: string,
  purpose: SlotSwitchPurpose,
): Promise<SlotSwitchOutcome> {
  try {
    const result = await switchSlot(romId, slot);
    if (result.success) {
      reportServerReachable(true);
      return { ok: true, saveStatus: result.save_status };
    }
    if (result.reason === "server_unreachable") {
      reportServerReachable(false);
    }
    const setAside = result.reason === "switch_stopped" ? result.set_aside : [];
    const partial = partialSlotSwitchMessage(result.reason, setAside, purpose);
    if (partial !== null) {
      announceSaveSync(romId);
      return { ok: false, reason: result.reason, message: partial, partial: true };
    }
    const message =
      purpose === "create"
        ? createSlotFailureMessage(result.reason, result.message)
        : switchSlotFailureMessage(result.reason, result.message);
    logWarn(
      `saveSlotFlow: switchSlot for rom ${romId} to '${slot}' (${purpose}) failed (${result.reason}): ${result.message}`,
    );
    return { ok: false, reason: result.reason, message };
  } catch (e) {
    logWarn(`saveSlotFlow: switchSlot for rom ${romId} to '${slot}' (${purpose}) threw: ${e}`);
    return {
      ok: false,
      reason: null,
      message: purpose === "create" ? CREATE_SLOT_ERROR_MESSAGE : SWITCH_SLOT_ERROR_MESSAGE,
    };
  }
}

/**
 * What deleting `slot` would remove, for the surface's confirmation, or `null`
 * once a failure answer or a throw has been toasted. Never throws.
 */
export async function loadSlotDeleteInfo(romId: number, slot: string): Promise<SlotDeleteInfo | null> {
  try {
    const info = await getSlotDeleteInfo(romId, slot);
    if (!info.success) {
      logWarn(
        `saveSlotFlow: getSlotDeleteInfo for rom ${romId} slot '${slot}' failed (${info.reason}): ${info.message}`,
      );
      showToast(info.message);
      return null;
    }
    return info;
  } catch (e) {
    logWarn(`saveSlotFlow: getSlotDeleteInfo for rom ${romId} slot '${slot}' threw: ${e}`);
    showToast(SLOT_DELETE_INFO_ERROR_MESSAGE);
    return null;
  }
}

/** Delete `slot` and toast the answer; `true` only when it was deleted. Never throws. */
export async function executeSlotDelete(romId: number, slot: string): Promise<boolean> {
  try {
    const result = await deleteSlot(romId, slot);
    if (result.success) {
      showToast(slotDeletedMessage(slot));
      return true;
    }
    logWarn(`saveSlotFlow: deleteSlot for rom ${romId} slot '${slot}' failed (${result.reason}): ${result.message}`);
    showToast(result.message);
    return false;
  } catch (e) {
    logWarn(`saveSlotFlow: deleteSlot for rom ${romId} slot '${slot}' threw: ${e}`);
    showToast(DELETE_SLOT_ERROR_MESSAGE);
    return false;
  }
}

/**
 * Load prior server versions of `filename` in `slot` for `romId`. Returns
 * `{ ok: true }` on `ok` and `multi_file_unsupported`; never throws: other
 * non-ok answers and rejections are mapped via {@link versionHistoryLoadError}
 * (logging rejections and non-refusal failures).
 */
export async function loadFileVersions(
  romId: number,
  slot: string,
  filename: string,
): Promise<{ ok: true; versions: SaveVersionEntry[] } | { ok: false; error: string }> {
  try {
    const result = await savesListFileVersions(romId, slot, filename);
    if (result.status === "ok" || result.status === "multi_file_unsupported") {
      // Multi-file games don't list per-file history; return an empty list.
      return { ok: true, versions: result.versions };
    }
    if (result.status === "not_found") {
      logWarn(
        `saveSlotFlow: server has no such entry for rom ${romId} slot '${slot}' file ${filename}: ${result.message}`,
      );
    } else if (result.status === "device_not_registered") {
      logWarn(
        `saveSlotFlow: device not registered for rom ${romId} slot '${slot}' file ${filename}: ${result.message}`,
      );
    } else if (result.status === "romm_refused") {
      logWarn(
        `saveSlotFlow: RomM refused version list for rom ${romId} slot '${slot}' file ${filename}: ${result.message}`,
      );
    } else {
      logWarn(`saveSlotFlow: server unreachable for rom ${romId} slot '${slot}' file ${filename}: ${result.message}`);
    }
    return { ok: false, error: versionHistoryLoadError(result) };
  } catch (e) {
    logWarn(`saveSlotFlow: failed to load versions for rom ${romId} slot '${slot}' file ${filename}: ${e}`);
    return { ok: false, error: versionHistoryLoadError(null) };
  }
}

/**
 * Copy server save `saveId` into `targetSlot` and surface the outcome via
 * toast or `showConflict`. Dispatches `romm_data_changed` on `ok` and
 * `upload_failed` (`already_present` copied nothing). Catches and toasts copy
 * RPC rejections.
 */
export async function executeCopySaveToSlot(
  romId: number,
  saveId: number,
  targetSlot: string,
  showConflict: (conflict: SyncConflict) => Promise<unknown>,
): Promise<void> {
  let result: Awaited<ReturnType<typeof copySaveToSlot>>;
  try {
    result = await copySaveToSlot(romId, saveId, targetSlot);
  } catch (e) {
    logWarn(`saveSlotFlow: copy failed for rom ${romId} save ${saveId} into '${targetSlot}': ${e}`);
    showToast(COPY_SAVE_ERROR_TOAST);
    return;
  }
  const feedback = copyToSlotFeedback(result, targetSlot);
  if (feedback === null) return;
  if (feedback.kind === "conflict") {
    await showConflict(feedback.conflict);
    return;
  }
  showToast(feedback.message);
  // Only a copy refreshes the slot views: `already_present` copied nothing.
  if (!isEndpointFailure(result) && (result.status === "ok" || result.status === "upload_failed")) {
    announceSaveSync(romId);
  }
}

/**
 * Roll back `slot` to `version` and surface the outcome via toast or
 * `showConflict`. Returns `true` when the local restore landed (`ok` or
 * `put_failed`). Catches and toasts rollback RPC rejections.
 */
export async function executeVersionRestore(
  romId: number,
  slot: string,
  version: SaveVersionEntry,
  showConflict: (conflict: SyncConflict) => Promise<unknown>,
): Promise<boolean> {
  let result: Awaited<ReturnType<typeof savesRollbackToVersion>>;
  try {
    result = await savesRollbackToVersion(romId, slot, version.id);
  } catch (e) {
    logWarn(`saveSlotFlow: restore error for rom ${romId} slot '${slot}' save ${version.id}: ${e}`);
    showToast(RESTORE_CONFIRM_ERROR_TOAST, { subtext: e instanceof Error ? e.message : String(e) });
    return false;
  }
  const feedback = rollbackFeedback(result, version.updated_at);
  if (feedback === null) return false;
  if (feedback.kind === "conflict") {
    await showConflict(feedback.conflict);
  } else {
    showToast(feedback.message);
  }
  // `put_failed` refreshes too: the local download succeeded and only the
  // server-side bump did not, so the switch is locally complete — it just
  // won't reach other devices yet.
  return !isEndpointFailure(result) && (result.status === "ok" || result.status === "put_failed");
}

/**
 * Delete local saves for `romId`, toast the result, and update the game's
 * shared save display + listeners on success. Never throws.
 */
export async function executeDeleteLocalSaves(appId: number, romId: number): Promise<void> {
  try {
    const result = await deleteLocalSaves(romId);
    if (result.success) {
      showToast(result.message);
      // Directly update the shown status — no local saves remain
      noteSaveSyncDisplay(appId, romId, { status: "none", label: "No saves", last_sync_check_at: null });
      announceSaveSync(romId);
    } else {
      logWarn(`saveSlotFlow: deleteLocalSaves for rom ${romId} failed: ${result.message}`);
      showToast(result.message || DELETE_LOCAL_SAVES_ERROR_TOAST);
    }
  } catch (e) {
    logWarn(`saveSlotFlow: deleteLocalSaves for rom ${romId} threw: ${e}`);
    showToast(DELETE_LOCAL_SAVES_ERROR_TOAST);
  }
}
