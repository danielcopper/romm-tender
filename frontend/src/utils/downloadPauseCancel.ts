/**
 * Pausing and cancelling a download from a game page's button. What either moves
 * on the button comes with the backend's frame for it, so a refusal or a call
 * with no answer is only logged — except where a cancel is refused because
 * there is nothing to cancel.
 */

import { cancelDownload, pauseDownload } from "../api/backend";
import { logFailure, logRefusal } from "./backendLog";
import { removeDownload } from "./downloadStore";

/** Pause the ROM's download; the paused frame is what shows it. */
export function executeDownloadPause(romId: number, tag: string): Promise<void> {
  return pauseDownload(romId).then(
    (result) => {
      if (!result.success) logRefusal(tag, "pause the download of", romId, result);
    },
    (e: unknown) => logFailure(tag, "pause the download of", romId, e),
  );
}

/**
 * Cancel the ROM's download; the cancelled frame is what ends it. The backend
 * answers `no_active_download` only where it holds neither a transfer nor a
 * paused entry for the ROM, so no frame will come: the ROM is dropped from the
 * download store and `onNothingToCancel` ends the caller's own bar.
 */
export function executeDownloadCancel(romId: number, tag: string, onNothingToCancel: () => void): Promise<void> {
  return cancelDownload(romId).then(
    (result) => {
      if (result.success) return;
      logRefusal(tag, "cancel the download of", romId, result);
      if (result.reason !== "no_active_download") return;
      removeDownload(romId);
      onNothingToCancel();
    },
    (e: unknown) => logFailure(tag, "cancel the download of", romId, e),
  );
}
