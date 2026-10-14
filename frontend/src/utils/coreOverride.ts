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

/** Pin *coreLabel* as the game's core, or follow the system core again when it is
 *  `null`. The backend re-bakes the launch_options + returns the bound app_id for
 *  an installed ROM; we confirm-set them on the Steam shortcut BEFORE toasting
 *  success (R1). An unconfirmed bake gets a DISTINCT "restart Steam" toast and
 *  the DB row is KEPT — migration/re-sync re-bake from the pin. Uninstalled/unbound
 *  ROMs carry no launch_options/app_id: persist-only, no SetAppLaunchOptions. */
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

        // Confirmed (or uninstalled/unbound: nothing to confirm) → success.
        showToast(isClear ? "Now following the system core" : `Core set to ${coreLabel}`);
        await onSuccess?.();
      },
      leaseOwner,
      admission,
    );
  } catch (e) {
    // The core pin is persisted before the Steam continuation runs, so a
    // teardown cancellation is not a "failed to set core" the user must see.
    if (isPruneLeaseCancellation(e, admission)) {
      detach(debugLog(`${logTag}: continuation was cancelled: ${e}`));
      return;
    }
    showToast(failureMessage);
  }
}
