import { ConfirmModal, showModal } from "@decky/ui";
import {
  CREATE_BUTTON,
  FORGET_BUTTON,
  FORGET_DESCRIPTION,
  KEEP_AS_TYPED,
  TENDER_FOLDER_SECTION,
  createMissingQuestion,
  forgetTitle,
  tenderNamedQuestion,
  useParentButton,
} from "../../utils/tenderFolderWording";

/**
 * The confirmations around Tender's folder, each resolving with the answer:
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

/** Folders of a typed place that do not exist yet: `true` on Create. */
export function showCreateMissingModal(place: string, existing: string, missing: string): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    showModal(
      <ConfirmModal
        strTitle={TENDER_FOLDER_SECTION}
        strDescription={createMissingQuestion(place, existing, missing)}
        strOKButtonText={CREATE_BUTTON}
        strCancelButtonText="Cancel"
        onOK={() => resolve(true)}
        onCancel={() => resolve(false)}
      />,
    );
  });
}

/**
 * A typed place whose last folder is named Tender: `"parent"` to use the
 * folder above it, `"typed"` to keep it, `"cancel"` to choose nothing. Cancel
 * is a third button because dismissing the dialog must not choose a place.
 */
export function showTenderNamedModal(place: string, parent: string): Promise<"parent" | "typed" | "cancel"> {
  return new Promise<"parent" | "typed" | "cancel">((resolve) => {
    showModal(
      <ConfirmModal
        strTitle={TENDER_FOLDER_SECTION}
        strDescription={tenderNamedQuestion(place, parent)}
        strOKButtonText={useParentButton(parent)}
        strMiddleButtonText={KEEP_AS_TYPED}
        strCancelButtonText="Cancel"
        onOK={() => resolve("parent")}
        onMiddleButton={() => resolve("typed")}
        onCancel={() => resolve("cancel")}
      />,
    );
  });
}
