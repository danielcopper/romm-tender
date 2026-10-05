/**
 * The words of a download whose file is missing: the game page's note naming
 * where the file was, its two actions, and how a "Forget this download" press
 * ended. One home, so the button and its tests state the case the same way.
 * The note names the path the install record holds and says nothing about why
 * the file is gone, which Tender cannot know.
 */

export const DOWNLOAD_AGAIN_LABEL = "Download again";

/** Names what is forgotten — the record of the download — never the game itself. */
export const FORGET_DOWNLOAD_LABEL = "Forget this download";

export const FORGETTING_LABEL = "Forgetting...";

export const FORGET_FAILED_TOAST = "Couldn't forget the download";

export function fileMissingNote(path: string): string {
  return `File missing at ${path}`;
}

/**
 * How a refused forget is said. `file_present` means the recorded file or
 * folder is on disk — after a missing file, most likely because the drive or
 * folder came back — so the sentence points at playing it, not at deleting it.
 */
export function forgetRefusedToast(result: { reason?: string; message?: string; path?: string }): string {
  if (result.reason === "file_present" && result.path) {
    return `The file is back at ${result.path}. Reopen the game page to play.`;
  }
  return result.message || FORGET_FAILED_TOAST;
}

export function downloadForgottenToast(romName: string): string {
  return `${romName || "ROM"}: download forgotten`;
}
