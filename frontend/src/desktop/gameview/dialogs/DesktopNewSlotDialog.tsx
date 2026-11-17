/**
 * Desktop modal for creating a new save slot.
 * Shown from SaveManagementCard when the user clicks "+ New Slot".
 */

import { useEffect, useRef, useState, type CSSProperties, type FC } from "react";
import { detach } from "../../../utils/detach";
import { CANCEL_LABEL } from "../../../utils/launchPromptWording";
import { DIALOG_MUTED_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

export interface DesktopNewSlotDialogProps {
  onDismiss: () => void;
  onCreate: (slotName: string) => Promise<string | null | void> | string | null | void;
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
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    inputRef.current?.focus();
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const trimmed = slotName.trim();
  const displayError = error ?? internalError;

  const handleSubmit = () => {
    if (creating || trimmed === "") return;
    setInternalError(null);
    const result = onCreate(trimmed);
    if (result instanceof Promise) {
      setCreating(true);
      detach(
        result
          .then((err) => {
            if (mountedRef.current && typeof err === "string" && err !== "") {
              setInternalError(err);
            }
          })
          .finally(() => {
            if (mountedRef.current) setCreating(false);
          }),
      );
    } else if (typeof result === "string" && result !== "") {
      setInternalError(result);
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
          if (e.key === "Enter") handleSubmit();
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
          style={dialogButtonStyle("primary", trimmed === "" || creating)}
          disabled={trimmed === "" || creating}
          onClick={handleSubmit}
        >
          {creating ? "Creating…" : "Create Slot"}
        </button>
      </div>
    </DesktopDialog>
  );
};
