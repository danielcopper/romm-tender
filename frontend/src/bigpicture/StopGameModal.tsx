import { ConfirmModal, showModal } from "@decky/ui";
import { CANCEL_LABEL, STOP_GAME_DESCRIPTION, STOP_GAME_LABEL, STOP_GAME_TITLE } from "../utils/launchPromptWording";

/**
 * "Stop Game" confirm. Shown before the running overlay's Stop Game action
 * terminates the live emulator.
 *
 * Mirrors the `showModal(...)`-returns-a-Promise pattern of
 * `showCoreChangeModal` / `showFallbackLaunchModal`. Resolves `true` on
 * "Stop Game", `false` on Cancel (and on outside-click / X, which
 * `ConfirmModal` routes through `onCancel`).
 */
export function showStopGameModal(): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    showModal(
      <ConfirmModal
        strTitle={STOP_GAME_TITLE}
        strDescription={STOP_GAME_DESCRIPTION}
        strOKButtonText={STOP_GAME_LABEL}
        strCancelButtonText={CANCEL_LABEL}
        onOK={() => resolve(true)}
        onCancel={() => resolve(false)}
      />,
    );
  });
}
