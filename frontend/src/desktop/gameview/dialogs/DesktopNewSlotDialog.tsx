/**
 * Desktop modal for creating a new save slot.
 * Shown from SaveManagementCard when the user clicks "+ New Slot".
 */

import { useEffect, useRef, useState, type CSSProperties, type FC } from "react";
import { CANCEL_LABEL } from "../../../utils/launchPromptWording";
import { DIALOG_MUTED_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopNewSlotDialogProps {
  onDismiss: () => void;
  onCreate: (slotName: string) => Promise<string | null>;
  error?: string | null;
}

const INPUT_STYLE: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 10px",
  fontSize: "13px",
  backgroundColor: "rgba(0, 0, 0, 0.4)",
  border: "1px solid rgba(255, 255, 255, 0.2)",
  borderRadius: "3px",
  color: "#ffffff",
  outline: "none",
  marginBottom: "12px",
};

const FOOTER_STYLE: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: "8px",
};

export const DesktopNewSlotDialog: FC<DesktopNewSlotDialogProps> = ({ onDismiss, onCreate, error }) => {
  const [slotName, setSlotName] = useState("");
  const [internalError, setInternalError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const trimmed = slotName.trim();
  const submitDisabled = creating || trimmed === "";
  const displayError = error ?? internalError;

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

      {displayError && (
        <div role="alert" style={{ ...DIALOG_MUTED_STYLE, color: "#ff6b6b", marginBottom: "12px" }}>
          {displayError}
        </div>
      )}

      <div style={FOOTER_STYLE}>
        <button type="button" style={dialogButtonStyle("quiet", creating)} disabled={creating} onClick={onDismiss}>
          {CANCEL_LABEL}
        </button>
        <button
          type="button"
          style={dialogButtonStyle("primary", submitDisabled)}
          disabled={submitDisabled}
          onClick={() => void handleSubmit()}
        >
          {creating ? "Creating…" : "Create Slot"}
        </button>
      </div>
    </DesktopDialog>
  );
};
