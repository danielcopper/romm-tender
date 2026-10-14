/**
 * coreOverride.ts — Shared game emulator and core override management (#211, #945).
 *
 * Implements:
 * - Pinning a per-game emulator core override via `setGameCore`.
 * - Clearing an override / following the system core via `clearGameCore`.
 * - Prune lease protection with unmount cancellation suppression.
 * - Steam shortcut launch option confirmation and "restart Steam" alert when unconfirmed.
 * - Uniform toast notifications on success and failure matching Big Picture.
 */

import { clearGameCore, debugLog, setGameCore } from "../api/backend";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";
import { showToast } from "./toast";
import { detach } from "./detach";
import {
  capturePruneLeaseAdmission,
  isPruneLeaseCancelled,
  isPruneLeaseCancellation,
  withPruneLease,
} from "./pruneLease";

export interface ApplyGameCoreChangeParams {
  romId: number;
  coreLabel: string | null;
  leaseOwner: string;
  onSuccess?: () => Promise<void> | void;
  logTag?: string;
}

/**
 * Applies a per-game emulator core change:
 * - If `coreLabel` is a string: pins the specified core override via `setGameCore`.
 * - If `coreLabel` is null: clears the override via `clearGameCore` (reverting to system core).
 *
 * If backend RPC succeeds:
 * - When launch options are returned, confirms them with `setLaunchOptionsConfirmed`.
 * - If unconfirmed, toasts "Core saved — restart Steam to apply".
 * - If confirmed, toasts "Core set to {coreLabel}" or "Now following the system core", and calls `onSuccess`.
 *
 * If backend RPC fails or throws:
 * - Cancellations during unmount are logged and suppressed without toasting.
 * - Errors toast backend failure message or generic fallback.
 */
export async function applyGameCoreChange({
  romId,
  coreLabel,
  leaseOwner,
  onSuccess,
  logTag = "Core selection",
}: ApplyGameCoreChangeParams): Promise<void> {
  const isClear = coreLabel === null;
  const failureMessage = isClear ? "Failed to reset core" : "Failed to set core";
  detach(debugLog(isClear ? `${logTag}: romId=${romId}` : `${logTag}: romId=${romId} coreLabel=${coreLabel}`));
  const admission = capturePruneLeaseAdmission(leaseOwner);

  try {
    const result = isClear ? await clearGameCore(romId) : await setGameCore(romId, coreLabel);
    detach(debugLog(`${logTag}: result success=${result.success}`));
    await withPruneLease(
      result.success ? result.prune_lease_token : undefined,
      "Core selection",
      async (signal) => {
        if (!result.success) {
          showToast(result.message || "Failed to set core");
          return;
        }

        // Installed + bound: confirm the re-baked launch_options landed before
        // claiming success. app_id can be null/undefined for an unbound ROM.
        if (result.launch_options !== undefined && result.app_id != null) {
          if (isPruneLeaseCancelled(signal)) return;
          const confirmed = await setLaunchOptionsConfirmed(result.app_id, result.launch_options);
          if (isPruneLeaseCancelled(signal)) return;
          if (!confirmed) {
            // Never toast success on an unconfirmed bake. Keep the DB row — a Steam
            // restart (or the next migration/re-sync) re-bakes from the override.
            showToast("Core saved — restart Steam to apply");
            return;
          }
        }

        // Confirmed (or uninstalled/unbound: nothing to confirm) -> success.
        showToast(isClear ? "Now following the system core" : `Core set to ${coreLabel}`);
        await onSuccess?.();
      },
      leaseOwner,
      admission,
    );
  } catch (e) {
    if (isPruneLeaseCancellation(e, admission)) {
      detach(debugLog(`${logTag}: continuation was cancelled: ${e}`));
      return;
    }
    showToast(failureMessage);
  }
}
