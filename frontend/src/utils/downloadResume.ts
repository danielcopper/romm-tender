/**
 * Resuming a paused download from a game page's button, and saying why when it
 * is refused.
 */

import { isTargetOccupied, logError, logWarn, resumeDownload } from "../api/backend";
import { RESUME_REFUSED_TOAST, RESUME_TARGET_OCCUPIED_TOAST, RESUME_THREW_TOAST } from "./adoptWording";
import { showToast } from "./toast";

/**
 * Resume a paused download. The success path is fire-and-forget — the backend
 * re-begins the transfer from the partial .tmp and emits "downloading" frames,
 * which clear the paused state where they are listened for. A REFUSAL has to be
 * said out loud: a resume can be turned down (content appeared at the game's
 * location while it sat paused, or a version switch stranded this target), and
 * a silent refusal leaves the user pressing a button that does nothing, with
 * Cancel — which discards the transferred bytes — as their only way out.
 */
export function executeDownloadResume(romId: number, tag: string): Promise<void> {
  return resumeDownload(romId)
    .then((result) => {
      if (result.success) return;
      logWarn(
        `${tag}: the backend refused to resume the download of romId=${romId}: ${result.reason ?? "no reason"}: ${result.message}`,
      );
      showToast(isTargetOccupied(result) ? RESUME_TARGET_OCCUPIED_TOAST : result.message || RESUME_REFUSED_TOAST);
    })
    .catch((e: unknown) => {
      logError(`${tag}: could not resume the download of romId=${romId}: ${String(e)}`);
      showToast(RESUME_THREW_TOAST);
    });
}
