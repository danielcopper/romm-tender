import { ConfirmModal, showModal } from "@decky/ui";
import { UNCHECKED_START_SOURCE_QUESTION, UNCHECKED_START_SOURCE_TITLE } from "../utils/emulatorSourceWording";

/**
 * The question both launch paths ask where the launch check could not tell
 * whether the source that would start the game is switched off. Resolves
 * `true` on Start, `false` on Cancel (and on outside-click / X, which
 * `ConfirmModal` routes through `onCancel`).
 */
export function showUncheckedSourceModal(): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    showModal(
      <ConfirmModal
        strTitle={UNCHECKED_START_SOURCE_TITLE}
        strDescription={UNCHECKED_START_SOURCE_QUESTION}
        strOKButtonText="Start"
        strCancelButtonText="Cancel"
        onOK={() => resolve(true)}
        onCancel={() => resolve(false)}
      />,
    );
  });
}
