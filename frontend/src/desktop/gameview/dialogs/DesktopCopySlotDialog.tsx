/**
 * Desktop modal for copying a save to another slot.
 * Shown from SaveManagementCard when the user chooses "Copy to slot...".
 */

import { useState, type FC } from "react";
import type { SaveSlotSummary } from "../../../types";
import { displaySlot, eligibleCopyTargetSlots } from "../../../utils/saveHelpers";
import { COPY_TO_SLOT_DESCRIPTION, COPY_TO_SLOT_EMPTY_TARGETS } from "../../../utils/saveSlotWording";
import {
  DIALOG_MUTED_STYLE,
  DIALOG_TEXT_STYLE,
  DesktopDialog,
  DesktopDialogFooter,
  dialogButtonStyle,
  dialogInputStyle,
} from "./DesktopDialog";

interface DesktopCopySlotDialogProps {
  sourceSlot: string;
  availableSlots: SaveSlotSummary[];
  onDismiss: () => void;
  onCopy: (targetSlot: string) => Promise<void> | void;
}

const INPUT_STYLE = dialogInputStyle({
  flex: 1,
  padding: "6px 10px",
  fontSize: "12px",
});

export const DesktopCopySlotDialog: FC<DesktopCopySlotDialogProps> = ({
  sourceSlot,
  availableSlots,
  onDismiss,
  onCopy,
}) => {
  const [newSlotInput, setNewSlotInput] = useState("");
  const targetSlots = eligibleCopyTargetSlots(availableSlots, sourceSlot);
  const trimmed = newSlotInput.trim();
  const createDisabled = trimmed === "";

  const handleCreateAndCopy = () => {
    if (!createDisabled) {
      void onCopy(trimmed);
    }
  };

  return (
    <DesktopDialog titleId="tender-desktop-copy-slot-title" title="Copy save to slot" onDismiss={onDismiss}>
      <div style={DIALOG_TEXT_STYLE}>{COPY_TO_SLOT_DESCRIPTION}</div>

      {targetSlots.length > 0 ? (
        <div style={{ marginBottom: "16px" }}>
          <div style={{ ...DIALOG_MUTED_STYLE, marginBottom: "6px" }}>Existing slots:</div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {targetSlots.map((slotName) => (
              <button
                key={`target-slot-${slotName}`}
                type="button"
                style={dialogButtonStyle("secondary")}
                onClick={() => void onCopy(slotName)}
              >
                {displaySlot(slotName)}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ ...DIALOG_MUTED_STYLE, marginBottom: "14px" }}>{COPY_TO_SLOT_EMPTY_TARGETS}</div>
      )}

      <div style={{ borderTop: "1px solid rgba(255, 255, 255, 0.08)", paddingTop: "12px" }}>
        <div style={{ ...DIALOG_MUTED_STYLE, marginBottom: "6px" }}>Or copy to a new slot:</div>
        <div style={{ display: "flex", gap: "8px" }}>
          <input
            type="text"
            placeholder="New slot name…"
            value={newSlotInput}
            onChange={(e) => setNewSlotInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreateAndCopy();
            }}
            style={INPUT_STYLE}
          />
          <button
            type="button"
            style={dialogButtonStyle("primary", createDisabled)}
            disabled={createDisabled}
            onClick={handleCreateAndCopy}
          >
            Create & Copy
          </button>
        </div>
      </div>

      <DesktopDialogFooter marginTop="16px" onDismiss={onDismiss} />
    </DesktopDialog>
  );
};
