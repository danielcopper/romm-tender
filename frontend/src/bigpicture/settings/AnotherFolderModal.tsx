/**
 * "Another folder…": one window that stays open until a typed place is
 * accepted. OK checks the place; a refusal is shown under the field with the
 * text kept for correcting, and the two questions — a place whose last folder
 * is named Tender, and folders that do not exist yet — are asked in the same
 * window under the field, each with its own buttons. Editing the text drops a
 * shown refusal or question, and an answer that arrives after an edit is
 * dropped too, so a question's buttons act on the text that was checked. An
 * accepted place closes the window and is handed to `onAccepted`; Cancel closes
 * it with nothing chosen. The backend calls are the caller's, passed in.
 */

import { ChangeEvent, FC, KeyboardEvent, useRef, useState } from "react";
import { DialogButton, Focusable, TextField } from "@decky/ui";
import { isEndpointFailure, logError } from "../../api/backend";
import type { TenderFolderState, TenderPlaceAsks, TenderPlaceRefusal } from "../../types";
import {
  ANOTHER_FOLDER,
  ANOTHER_FOLDER_FIELD,
  CREATE_BUTTON,
  KEEP_AS_TYPED,
  anotherFolderExample,
  createMissingQuestion,
  placeRefusalSentence,
  tenderNamedQuestion,
  parentButton,
} from "../../utils/tenderFolderWording";
import { AMBER } from "../layout/pane";
import { ValidatingModalShell } from "./ValidatingModalShell";

type Shown =
  | { kind: "refusal"; sentence: string }
  | { kind: "named"; typed: string; asks: TenderPlaceAsks }
  | { kind: "missing"; typed: string; asks: TenderPlaceAsks };

interface AnotherFolderModalProps {
  closeModal?: () => void;
  drivesRoot: string;
  check: (text: string) => Promise<TenderPlaceAsks | TenderPlaceRefusal>;
  choose: (text: string) => Promise<TenderFolderState | TenderPlaceRefusal>;
  onAccepted: (state: TenderFolderState) => void;
}

const questionStyle = { marginTop: "12px", fontSize: "13px", color: "#dfe3e8" } as const;
const buttonRowStyle = { display: "flex", gap: "8px", marginTop: "10px" } as const;
const buttonStyle = { flex: "1 1 0px", minWidth: 0 } as const;

export const AnotherFolderModal: FC<AnotherFolderModalProps> = ({
  closeModal,
  drivesRoot,
  check,
  choose,
  onAccepted,
}) => {
  const [text, setText] = useState("");
  const [shown, setShown] = useState<Shown | null>(null);
  const [busy, setBusy] = useState(false);
  // The field's text as it stands now, read when an answer arrives.
  const current = useRef("");

  /**
   * Run *work* with the window busy; a failed call is logged and leaves the
   * window as it was. *work* is handed `edited`, which tells whether the text
   * was edited since it started: an answer that arrives after an edit is
   * dropped rather than shown or acted on.
   */
  const step = async (work: (edited: () => boolean) => Promise<void>) => {
    if (busy) return;
    const asked = current.current;
    setBusy(true);
    try {
      await work(() => current.current !== asked);
    } catch (e) {
      logError(`Failed to choose another folder for Tender: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  // A place chosen has switched Tender's folder, so it is handed on and the
  // window closes even where the text was edited meanwhile.
  const accept = async (typed: string, edited: () => boolean) => {
    const answer = await choose(typed);
    if (isEndpointFailure(answer)) {
      if (!edited()) setShown({ kind: "refusal", sentence: placeRefusalSentence(answer) });
      return;
    }
    onAccepted(answer);
    closeModal?.();
  };

  /** Past the Tender-named question: ask about missing folders, or choose. */
  const proceed = async (typed: string, asks: TenderPlaceAsks, edited: () => boolean) => {
    if (asks.missing !== null) {
      setShown({ kind: "missing", typed, asks });
      return;
    }
    await accept(typed, edited);
  };

  /** Check *typed* and act on the answer, unless the text was edited while it was checked. */
  const checkThen = async (typed: string, edited: () => boolean, named: (asks: TenderPlaceAsks) => boolean) => {
    const asks = await check(typed);
    if (edited()) return;
    if (isEndpointFailure(asks)) {
      setShown({ kind: "refusal", sentence: placeRefusalSentence(asks) });
      return;
    }
    if (named(asks)) {
      setShown({ kind: "named", typed, asks });
      return;
    }
    await proceed(typed, asks, edited);
  };

  const submit = () => step((edited) => checkThen(text, edited, (asks) => asks.tender_parent !== null));

  // The parent is not asked about its own name again.
  const chooseParent = (parent: string) => step((edited) => checkThen(parent, edited, () => false));

  const canSubmit = text.trim() !== "" && !busy && (shown === null || shown.kind === "refusal");

  return (
    <ValidatingModalShell
      {...(closeModal === undefined ? {} : { closeModal })}
      title={ANOTHER_FOLDER}
      error={null}
      errorTestId="another-folder-error"
      submitLabel="OK"
      submitDisabled={!canSubmit}
      onSubmit={() => {
        if (canSubmit) void submit();
      }}
    >
      <TextField
        focusOnMount={true}
        label={ANOTHER_FOLDER_FIELD}
        description={anotherFolderExample(drivesRoot)}
        value={text}
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          current.current = e.target.value;
          setText(e.target.value);
          setShown(null);
        }}
        // Enter (the on-screen keyboard's confirm key) does what OK does.
        onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
          if (e.key === "Enter" && canSubmit) void submit();
        }}
      />
      {shown?.kind === "refusal" && (
        <div data-testid="another-folder-refusal" style={{ ...questionStyle, color: AMBER }}>
          {shown.sentence}
        </div>
      )}
      {shown?.kind === "named" && shown.asks.tender_parent !== null && (
        <div data-testid="another-folder-question">
          <div style={questionStyle}>{tenderNamedQuestion(shown.asks.place, shown.asks.tender_parent)}</div>
          <Focusable flow-children="horizontal" style={buttonRowStyle}>
            <DialogButton
              style={buttonStyle}
              disabled={busy}
              onClick={() => {
                if (shown.asks.tender_parent !== null) void chooseParent(shown.asks.tender_parent);
              }}
            >
              {parentButton(shown.asks.tender_parent)}
            </DialogButton>
            <DialogButton
              style={buttonStyle}
              disabled={busy}
              onClick={() => void step((edited) => proceed(shown.typed, shown.asks, edited))}
            >
              {KEEP_AS_TYPED}
            </DialogButton>
            <DialogButton style={buttonStyle} disabled={busy} onClick={() => setShown(null)}>
              Cancel
            </DialogButton>
          </Focusable>
        </div>
      )}
      {shown?.kind === "missing" && shown.asks.missing !== null && (
        <div data-testid="another-folder-question">
          <div style={questionStyle}>
            {createMissingQuestion(shown.asks.place, shown.asks.existing, shown.asks.missing)}
          </div>
          <Focusable flow-children="horizontal" style={buttonRowStyle}>
            <DialogButton
              style={buttonStyle}
              disabled={busy}
              onClick={() => void step((edited) => accept(shown.typed, edited))}
            >
              {CREATE_BUTTON}
            </DialogButton>
            <DialogButton style={buttonStyle} disabled={busy} onClick={() => setShown(null)}>
              Cancel
            </DialogButton>
          </Focusable>
        </div>
      )}
    </ValidatingModalShell>
  );
};
