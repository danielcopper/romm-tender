import { ConfirmModal, showModal } from "@decky/ui";
import {
  FORGET_CONFIRM_BUTTON,
  FORGET_DOWNLOAD_LABEL,
  forgetConfirmDescription,
} from "../utils/missingDownloadWording";

/**
 * "Forget this download" confirm, shown before the forget of a download whose
 * file is missing. It names the path the install record holds, which the play
 * row's one-line note leaves out.
 *
 * Mirrors `showStopGameModal`'s `showModal(...)`-returns-a-Promise pattern.
 * Resolves `true` on "Forget", `false` on Cancel (and on outside-click / X,
 * which `ConfirmModal` routes through `onCancel`).
 */
export function showForgetDownloadModal(romName: string, path: string): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    showModal(
      <ConfirmModal
        strTitle={FORGET_DOWNLOAD_LABEL}
        strDescription={forgetConfirmDescription(romName, path)}
        strOKButtonText={FORGET_CONFIRM_BUTTON}
        strCancelButtonText="Cancel"
        onOK={() => resolve(true)}
        onCancel={() => resolve(false)}
      />,
    );
  });
}
