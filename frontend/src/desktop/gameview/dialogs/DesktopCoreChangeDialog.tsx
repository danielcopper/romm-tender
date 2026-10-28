/**
 * The desktop client's core-change prompt, asked before a start when the
 * launch check's core comparison (`checkCoreChange`) answers that the core
 * changed. `shared/CoreChangeModal` is its gamepad drawing.
 */

import type { FC } from "react";
import {
  CANCEL_LABEL,
  CORE_CHANGE_CONTINUE_LABEL,
  CORE_CHANGE_TITLE,
  CORE_CHANGE_WARNING,
  CORE_CHANGE_WARNING_HEADING,
} from "../../../utils/launchPromptWording";
import {
  DIALOG_ACTIONS_STYLE,
  DIALOG_MUTED_STYLE,
  DIALOG_TEXT_STYLE,
  DesktopDialog,
  dialogButtonStyle,
} from "./DesktopDialog";

interface DesktopCoreChangeDialogProps {
  oldLabel: string;
  newLabel: string;
  onChoice: (proceed: boolean) => void;
}

export const DesktopCoreChangeDialog: FC<DesktopCoreChangeDialogProps> = ({ oldLabel, newLabel, onChoice }) => {
  return (
    <DesktopDialog
      titleId="tender-desktop-core-change-title"
      title={CORE_CHANGE_TITLE}
      onDismiss={() => onChoice(false)}
    >
      <div style={{ ...DIALOG_MUTED_STYLE, marginBottom: "16px" }}>
        {oldLabel} → {newLabel}
      </div>

      <div
        style={{
          padding: "10px",
          background: "rgba(255, 152, 0, 0.15)",
          borderRadius: "4px",
          border: "1px solid rgba(255, 152, 0, 0.3)",
          marginBottom: "16px",
        }}
      >
        <div style={{ fontSize: "12px", color: "#ffb74d", marginBottom: "6px", fontWeight: "bold" }}>
          {CORE_CHANGE_WARNING_HEADING}
        </div>
        <div style={{ ...DIALOG_TEXT_STYLE, marginBottom: 0 }}>{CORE_CHANGE_WARNING}</div>
      </div>

      <div style={DIALOG_ACTIONS_STYLE}>
        <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice(true)}>
          {CORE_CHANGE_CONTINUE_LABEL}
        </button>
        <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice(false)}>
          {CANCEL_LABEL}
        </button>
      </div>
    </DesktopDialog>
  );
};
