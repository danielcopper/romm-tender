/**
 * Desktop confirmation dialog for deleting a save slot.
 * Shown from SaveManagementCard when the user confirms deleting an inactive slot.
 */

import type { FC } from "react";
import type { SlotDeleteInfo } from "../../../types";
import { CANCEL_LABEL } from "../../../utils/launchPromptWording";
import { formatSlotDeleteLines } from "../../../utils/saveSlotWording";
import { DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopDeleteSlotDialogProps {
  deleteInfo: SlotDeleteInfo;
  onDismiss: () => void;
  onConfirm: () => Promise<void> | void;
}

export const DesktopDeleteSlotDialog: FC<DesktopDeleteSlotDialogProps> = ({ deleteInfo, onDismiss, onConfirm }) => (
  <DesktopDialog titleId="tender-desktop-delete-slot-title" title="Delete Slot" onDismiss={onDismiss}>
    {formatSlotDeleteLines(deleteInfo).map((line) => (
      <div key={line} style={DIALOG_TEXT_STYLE}>
        {line}
      </div>
    ))}
    <div style={{ fontSize: "13px", color: "#ff6b6b", fontWeight: 600, marginBottom: "16px" }}>
      This cannot be undone.
    </div>

    <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
      <button type="button" style={dialogButtonStyle("quiet")} onClick={onDismiss}>
        {CANCEL_LABEL}
      </button>
      <button type="button" style={dialogButtonStyle("danger")} onClick={() => void onConfirm()}>
        Delete
      </button>
    </div>
  </DesktopDialog>
);
