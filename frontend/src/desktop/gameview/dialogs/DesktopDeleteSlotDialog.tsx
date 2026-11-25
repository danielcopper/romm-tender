/**
 * The confirmation for deleting a save slot, opened by an inactive slot's
 * Delete press in DesktopSlotPanel.
 */

import type { FC } from "react";
import type { SlotDeleteInfo } from "../../../types";
import { CANNOT_BE_UNDONE, formatSlotDeleteLines } from "../../../utils/saveSlotWording";
import { DANGER_TEXT_RED } from "../styles";
import { DIALOG_TEXT_STYLE, DesktopDialog, DesktopDialogFooter, dialogButtonStyle } from "./DesktopDialog";

interface DesktopDeleteSlotDialogProps {
  deleteInfo: SlotDeleteInfo;
  onDismiss: () => void;
  onConfirm: () => void;
}

export const DesktopDeleteSlotDialog: FC<DesktopDeleteSlotDialogProps> = ({ deleteInfo, onDismiss, onConfirm }) => (
  <DesktopDialog titleId="tender-desktop-delete-slot-title" title="Delete Slot" onDismiss={onDismiss}>
    {formatSlotDeleteLines(deleteInfo).map((line) => (
      <div key={line} style={DIALOG_TEXT_STYLE}>
        {line}
      </div>
    ))}
    <div style={{ fontSize: "13px", color: DANGER_TEXT_RED, fontWeight: 600, marginBottom: "16px" }}>
      {CANNOT_BE_UNDONE}
    </div>

    <DesktopDialogFooter onDismiss={onDismiss}>
      <button type="button" style={dialogButtonStyle("danger")} onClick={onConfirm}>
        Delete
      </button>
    </DesktopDialogFooter>
  </DesktopDialog>
);
