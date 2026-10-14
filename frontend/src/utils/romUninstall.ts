/**
 * Uninstalling a ROM: the backend removal, the launch-command reset that follows
 * it, and saying how it went.
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

    // Reset the shortcut's now-stale launch command to the uninstalled ""
    // placeholder under the removal's lease. The reset keeps a raced-past
    // not_installed launch from exec'ing a stale `flatpak run … '<path>'` (#1051);
    // it is best-effort, so a launch-options hiccup never turns a removal that
    // happened into an error.
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
    // The backend has removed the ROM whether the reset above ran to the end or
    // its signal aborted it, so the game page and the pickers hear about it in
    // both cases.
    globalThis.dispatchEvent(new CustomEvent("romm_rom_uninstalled", { detail: { rom_id: romId } }));

    showToast(`${romName || "ROM"} uninstalled`);
    return true;
  } catch (e) {
    // A throw once the page's admission is stale is its teardown, not a failure the
    // user can act on: the page is gone, and where the lease was refused the backend
    // uninstall had already committed.
    if (isPruneLeaseCancellation(e, currentAdmission)) {
      detach(debugLog(`${tag}: continuation was cancelled: ${e}`));
      return false;
    }
    showToast("Uninstall failed");
    return false;
  }
}
