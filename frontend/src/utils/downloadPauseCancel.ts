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
 * backend/services/downloads/service.py), so the ROM is dropped from the
 * download store and `onNothingToCancel` ends the caller's own bar — unless the
 * store took the download as ended while the cancel was out: `DownloadTransfer.run`
 * (backend/services/downloads/transfer.py) sends a download's last frame
 * before it lets the download go, so a cancel crossing the end is refused after
 * that frame. An ended entry already there at the send is an earlier attempt's,
 * and says nothing about this one.
 */
export function executeDownloadCancel(romId: number, tag: string, onNothingToCancel: () => void): Promise<void> {
  const entryOf = () => getDownloadState().find((d) => d.rom_id === romId);
  const sentAgainst = entryOf();
  return cancelDownload(romId)
    .then((result) => {
      if (result.success) return;
      logRefusal(tag, "cancel the download of", romId, result);
      if (result.reason !== "no_active_download") return;
      const entry = entryOf();
      if (entry !== undefined && entry !== sentAgainst && !isDownloadUnderWay(entry)) return;
      removeDownload(romId);
      onNothingToCancel();
    })
    .catch((e: unknown) => logFailure(tag, "cancel the download of", romId, e));
}
