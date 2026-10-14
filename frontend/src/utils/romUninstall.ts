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

export async function executeRomUninstall(options: RomUninstallOptions): Promise<void> {
  const { romId, appId, romName, leaseOwner, tag = "handleUninstall", context = "ROM uninstall" } = options;
  const currentAdmission = capturePruneLeaseAdmission(leaseOwner);

  try {
    const result = await removeRom(romId);
    if (!result.success) {
      showToast(result.message || "Uninstall failed");
      return;
    }

    // Why the launch command is reset, and why best-effort: `removeInstallRecord`'s
    // docstring in `bigpicture/CustomPlayButton.tsx`.
    await withPruneLease(
      result.prune_lease_token,
      context,
      async (signal) => {
        if (isPruneLeaseCancelled(signal)) return;
        await setLaunchOptionsConfirmed(appId, "").catch(() => false);
        if (isPruneLeaseCancelled(signal)) return;
        globalThis.dispatchEvent(new CustomEvent("romm_rom_uninstalled", { detail: { rom_id: romId } }));
      },
      leaseOwner,
      currentAdmission,
    );

    showToast(`${romName || "ROM"} uninstalled`);
  } catch (e) {
    // The backend uninstall already committed before the continuation was torn
    // down; reporting it as a failure would be a lie the user can't act on.
    if (isPruneLeaseCancellation(e, currentAdmission)) {
      detach(debugLog(`${tag}: continuation was cancelled: ${e}`));
      return;
    }
    showToast("Uninstall failed");
  }
}
