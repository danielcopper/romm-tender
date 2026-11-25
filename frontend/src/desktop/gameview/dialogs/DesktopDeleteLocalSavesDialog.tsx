import type { FC } from "react";
import {
  CANNOT_BE_UNDONE,
  DELETE_LOCAL_SAVES_DESCRIPTION,
  DELETE_LOCAL_SAVES_TITLE,
} from "../../../utils/saveSlotWording";
import { DANGER_TEXT_RED } from "../styles";
import { DIALOG_TEXT_STYLE, DesktopDialog, DesktopDialogFooter, dialogButtonStyle } from "./DesktopDialog";

interface DesktopDeleteLocalSavesDialogProps {
  onDismiss: () => void;
  onConfirm: () => void;
}

export const DesktopDeleteLocalSavesDialog: FC<DesktopDeleteLocalSavesDialogProps> = ({ onDismiss, onConfirm }) => (
  <DesktopDialog
    titleId="tender-desktop-delete-local-saves-title"
    title={DELETE_LOCAL_SAVES_TITLE}
    onDismiss={onDismiss}
  >
    <div style={DIALOG_TEXT_STYLE}>{DELETE_LOCAL_SAVES_DESCRIPTION}</div>
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
