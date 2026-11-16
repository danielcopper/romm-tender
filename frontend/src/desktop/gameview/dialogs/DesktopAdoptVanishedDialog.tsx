/**
 * The desktop client's backstop dialog: the page said a copy was here and the
 * search at press time names nothing. It claims no cause. Big Picture's
 * `AdoptVanishedModal` is the other drawing of it.
 */

import type { FC } from "react";
import {
  CANCEL_LABEL,
  VANISHED_DOWNLOAD_NOTE,
  VANISHED_INTRO,
  VANISHED_TITLE,
  vanishedDownloadLabel,
} from "../../../utils/adoptWording";
import type { VanishedChoice } from "../../../utils/adoptFlow";
import type { CandidateVanishedResult } from "../../../types";
import {
  DIALOG_ACTIONS_STYLE,
  DIALOG_MUTED_STYLE,
  DIALOG_TEXT_STYLE,
  DesktopDialog,
  dialogButtonStyle,
} from "./DesktopDialog";

interface DesktopAdoptVanishedDialogProps {
  vanished: CandidateVanishedResult;
  onChoice: (choice: VanishedChoice) => void;
}

export const DesktopAdoptVanishedDialog: FC<DesktopAdoptVanishedDialogProps> = ({ vanished, onChoice }) => (
  <DesktopDialog
    titleId="tender-desktop-adopt-vanished-title"
    title={VANISHED_TITLE}
    onDismiss={() => onChoice("cancel")}
  >
    <div style={DIALOG_TEXT_STYLE}>{VANISHED_INTRO}</div>
    <div style={DIALOG_ACTIONS_STYLE}>
      <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice("download")}>
        {vanishedDownloadLabel(vanished)}
      </button>
      <div style={DIALOG_MUTED_STYLE}>{VANISHED_DOWNLOAD_NOTE}</div>
      <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice("cancel")}>
        {CANCEL_LABEL}
      </button>
    </div>
  </DesktopDialog>
);
