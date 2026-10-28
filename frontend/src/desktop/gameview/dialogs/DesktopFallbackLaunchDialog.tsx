/**
 * The desktop client's fallback-launch prompt, asked before a start when the
 * pre-launch sync failed without surfacing a conflict, or the check got no
 * answer in time. `shared/FallbackLaunchModal` is its gamepad drawing.
 */

import type { FC } from "react";
import {
  CANCEL_LABEL,
  FALLBACK_LAUNCH_LABEL,
  FALLBACK_LAUNCH_TITLE,
  fallbackLaunchDescription,
} from "../../../utils/launchPromptWording";
import { DIALOG_ACTIONS_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopFallbackLaunchDialogProps {
  message?: string | undefined;
  onChoice: (proceed: boolean) => void;
}

export const DesktopFallbackLaunchDialog: FC<DesktopFallbackLaunchDialogProps> = ({ message, onChoice }) => (
  <DesktopDialog
    titleId="tender-desktop-fallback-launch-title"
    title={FALLBACK_LAUNCH_TITLE}
    onDismiss={() => onChoice(false)}
  >
    <div style={DIALOG_TEXT_STYLE}>{fallbackLaunchDescription(message)}</div>
    <div style={DIALOG_ACTIONS_STYLE}>
      <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice(true)}>
        {FALLBACK_LAUNCH_LABEL}
      </button>
      <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice(false)}>
        {CANCEL_LABEL}
      </button>
    </div>
  </DesktopDialog>
);
