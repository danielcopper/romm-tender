/**
 * The desktop client's confirmation dialog for BIOS file actions — replacing a
 * file at an emulator's path (`tone="primary"`) or deleting Tender's BIOS
 * downloads (`tone="danger"`).
 */

import type { FC } from "react";
import { CANCEL_LABEL } from "../../../utils/launchPromptWording";
import type { BiosConfirmCopy } from "../../../utils/biosConfirmWording";
import { DIALOG_ACTIONS_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopBiosConfirmDialogProps {
  copy: BiosConfirmCopy;
  tone: "primary" | "danger";
  onChoice: (confirmed: boolean) => void;
}

export const DesktopBiosConfirmDialog: FC<DesktopBiosConfirmDialogProps> = ({ copy, tone, onChoice }) => (
  <DesktopDialog titleId="tender-desktop-bios-confirm-title" title={copy.title} onDismiss={() => onChoice(false)}>
    <div style={DIALOG_TEXT_STYLE}>{copy.description}</div>
    <div style={DIALOG_ACTIONS_STYLE}>
      <button type="button" style={dialogButtonStyle(tone)} onClick={() => onChoice(true)}>
        {copy.confirmLabel}
      </button>
      <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice(false)}>
        {CANCEL_LABEL}
      </button>
    </div>
  </DesktopDialog>
);
