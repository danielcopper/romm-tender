import { ConfirmModal, showModal } from "@decky/ui";
import { FORGET_BUTTON, FORGET_DESCRIPTION, forgetTitle } from "../../utils/tenderFolderWording";

/**
 * The confirmation around Tender's folder, resolving with the answer:
 * the `showModal(...)`-returns-a-Promise pattern of `showForgetDownloadModal`.
 * Cancel, outside-click and X all resolve as Cancel, which `ConfirmModal`
 * routes through `onCancel`.
 */

/** "Forget what is recorded there": `true` on Forget. */
export function showForgetRecordedModal(folder: string): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    showModal(
      <ConfirmModal
        strTitle={forgetTitle(folder)}
        strDescription={FORGET_DESCRIPTION}
        strOKButtonText={FORGET_BUTTON}
        strCancelButtonText="Cancel"
        onOK={() => resolve(true)}
        onCancel={() => resolve(false)}
      />,
    );
  });
}
