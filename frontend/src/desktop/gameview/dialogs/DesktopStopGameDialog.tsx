/**
 * The desktop client's Stop confirm, asked before the Play button's Stop ends
 * the running emulator. `bigpicture/StopGameModal` is its gamepad drawing.
 */

import type { FC } from "react";
import {
  CANCEL_LABEL,
  STOP_GAME_DESCRIPTION,
  STOP_GAME_LABEL,
  STOP_GAME_TITLE,
} from "../../../utils/launchPromptWording";
import { DIALOG_ACTIONS_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopStopGameDialogProps {
  onChoice: (stop: boolean) => void;
}

export const DesktopStopGameDialog: FC<DesktopStopGameDialogProps> = ({ onChoice }) => (
  <DesktopDialog titleId="tender-desktop-stop-game-title" title={STOP_GAME_TITLE} onDismiss={() => onChoice(false)}>
    <div style={DIALOG_TEXT_STYLE}>{STOP_GAME_DESCRIPTION}</div>
    <div style={DIALOG_ACTIONS_STYLE}>
      <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice(true)}>
        {STOP_GAME_LABEL}
      </button>
      <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice(false)}>
        {CANCEL_LABEL}
      </button>
    </div>
  </DesktopDialog>
);
