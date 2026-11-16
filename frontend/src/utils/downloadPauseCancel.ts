/**
 * Pausing and cancelling a download. What either moves comes with the backend's
 * frame for it, so a refusal or a call with no answer is only logged — except
 * where a cancel is refused because there is nothing to cancel.
 */

import { cancelDownload, pauseDownload } from "../api/backend";
import { logFailure, logRefusal } from "./backendLog";
import { getDownloadState, isDownloadUnderWay, removeDownload } from "./downloadStore";

/** Pause the ROM's download; the paused frame is what shows it. */
export function executeDownloadPause(romId: number, tag: string): Promise<void> {
  return pauseDownload(romId)
    .then((result) => {
      if (!result.success) logRefusal(tag, "pause the download of", romId, result);
    })
    .catch((e: unknown) => logFailure(tag, "pause the download of", romId, e));
}

/**
 * Cancel the ROM's download; the cancelled frame is what ends it. A refusal
 * with `no_active_download` gets no frame (`cancel_download`,
 * backend/services/downloads/service.py), so while the download is still under
 * way the ROM is dropped from the download store and `onNothingToCancel` ends
 * the caller's own bar. One that ended meanwhile is left as its last frame put
 * it: `DownloadTransfer.run` (backend/services/downloads/transfer.py) sends that
 * frame before it lets the download go, so a cancel crossing the end is refused
 * after it.
 */
export function executeDownloadCancel(romId: number, tag: string, onNothingToCancel: () => void): Promise<void> {
  return cancelDownload(romId)
    .then((result) => {
      if (result.success) return;
      logRefusal(tag, "cancel the download of", romId, result);
      if (result.reason !== "no_active_download") return;
      const entry = getDownloadState().find((d) => d.rom_id === romId);
      if (entry !== undefined && !isDownloadUnderWay(entry)) return;
      removeDownload(romId);
      onNothingToCancel();
    })
    .catch((e: unknown) => logFailure(tag, "cancel the download of", romId, e));
}
