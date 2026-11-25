/**
 * Picker for the "Copy to slot…" action. Lists the ROM's existing slots as copy
 * targets — minus the source slot (a same-slot copy is just a rollback) and
 * minus the read-only legacy no-slot bucket ("") — plus a "New slot…" text field
 * for creating a fresh named slot. Owns only its text-field state; the chosen
 * target is handed to `onSubmit` (trimmed, never empty) and the copy side effects
 * belong to the parent (`useCopyToSlot`).
 */

import { useState, FC, ChangeEvent, KeyboardEvent, type CSSProperties } from "react";
import { ModalRoot, DialogButton, TextField } from "@decky/ui";
import type { SaveSlotSummary } from "../../types";
import { displaySlot, eligibleCopyTargetSlots } from "../../utils/saveHelpers";
import { COPY_TO_SLOT_DESCRIPTION, COPY_TO_SLOT_EMPTY_TARGETS } from "../../utils/saveSlotWording";

const MODAL_CONTAINER_STYLE: CSSProperties = { padding: "16px", minWidth: "320px" };
const MODAL_TITLE_STYLE: CSSProperties = { fontSize: "16px", fontWeight: "bold", marginBottom: "4px", color: "#fff" };
const MODAL_DESC_STYLE: CSSProperties = {
  fontSize: "13px",
  color: "rgba(255,255,255,0.6)",
  marginBottom: "16px",
  lineHeight: "1.4",
};

export const CopyToSlotModal: FC<{
  availableSlots: SaveSlotSummary[];
  sourceSlot: string;
  onSubmit: (target: string) => void;
  closeModal?: () => void;
}> = ({ availableSlots, sourceSlot, onSubmit, closeModal }) => {
  const [newName, setNewName] = useState("");

  const targetSlots = eligibleCopyTargetSlots(availableSlots, sourceSlot);

  const pick = (target: string) => {
    const trimmed = target.trim();
    if (!trimmed) return;
    closeModal?.();
    onSubmit(trimmed);
  };

  const existingRows =
    targetSlots.length > 0
      ? targetSlots.map((name) => (
          <DialogButton
            key={`target-${name}`}
            style={{ padding: "6px 12px", width: "100%", textAlign: "left" as const }}
            onClick={() => {
              pick(name);
            }}
          >
            {displaySlot(name)}
          </DialogButton>
        ))
      : [
          <div
            key="no-existing"
            style={{ fontSize: "12px", color: "rgba(255,255,255,0.5)", fontStyle: "italic" as const }}
          >
            {COPY_TO_SLOT_EMPTY_TARGETS}
          </div>,
        ];

  return (
    <ModalRoot {...(closeModal !== undefined ? { closeModal } : {})}>
      <div style={MODAL_CONTAINER_STYLE}>
        <div style={MODAL_TITLE_STYLE}>Copy save to slot</div>
        <div style={MODAL_DESC_STYLE}>{COPY_TO_SLOT_DESCRIPTION}</div>
        {/* Existing-slot targets. */}
        <div style={{ display: "flex", flexDirection: "column" as const, gap: "8px" }}>{existingRows}</div>
        {/* New-slot creator. */}
        <div style={{ marginTop: "16px" }}>
          <TextField
            focusOnMount={false}
            label="New slot…"
            value={newName}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setNewName(e.target.value)}
            // Enter (the on-screen keyboard's "Eingabe" key) submits the typed name,
            // same as the "Create & copy here" button — pick() no-ops on an empty
            // name, so a stray Enter in a blank field does nothing.
            onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
              if (e.key === "Enter") pick(newName);
            }}
          />
          <DialogButton
            style={{ marginTop: "8px", padding: "6px 12px", width: "100%" }}
            disabled={newName.trim() === ""}
            onClick={() => {
              pick(newName);
            }}
          >
            Create & copy here
          </DialogButton>
        </div>
      </div>
    </ModalRoot>
  );
};
