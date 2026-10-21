/**
 * "Another folder…": one window that stays open until a typed place is
 * accepted. OK checks the place; a refusal is shown under the field with the
 * text kept for correcting, and the two questions — a place whose last folder
 * is named Tender, and folders that do not exist yet — are asked in the same
 * window under the field, each with its own buttons. Editing the text drops a
 * shown refusal or question. An accepted place closes the window and is handed
 * to `onAccepted`; Cancel closes it with nothing chosen. The backend calls are
 * the caller's, passed in.
 */

import { ChangeEvent, FC, KeyboardEvent, useState } from "react";
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
  | { kind: "named"; asks: TenderPlaceAsks }
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

  /** Run *step* with the window busy; a failed call is logged and leaves the window as it was. */
  const step = async (work: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try {
      await work();
    } catch (e) {
      logError(`Failed to choose another folder for Tender: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  const accept = async (typed: string) => {
    const answer = await choose(typed);
    if (isEndpointFailure(answer)) {
      setShown({ kind: "refusal", sentence: placeRefusalSentence(answer) });
      return;
    }
    onAccepted(answer);
    closeModal?.();
  };

  /** Past the Tender-named question: ask about missing folders, or choose. */
  const proceed = async (typed: string, asks: TenderPlaceAsks) => {
    if (asks.missing !== null) {
      setShown({ kind: "missing", typed, asks });
      return;
    }
    await accept(typed);
  };

  const submit = () =>
    step(async () => {
      const asks = await check(text);
      if (isEndpointFailure(asks)) {
        setShown({ kind: "refusal", sentence: placeRefusalSentence(asks) });
        return;
      }
      if (asks.tender_parent !== null) {
        setShown({ kind: "named", asks });
        return;
      }
      await proceed(text, asks);
    });

  const chooseParent = (parent: string) =>
    step(async () => {
      const asks = await check(parent);
      if (isEndpointFailure(asks)) {
        setShown({ kind: "refusal", sentence: placeRefusalSentence(asks) });
        return;
      }
      await proceed(parent, asks);
    });

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
              onClick={() => void step(() => proceed(text, shown.asks))}
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
            <DialogButton style={buttonStyle} disabled={busy} onClick={() => void step(() => accept(shown.typed))}>
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
