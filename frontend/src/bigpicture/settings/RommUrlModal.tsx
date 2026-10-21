/**
 * The RomM URL's own window: it stays open until the backend has stored the
 * typed URL. OK, Enter in the field and R2 save it; a refusal — or a save that
 * could not reach the backend — is shown under the field in amber with the
 * text kept for correcting. Editing the text drops a shown refusal, and a
 * refusal that arrives after an edit is dropped too. A save that went through
 * closes the window even then, because the URL is stored. Cancel and B close it
 * without waiting for a save in flight, whose answer then reaches nothing in
 * the window. The save is the caller's, passed in.
 */

import { ChangeEvent, FC, useRef, useState } from "react";
import { TextField } from "@decky/ui";
import { logError } from "../../api/backend";
import { AMBER } from "../layout/pane";
import { SAVE_FAILED } from "./helpers";
import { ValidatingModalShell } from "./ValidatingModalShell";

const ROMM_URL = "RomM URL";

interface RommUrlModalProps {
  closeModal?: () => void;
  /** The URL the backend holds, which the field starts with. */
  value: string;
  save: (text: string) => Promise<{ success: boolean; message: string }>;
}

const refusalStyle = { marginTop: "12px", fontSize: "13px", color: AMBER } as const;

export const RommUrlModal: FC<RommUrlModalProps> = ({ closeModal, value, save }) => {
  const [text, setText] = useState(value);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The field's text as it stands now, read when an answer arrives.
  const current = useRef(value);
  // Set once the window is closed, so an answer that arrives later does nothing.
  const closed = useRef(false);
  const close = () => {
    closed.current = true;
    closeModal?.();
  };

  const canSubmit = text.trim() !== "" && !busy;

  const submit = async () => {
    const asked = text;
    setBusy(true);
    try {
      const answer = await save(asked);
      if (closed.current) return;
      if (answer.success) {
        close();
        return;
      }
      if (current.current === asked) setRefusal(answer.message);
    } catch (e) {
      // The type alone: the text of a failed request can carry what it was sent with.
      logError(`Failed to save the RomM URL: ${e instanceof Error ? e.name : typeof e}`);
      if (!closed.current && current.current === asked) setRefusal(SAVE_FAILED);
    } finally {
      if (!closed.current) setBusy(false);
    }
  };

  return (
    <ValidatingModalShell
      closeModal={close}
      title={ROMM_URL}
      error={null}
      errorTestId="romm-url-error"
      submitLabel="OK"
      submitDisabled={!canSubmit}
      onSubmit={() => {
        if (canSubmit) void submit();
      }}
      // Enter in the field and R2 do what OK does: they close the window only on a
      // stored URL. Cancel and B close it at once.
      dialogOKSubmits={true}
    >
      <TextField
        focusOnMount={true}
        label={ROMM_URL}
        value={text}
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          current.current = e.target.value;
          setText(e.target.value);
          setRefusal(null);
        }}
      />
      {refusal !== null && (
        <div data-testid="romm-url-refusal" style={refusalStyle}>
          {refusal}
        </div>
      )}
    </ValidatingModalShell>
  );
};
