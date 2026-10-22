/**
 * "Replace…" on a game page's BIOS row, once the user confirmed it: the file at
 * the row's place goes to the backup folder and the platform's store copy is
 * linked in its place. What it did is said on the rows (`biosAttempts`); a
 * refusal is a toast, as a failed BIOS press on the game page is.
 */

import { replaceBiosFile } from "../api/backend";
import { noteReplaced } from "./biosAttempts";
import { showToast } from "./toast";

/** Replace what lies at *place* for *platformSlug*; true once it was replaced. */
export async function executeBiosReplace(platformSlug: string, place: string): Promise<boolean> {
  try {
    const result = await replaceBiosFile(platformSlug, place);
    if (!result.success) {
      showToast(result.message);
      return false;
    }
    noteReplaced(place, result.backup_path, result.link_failures);
    globalThis.dispatchEvent(
      new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: platformSlug } }),
    );
    return true;
  } catch (e) {
    showToast(`Failed to replace the file: ${e}`);
    return false;
  }
}
