/**
 * The words of a download whose file is missing: the game page's note, the
 * menu behind its arrow, its two actions, the confirmation a "Forget this
 * download" asks, and how the forget ended. One home, so the button and its
 * tests state the case the same way. The note stays one short line beside the
 * play row's stats; the path the install record holds is named in the menu and
 * by the confirmation, and nothing says why the file is gone, which Tender
 * cannot know.
 */

/** The note under the split button, and the title of its arrow's menu. */
export const FILE_MISSING_LABEL = "File missing";

export const DOWNLOAD_AGAIN_LABEL = "Download again";

/** Names what is forgotten — the record of the download — never the game itself. */
export const FORGET_DOWNLOAD_LABEL = "Forget this download";

export const FORGETTING_LABEL = "Forgetting...";

export const FORGET_FAILED_TOAST = "Couldn't forget the download";

export const FORGET_CONFIRM_BUTTON = "Forget";

export function forgetConfirmDescription(romName: string, path: string): string {
  return `Forget the download of ${romName || "this game"}? Its file is missing at ${path}.`;
}

/**
 * How a refused forget is said. `file_present` means the recorded file or
 * folder is on disk — after a missing file, most likely because the drive or
 * folder came back — so the sentence says where it is, not that it failed.
 */
export function forgetRefusedToast(result: { reason?: string; message?: string; path?: string }): string {
  if (result.reason === "file_present" && result.path) {
    return `The file is back at ${result.path}.`;
  }
  return result.message || FORGET_FAILED_TOAST;
}

export function downloadForgottenToast(romName: string): string {
  return `${romName || "ROM"}: download forgotten`;
}
