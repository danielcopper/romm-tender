/**
 * Desktop modal for creating a new save slot.
 * Shown from SaveManagementCard when the user clicks "+ New Slot".
 */

import { useEffect, useRef, useState, type FC } from "react";
import { DANGER_TEXT_RED } from "../styles";
import {
  DIALOG_MUTED_STYLE,
  DIALOG_TEXT_STYLE,
  DesktopDialog,
  DesktopDialogFooter,
  dialogButtonStyle,
  dialogInputStyle,
} from "./DesktopDialog";

interface DesktopNewSlotDialogProps {
  onDismiss: () => void;
  onCreate: (slotName: string) => Promise<string | null>;
}

const INPUT_STYLE = dialogInputStyle({
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 10px",
  fontSize: "13px",
  marginBottom: "12px",
});

export const DesktopNewSlotDialog: FC<DesktopNewSlotDialogProps> = ({ onDismiss, onCreate }) => {
  const [slotName, setSlotName] = useState("");
  const [internalError, setInternalError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const trimmed = slotName.trim();
  const submitDisabled = creating || trimmed === "";

  const handleSubmit = async () => {
    if (submitDisabled) return;
    setInternalError(null);
    setCreating(true);
    try {
      const err = await onCreate(trimmed);
      if (err) setInternalError(err);
    } finally {
      setCreating(false);
    }
  };

  return (
    <DesktopDialog
      titleId="tender-desktop-new-slot-title"
      title="New Save Slot"
      onDismiss={creating ? undefined : onDismiss}
    >
      <div style={DIALOG_TEXT_STYLE}>
        Enter a name for the new save slot. It will become the active slot immediately.
      </div>

      <input
        ref={inputRef}
        type="text"
        placeholder="Slot Name (e.g. speedrun, casual)"
        value={slotName}
        disabled={creating}
        onChange={(e) => setSlotName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void handleSubmit();
        }}
        style={INPUT_STYLE}
      />

      {internalError ? (
        <div role="alert" style={{ ...DIALOG_MUTED_STYLE, color: DANGER_TEXT_RED, marginBottom: "12px" }}>
          {internalError}
        </div>
      ) : null}

      <DesktopDialogFooter onDismiss={onDismiss} dismissDisabled={creating}>
        <button
          type="button"
          style={dialogButtonStyle("primary", submitDisabled)}
          disabled={submitDisabled}
          onClick={() => void handleSubmit()}
        >
          {creating ? "Creating…" : "Create Slot"}
        </button>
      </DesktopDialogFooter>
    </DesktopDialog>
  );
};
