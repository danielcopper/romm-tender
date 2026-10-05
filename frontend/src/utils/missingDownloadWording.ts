/**
 * The words of a download whose file is missing: the game page's note naming
 * where the file was, its two actions, and how a "Forget this download" press
 * ended. One home, so the button and its tests state the case the same way.
 *
 * The note names the path the install record holds and says nothing about why
 * the file is gone: a deleted file, a moved folder and an unplugged drive look
 * the same from here, which is why the user, not Tender, decides what happens.
 */

export const DOWNLOAD_AGAIN_LABEL = "Download again";

/** Names what is forgotten — the record of the download — never the game itself. */
export const FORGET_DOWNLOAD_LABEL = "Forget this download";

export const FORGETTING_LABEL = "Forgetting...";

export const FORGET_FAILED_TOAST = "Couldn't forget the download";

export function fileMissingNote(path: string): string {
  return `File missing at ${path}`;
}

export function downloadForgottenToast(romName: string): string {
  return `${romName || "ROM"}: download forgotten`;
}
