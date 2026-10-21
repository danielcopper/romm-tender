/**
 * The RomM URL's own window: it stays open until the backend has stored the
 * typed URL. OK, Enter in the field and R2 save it; a refusal — or a save that
 * could not reach the backend — is shown under the field in amber with the
 * text kept for correcting. Editing the text drops a shown refusal, and a
 * refusal that arrives after an edit is dropped too. A save that went through
 * closes the window even then, because the URL is stored. Cancel closes it with
 * nothing saved. The save is the caller's, passed in.
 */

import { ChangeEvent, FC, useRef, useState } from "react";
import { TextField } from "@decky/ui";
import { logError } from "../../api/backend";
import { AMBER } from "../layout/pane";
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

  const canSubmit = text.trim() !== "" && !busy;

  const submit = async () => {
    const asked = text;
    setBusy(true);
    try {
      const answer = await save(asked);
      if (answer.success) {
        closeModal?.();
        return;
      }
      if (current.current === asked) setRefusal(answer.message);
    } catch (e) {
      logError(`Failed to save the RomM URL: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ValidatingModalShell
      {...(closeModal === undefined ? {} : { closeModal })}
      title={ROMM_URL}
      error={null}
      errorTestId="romm-url-error"
      submitLabel="OK"
      submitDisabled={!canSubmit}
      onSubmit={() => {
        if (canSubmit) void submit();
      }}
      // Enter in the field and R2 do what OK does: only a stored URL closes the window.
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
