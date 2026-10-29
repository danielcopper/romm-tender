/**
 * The desktop drawing of `LaunchPrompts.confirmUncheckedSource` (`utils/launchVerdict.ts`).
 * `shared/UncheckedSourceModal` is its gamepad drawing.
 */

import type { FC } from "react";
import { UNCHECKED_START_SOURCE_QUESTION, UNCHECKED_START_SOURCE_TITLE } from "../../../utils/emulatorSourceWording";
import { CANCEL_LABEL } from "../../../utils/launchPromptWording";
import { DIALOG_ACTIONS_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopUncheckedSourceDialogProps {
  onChoice: (start: boolean) => void;
}

export const DesktopUncheckedSourceDialog: FC<DesktopUncheckedSourceDialogProps> = ({ onChoice }) => (
  <DesktopDialog
    titleId="tender-desktop-unchecked-source-title"
    title={UNCHECKED_START_SOURCE_TITLE}
    onDismiss={() => onChoice(false)}
  >
    <div style={DIALOG_TEXT_STYLE}>{UNCHECKED_START_SOURCE_QUESTION}</div>
    <div style={DIALOG_ACTIONS_STYLE}>
      <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice(true)}>
        Start
      </button>
      <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice(false)}>
        {CANCEL_LABEL}
      </button>
    </div>
  </DesktopDialog>
);
