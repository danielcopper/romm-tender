/**
 * The save-slot surfaces' round trips: switching into a slot, and deleting
 * one. Each surface keeps its own state, timers and dialogs; the sentences are
 * `saveSlotWording.ts`'s.
 */

import { debugLog, deleteSlot, getSlotDeleteInfo, switchSlot } from "../api/backend";
import { reportServerReachable } from "./connectionState";
import { detach } from "./detach";
import { showToast } from "./toast";
import {
  CREATE_SLOT_ERROR_MESSAGE,
  DELETE_SLOT_ERROR_MESSAGE,
  SLOT_DELETE_INFO_ERROR_MESSAGE,
  SWITCH_SLOT_ERROR_MESSAGE,
  createSlotFailureMessage,
  slotDeletedMessage,
  switchSlotFailureMessage,
} from "./saveSlotWording";
import type { SaveStatus, SlotDeleteInfo, WireReason } from "../types";

/** Whether the switch activates an existing slot or creates a new one; it picks the failure sentences. */
type SlotSwitchPurpose = "activate" | "create";

/** `reason` is the failure answer's, and `null` when the call threw. */
type SlotSwitchOutcome =
  { ok: true; saveStatus: SaveStatus } | { ok: false; reason: WireReason | null; message: string };

/**
 * Switch `romId` into `slot`, telling the connection store what the answer
 * showed about RomM's reachability. Never throws: a throw is logged and comes
 * back as `{ ok: false, reason: null }`.
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
