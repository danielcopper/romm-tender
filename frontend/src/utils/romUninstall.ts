/**
 * Uninstalling a ROM: the backend removal, and saying how it went.
 */

import { removeRom, debugLog } from "../api/backend";
import { logFailure, logRefusal } from "./backendLog";
import { capturePruneLeaseAdmission, isPruneLeaseAdmissionCurrent } from "./pruneLease";
import { showToast } from "./toast";
import { detach } from "./detach";

export interface RomUninstallOptions {
  romId: number;
  romName?: string | undefined;
  leaseOwner: string;
  tag?: string | undefined;
}

/**
 * Remove the ROM's files and announce it; true once the removal is done and announced.
 *
 * The shortcut is left as it is: its launch options are its appId, and a start of
 * a game that is not installed is refused before anything runs. A removal that
 * answers after the page was left is not announced from here: that is the
 * page's teardown, not a failure the user can act on.
 */
export async function executeRomUninstall(options: RomUninstallOptions): Promise<boolean> {
  const { romId, romName, leaseOwner, tag = "handleUninstall" } = options;
  const admission = capturePruneLeaseAdmission(leaseOwner);

  const result = await removeRom(romId).catch((e: unknown) => {
    logFailure(tag, "remove", romId, e);
    return null;
  });
  if (!result) {
    showToast("Uninstall failed");
    return false;
  }
  if (!result.success) {
    logRefusal(tag, "remove", romId, result);
    showToast(result.message || "Uninstall failed");
    return false;
  }
  if (!isPruneLeaseAdmissionCurrent(admission)) {
    detach(debugLog(`${tag}: continuation was cancelled: the removal answered after the page was left`));
    return false;
  }

  // The game page and the pickers hear about it.
  globalThis.dispatchEvent(new CustomEvent("romm_rom_uninstalled", { detail: { rom_id: romId } }));
  showToast(`${romName || "ROM"} uninstalled`);
  return true;
}
