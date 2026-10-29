/**
 * The desktop drawing of `LaunchPrompts.askOfflineDrift` (`utils/launchVerdict.ts`).
 * `shared/OfflineDriftModal` is its gamepad drawing.
 */

import type { FC } from "react";
import {
  CANCEL_LABEL,
  OFFLINE_DRIFT_DESCRIPTION,
  OFFLINE_DRIFT_RETRY_LABEL,
  OFFLINE_DRIFT_START_LABEL,
  OFFLINE_DRIFT_TITLE,
} from "../../../utils/launchPromptWording";
import { DIALOG_ACTIONS_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopOfflineDriftDialogProps {
  onChoice: (choice: "start_anyway" | "retry" | "cancel") => void;
}

export const DesktopOfflineDriftDialog: FC<DesktopOfflineDriftDialogProps> = ({ onChoice }) => (
  <DesktopDialog
    titleId="tender-desktop-offline-drift-title"
    title={OFFLINE_DRIFT_TITLE}
    onDismiss={() => onChoice("cancel")}
  >
    <div style={DIALOG_TEXT_STYLE}>{OFFLINE_DRIFT_DESCRIPTION}</div>
    <div style={DIALOG_ACTIONS_STYLE}>
      <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice("start_anyway")}>
        {OFFLINE_DRIFT_START_LABEL}
      </button>
      <button type="button" style={dialogButtonStyle("secondary")} onClick={() => onChoice("retry")}>
        {OFFLINE_DRIFT_RETRY_LABEL}
      </button>
      <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice("cancel")}>
        {CANCEL_LABEL}
      </button>
    </div>
  </DesktopDialog>
);
