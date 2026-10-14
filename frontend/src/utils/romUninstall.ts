/**
 * ROM uninstallation lifecycle utility.
 *
 * Encapsulates the complete removal workflow shared between Desktop and Big Picture:
 *   1. Capturing prune lease admission
 *   2. Invoking backend `remove_rom`
 *   3. Executing the prune lease continuation (clearing shortcut launch options
 *      and dispatching `romm_rom_uninstalled`)
 *   4. Surfacing toast feedback and handling unmount/cancellation without false error toasts
 */

import { removeRom, debugLog } from "../api/backend";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";
import {
  capturePruneLeaseAdmission,
  isPruneLeaseCancelled,
  isPruneLeaseCancellation,
  withPruneLease,
} from "./pruneLease";
import { showToast } from "./toast";
import { detach } from "./detach";

export interface RomUninstallOptions {
  romId: number;
  appId: number;
  romName?: string | undefined;
  leaseOwner: string;
  tag?: string | undefined;
  context?: string | undefined;
}

/** Remove the ROM's files and announce it; true once the removal is done and announced. */
export async function executeRomUninstall(options: RomUninstallOptions): Promise<boolean> {
  const { romId, appId, romName, leaseOwner, tag = "handleUninstall", context = "ROM uninstall" } = options;
  const currentAdmission = capturePruneLeaseAdmission(leaseOwner);

  try {
    const result = await removeRom(romId);
    if (!result.success) {
      showToast(result.message || "Uninstall failed");
      return false;
    }

    // Why the launch command is reset, and why best-effort: `removeInstallRecord`'s
    // docstring in `bigpicture/CustomPlayButton.tsx`.
    await withPruneLease(
      result.prune_lease_token,
      context,
      async (signal) => {
        if (isPruneLeaseCancelled(signal)) return;
        await setLaunchOptionsConfirmed(appId, "").catch(() => false);
      },
      leaseOwner,
      currentAdmission,
    );
    // The backend has removed the ROM whether or not the reset above ran to the
    // end, so the game page and the pickers hear about it either way.
    globalThis.dispatchEvent(new CustomEvent("romm_rom_uninstalled", { detail: { rom_id: romId } }));

    showToast(`${romName || "ROM"} uninstalled`);
    return true;
  } catch (e) {
    // The backend uninstall already committed before the continuation was torn
    // down; reporting it as a failure would be a lie the user can't act on.
    if (isPruneLeaseCancellation(e, currentAdmission)) {
      detach(debugLog(`${tag}: continuation was cancelled: ${e}`));
      return false;
    }
    showToast("Uninstall failed");
    return false;
  }
}
