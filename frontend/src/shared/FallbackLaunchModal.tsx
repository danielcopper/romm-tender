import { ConfirmModal, showModal } from "@decky/ui";
import {
  CANCEL_LABEL,
  FALLBACK_LAUNCH_LABEL,
  FALLBACK_LAUNCH_TITLE,
  fallbackLaunchDescription,
} from "../utils/launchPromptWording";

/**
 * "Save Sync Unavailable" fallback confirm (ADR-0015). Shown when an online
 * pre-launch sync failed without surfacing a conflict, or when a step of the
 * launch check got no answer in time — asks whether to launch with local saves
 * anyway.
 *
 * Mirrors the `showModal(...)`-returns-a-Promise pattern of
 * `showCoreChangeModal` / `showOfflineDriftModal`. Resolves `true` on
 * "Launch Anyway", `false` on Cancel (and on outside-click / X, which
 * `ConfirmModal` routes through `onCancel`).
 */
export function showFallbackLaunchModal(message?: string): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    showModal(
      <ConfirmModal
        strTitle={FALLBACK_LAUNCH_TITLE}
        strDescription={fallbackLaunchDescription(message)}
        strOKButtonText={FALLBACK_LAUNCH_LABEL}
        strCancelButtonText={CANCEL_LABEL}
        onOK={() => resolve(true)}
        onCancel={() => resolve(false)}
      />,
    );
  });
}
