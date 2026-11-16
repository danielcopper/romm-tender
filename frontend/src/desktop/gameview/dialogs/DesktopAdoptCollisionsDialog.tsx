/**
 * The desktop client's one question for a whole set of taken names, asked
 * before a single file has moved. Big Picture's `AdoptCollisionModal` is the
 * other drawing of it, and its header says why neither exit destroys anything.
 */

import type { FC } from "react";
import {
  CANCEL_LABEL,
  COLLISIONS_CONSEQUENCES,
  COLLISIONS_INTRO,
  COLLISIONS_KEEP_LABEL,
  COLLISIONS_REPLACE_LABEL,
  COLLISIONS_TITLE,
  COLLISION_KIND_LABEL,
} from "../../../utils/adoptWording";
import type { CollisionAnswer } from "../../../utils/adoptFlow";
import type { RenameCollision } from "../../../types";
import {
  DIALOG_ACTIONS_STYLE,
  DIALOG_MUTED_STYLE,
  DIALOG_TEXT_STYLE,
  DIALOG_VALUE_STYLE,
  DesktopDialog,
  dialogButtonStyle,
} from "./DesktopDialog";

interface DesktopAdoptCollisionsDialogProps {
  collisions: RenameCollision[];
  onChoice: (choice: CollisionAnswer) => void;
}

export const DesktopAdoptCollisionsDialog: FC<DesktopAdoptCollisionsDialogProps> = ({ collisions, onChoice }) => (
  <DesktopDialog
    titleId="tender-desktop-adopt-collisions-title"
    title={COLLISIONS_TITLE}
    onDismiss={() => onChoice("cancel")}
  >
    <div style={DIALOG_TEXT_STYLE}>{COLLISIONS_INTRO}</div>

    <div style={{ marginBottom: "12px" }}>
      {collisions.map((collision) => (
        <div key={collision.path} style={{ ...DIALOG_VALUE_STYLE, marginBottom: "2px" }}>
          {collision.name} <span style={DIALOG_MUTED_STYLE}>({COLLISION_KIND_LABEL[collision.kind]})</span>
        </div>
      ))}
    </div>

    <div style={DIALOG_ACTIONS_STYLE}>
      <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice("overwrite")}>
        {COLLISIONS_REPLACE_LABEL}
      </button>
      <button type="button" style={dialogButtonStyle("secondary")} onClick={() => onChoice("keep")}>
        {COLLISIONS_KEEP_LABEL}
      </button>
      <div style={DIALOG_MUTED_STYLE}>{COLLISIONS_CONSEQUENCES}</div>
      <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice("cancel")}>
        {CANCEL_LABEL}
      </button>
    </div>
  </DesktopDialog>
);
