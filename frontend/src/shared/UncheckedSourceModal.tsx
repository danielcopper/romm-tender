import { ConfirmModal, showModal } from "@decky/ui";
import { UNCHECKED_START_SOURCE_QUESTION } from "../utils/emulatorSourceWording";

/**
 * The question both launch paths ask where the launch check could not tell
 * whether the source that would start the game is switched off. Resolves
 * `true` on Start, `false` on Cancel (and on outside-click / X, which
 * `ConfirmModal` routes through `onCancel`). It has no title: the question is
 * the whole of what it says.
 */
export function showUncheckedSourceModal(): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    showModal(
      <ConfirmModal
        strDescription={UNCHECKED_START_SOURCE_QUESTION}
        strOKButtonText="Start"
        strCancelButtonText="Cancel"
        onOK={() => resolve(true)}
        onCancel={() => resolve(false)}
      />,
    );
  });
}
