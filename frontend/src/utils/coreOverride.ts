import { clearGameCore, debugLog, logWarn, setGameCore } from "../api/backend";
import { showToast } from "./toast";
import { detach } from "./detach";
import { capturePruneLeaseAdmission, isPruneLeaseCancellation } from "./pruneLease";

export interface GameCoreChangeParams {
  romId: number;
  coreLabel: string | null;
  /** What the success toast calls the pinned emulator; the label where omitted. */
  coreName?: string;
  leaseOwner: string;
  onSuccess?: () => Promise<void> | void;
  logTag?: string;
}

export type GameCoreChangeResult =
  { status: "applied" } | { status: "cancelled" } | { status: "failed"; message: string };

/** Pin *coreLabel* as the game's core, or follow the system core again when it is
 *  `null`. Nothing is written to the Steam shortcut: its launch options are its
 *  appId, and the next start asks with the new pin. */
export async function executeGameCoreChange({
  romId,
  coreLabel,
  coreName,
  leaseOwner,
  onSuccess,
  logTag = "Core selection",
}: GameCoreChangeParams): Promise<GameCoreChangeResult> {
  const isClear = coreLabel === null;
  const failureMessage = isClear ? "Failed to reset core" : "Failed to set core";
  detach(debugLog(isClear ? `${logTag}: romId=${romId}` : `${logTag}: romId=${romId} coreLabel=${coreLabel}`));
  const admission = capturePruneLeaseAdmission(leaseOwner);

  try {
    const result = isClear ? await clearGameCore(romId) : await setGameCore(romId, coreLabel);
    detach(debugLog(`${logTag}: result success=${result.success}`));
    if (!result.success) {
      const message = result.message || "Failed to set core";
      showToast(message);
      return { status: "failed", message };
    }
    showToast(isClear ? "Now following the system core" : `Core set to ${coreName ?? coreLabel}`);
    await onSuccess?.();
    return { status: "applied" };
  } catch (e) {
    // Leaving the page cancels the change; the pin may already be persisted, so
    // that teardown is not a "failed to set core" the user must see.
    if (isPruneLeaseCancellation(e, admission)) {
      detach(debugLog(`${logTag}: continuation was cancelled: ${e}`));
      return { status: "cancelled" };
    }
    logWarn(`${logTag}: unexpected error: ${e}`);
    showToast(failureMessage);
    return { status: "failed", message: failureMessage };
  }
}
