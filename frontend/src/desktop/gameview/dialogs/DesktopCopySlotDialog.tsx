/**
 * Desktop modal for copying a save to another slot.
 * Shown from SaveManagementCard when the user chooses "Copy to slot...".
 */

import { useState, type CSSProperties, type FC } from "react";
import type { SaveSlotSummary } from "../../../types";
import { CANCEL_LABEL } from "../../../utils/launchPromptWording";
import { displaySlot, eligibleCopyTargetSlots } from "../../../utils/saveHelpers";
import { DIALOG_MUTED_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

export interface DesktopCopySlotDialogProps {
  sourceSlot: string;
  availableSlots: SaveSlotSummary[];
  onDismiss: () => void;
  onCopy: (targetSlot: string) => Promise<void> | void;
}

const INPUT_STYLE: CSSProperties = {
  flex: 1,
  padding: "6px 10px",
  fontSize: "12px",
  backgroundColor: "rgba(0, 0, 0, 0.4)",
  border: "1px solid rgba(255, 255, 255, 0.2)",
  borderRadius: "3px",
  color: "#ffffff",
  outline: "none",
};

export const DesktopCopySlotDialog: FC<DesktopCopySlotDialogProps> = ({
  sourceSlot,
  availableSlots,
  onDismiss,
  onCopy,
}) => {
  const [newSlotInput, setNewSlotInput] = useState("");
  const targetSlots = eligibleCopyTargetSlots(availableSlots, sourceSlot);
  const trimmed = newSlotInput.trim();

  const handleCreateAndCopy = () => {
    if (trimmed !== "") {
      void onCopy(trimmed);
    }
  };

  return (
    <DesktopDialog titleId="tender-desktop-copy-slot-title" title="Copy save to slot" onDismiss={onDismiss}>
      <div style={DIALOG_TEXT_STYLE}>
        Copies this save into the chosen slot, which becomes the active slot. The original save is kept.
      </div>

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
        <div style={{ ...DIALOG_MUTED_STYLE, marginBottom: "14px" }}>No other slots yet — create one below.</div>
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
            style={dialogButtonStyle("primary", trimmed === "")}
            disabled={trimmed === ""}
            onClick={handleCreateAndCopy}
          >
            Create & Copy
          </button>
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "16px" }}>
        <button type="button" style={dialogButtonStyle("quiet")} onClick={onDismiss}>
          {CANCEL_LABEL}
        </button>
      </div>
    </DesktopDialog>
  );
};
