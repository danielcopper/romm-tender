/**
 * Editor for the extra HTTP headers sent to the RomM server, for a server behind
 * an authenticating reverse proxy (Pangolin, Cloudflare Access, Authelia,
 * Authentik forward-auth) that rejects the plugin's requests before RomM ever
 * sees them.
 *
 * A stored header arrives with its NAME only — a value is a proxy credential and
 * the backend never sends one back — so a row starts with an empty value field
 * and keeps what is stored until the user types into it. That is the whole
 * meaning of the two value actions on the wire: a stored row still under its own
 * name with an untouched value field saves as `keep`, anything else as `set`.
 * Save is a whole-list replace, so a removed row is gone; the backend refuses the
 * list as a whole and this modal shows the refusal and stays open, which is why
 * the rules are not restated here.
 */

import { FC, useRef, useState, ChangeEvent } from "react";
import { TextField, DialogButton, Focusable } from "@decky/ui";
import { ValidatingModalShell } from "./ValidatingModalShell";
import type { CustomHeaderEntry } from "../../types";

/** The subset of the backend verdict the modal needs to close or stay open. */
export interface SaveHeadersResult {
  success: boolean;
  message?: string;
}

interface CustomHeadersModalProps {
  closeModal?: () => void;
  /** Names of the headers already stored, in the order they were entered. */
  storedNames: string[];
  /** Persists the whole list. Resolves with the backend's verdict. */
  onSave: (headers: CustomHeaderEntry[]) => Promise<SaveHeadersResult>;
}

interface HeaderRow {
  /** Stable across adds and removals, so a row keeps its identity as the list changes. */
  id: number;
  name: string;
  value: string;
  /** The name the backend holds this row's value under, or null for a new row. */
  storedName: string | null;
}

const helperTextStyle = { fontSize: "12px", marginBottom: "12px", color: "rgba(255,255,255,0.6)" } as const;
const rowStyle = { marginBottom: "12px" } as const;
const removeButtonStyle = { marginTop: "6px", minWidth: "auto", width: "auto" } as const;
const emptyStyle = { fontSize: "12px", marginBottom: "8px", color: "rgba(255,255,255,0.6)" } as const;

const STORED_VALUE_HINT = "stored — leave blank to keep it";
const GENERIC_SAVE_ERROR = "Could not save the headers. Check your connection and try again.";

// The dots belong inside the empty field, where they read as "something is in
// here you cannot see" rather than as part of the sentence below it.
//
// `placeholder` lives on React's InputHTMLAttributes while @decky/ui types
// TextField's props as the wider HTMLAttributes, so the prop has to be handed
// over past the type. That says nothing about whether Steam's own component
// forwards it to the <input> it renders — the component is fished out of a
// webpack module and its source is not readable here, so the cast could render
// nothing at all. It is kept only because it was seen working on the device;
// `inlineControls` is the declared prop to fall back to if it ever stops.
const storedValuePlaceholder = { placeholder: "••••" } as Record<string, string>;

/**
 * Whether this row's stored value still applies: it must have one, still be under
 * the name it was stored as, and have an untouched value field. A renamed row
 * saves as `set` with whatever the value field holds, because the backend keys a
 * kept value by name and has nothing to hand a new name.
 */
const keepsStoredValue = (row: HeaderRow): boolean =>
  row.storedName !== null && row.value === "" && row.name === row.storedName;

export const CustomHeadersModal: FC<CustomHeadersModalProps> = ({ closeModal, storedNames, onSave }) => {
  const nextId = useRef(storedNames.length);
  const [rows, setRows] = useState<HeaderRow[]>(() =>
    storedNames.map((name, index) => ({ id: index, name, value: "", storedName: name })),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateRow = (id: number, patch: Partial<HeaderRow>) => {
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, ...patch } : row)));
    // Any edit clears a stale error so it doesn't linger past a correction.
    setError(null);
  };

  const addRow = () => {
    const id = nextId.current;
    nextId.current += 1;
    setRows((prev) => [...prev, { id, name: "", value: "", storedName: null }]);
    setError(null);
  };

  const removeRow = (id: number) => {
    setRows((prev) => prev.filter((row) => row.id !== id));
    setError(null);
  };

  const toEntries = (): CustomHeaderEntry[] =>
    rows.map((row) =>
      keepsStoredValue(row)
        ? { name: row.name, value_action: "keep" }
        : { name: row.name, value_action: "set", value: row.value },
    );

  // Save the whole list. Success closes; a refusal — the backend's own verdict or
  // a rejection — keeps the modal open with a message so the user can correct it.
  const submit = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await onSave(toEntries());
      if (result.success) {
        closeModal?.();
      } else {
        setError(result.message ?? GENERIC_SAVE_ERROR);
      }
    } catch {
      setError(GENERIC_SAVE_ERROR);
    } finally {
      setSubmitting(false);
    }
  };

  const handleNameChange = (id: number) => (e: ChangeEvent<HTMLInputElement>) =>
    updateRow(id, { name: e.target.value });

  const handleValueChange = (id: number) => (e: ChangeEvent<HTMLInputElement>) =>
    updateRow(id, { value: e.target.value });

  return (
    <ValidatingModalShell
      {...(closeModal === undefined ? {} : { closeModal })}
      title="Custom headers"
      error={error}
      errorTestId="custom-headers-error"
      submitLabel={submitting ? "Saving…" : "Save"}
      submitDisabled={submitting}
      onSubmit={() => {
        void submit();
      }}
    >
      <div style={helperTextStyle}>
        Extra headers sent with every request to your RomM server — for a server behind a proxy that authenticates
        requests itself. They never go anywhere else. Authorization is not available: it already carries your RomM API
        token.
      </div>
      {rows.length === 0 && <div style={emptyStyle}>No custom headers.</div>}
      {rows.map((row) => (
        // Focusable per row so the gamepad steps name → value → Remove within one
        // header before moving on to the next.
        <Focusable key={row.id} style={rowStyle} data-testid={`header-row-${row.id}`}>
          <TextField label="Header name" value={row.name} onChange={handleNameChange(row.id)} />
          <TextField
            label="Value"
            {...(keepsStoredValue(row) ? { description: STORED_VALUE_HINT, ...storedValuePlaceholder } : {})}
            value={row.value}
            bIsPassword
            onChange={handleValueChange(row.id)}
          />
          <DialogButton style={removeButtonStyle} onClick={() => removeRow(row.id)}>
            Remove
          </DialogButton>
        </Focusable>
      ))}
      <DialogButton onClick={addRow}>Add header</DialogButton>
    </ValidatingModalShell>
  );
};
