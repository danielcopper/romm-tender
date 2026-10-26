/**
 * The save-slot surfaces' round trips: the stranded-version check, switching
 * into a slot, and deleting one. Each surface keeps its own state, timers and
 * dialogs; the sentences are `saveSlotWording.ts`'s.
 */

import {
  checkLocalDrift,
  debugLog,
  deleteSlot,
  getSlotDeleteInfo,
  getVersionList,
  logWarn,
  switchSlot,
  type SaveStranding,
} from "../api/backend";
import { reportServerReachable } from "./connectionState";
import { detach } from "./detach";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { announceSaveSync } from "./saveSyncEvents";
import { showToast } from "./toast";
import { withTimeout } from "./withTimeout";
import {
  CREATE_SLOT_ERROR_MESSAGE,
  DELETE_SLOT_ERROR_MESSAGE,
  SLOT_DELETE_INFO_ERROR_MESSAGE,
  SWITCH_SLOT_ERROR_MESSAGE,
  createSlotFailureMessage,
  partialSlotSwitchMessage,
  slotDeletedMessage,
  switchSlotFailureMessage,
} from "./saveSlotWording";
import type { SaveStatus, SlotDeleteInfo, WireReason } from "../types";

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
 * marks a switch that stopped partway after changing what the SAVES tab shows.
 */
type SlotSwitchOutcome =
  { ok: true; saveStatus: SaveStatus } | { ok: false; reason: WireReason | null; message: string; partial?: true };

/**
 * Switch `romId` into `slot`, telling the connection store what the answer
 * showed about RomM's reachability. A switch that stopped partway also
 * announces the ROM's save sync, so the SAVES tab reads back the slot and the
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
        ? createSlotFailureMessage(result.reason)
        : switchSlotFailureMessage(result.reason, result.message);
    return { ok: false, reason: result.reason, message };
  } catch (e) {
    detach(debugLog(`saveSlotFlow: switchSlot to '${slot}' (${purpose}) threw: ${e}`));
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
      showToast(info.message);
      return null;
    }
    return info;
  } catch (e) {
    detach(debugLog(`saveSlotFlow: getSlotDeleteInfo for '${slot}' threw: ${e}`));
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
    showToast(result.message);
    return false;
  } catch (e) {
    detach(debugLog(`saveSlotFlow: deleteSlot for '${slot}' threw: ${e}`));
    showToast(DELETE_SLOT_ERROR_MESSAGE);
    return false;
  }
}
