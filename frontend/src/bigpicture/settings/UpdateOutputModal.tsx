/**
 * The window "Show what the installer said" opens: the installer's run for one
 * failed update and, after a rollback, what the version it tried printed while
 * it tried to start, both read back from the journal by the backend.
 *
 * Which run is shown, why a rollback has two parts and the lines a part is cut
 * to are `docs/architecture/backend-architecture.md`, "UpdateOutputService
 * notes"; the backend picks the run and hides the admission token before any
 * line reaches the panel.
 */

import { FC, ReactNode, useEffect, useState } from "react";
import { DialogButton, Focusable, ModalRoot, showModal } from "@decky/ui";
import { getUpdateOutput, logError, type UpdateOutput, type UpdateOutputSection } from "../../api/backend";
import { detach } from "../../utils/detach";
import { clockTime } from "../../utils/updateInstallView";
import { MUTED } from "../layout/pane";

const TITLE = "What the installer said";

/** What the window says where the journal holds no run of the installer's for this failure. */
export const OUTPUT_MISSING: Partial<Record<string, string>> = {
  rotated: "This output is no longer in the system journal — it keeps only the last hours of logs.",
  terminal: "This update was run in a terminal, so its output is there, not in the journal.",
  empty: "The installer left nothing in the journal for this update.",
};

/** What the window says while the answer is on its way. */
export const OUTPUT_READING = "Reading what the installer said…";

/** What the window says where the failure it was opened for no longer stands. */
export const OUTPUT_GONE = "This failed update is no longer on record.";

/** What the window says where the journal could not be read, or the answer did not arrive. */
export const OUTPUT_UNREAD = "Tender could not read what the installer said.";

/**
 * How much one focus stop holds: this many lines, and no more characters than
 * this, so that no stop is taller than the dialog — a line of 500 characters
 * wraps to several rows. The window scrolls only by moving focus
 * (`docs/architecture/qam-panel.md`, "Two widths"), so a part of several
 * hundred lines is walked one stop at a time.
 */
const LINES_PER_STOP = 12;
const CHARS_PER_STOP = 1200;

/**
 * What makes text a focus stop: `focusableIfEmpty`, which `FocusableProps`
 * does not declare, as the cleanup's details region does
 * (`RemovedGamesCleanup.tsx`, `DETAILS_REGION_STOP`).
 */
const TEXT_STOP: { focusableIfEmpty: boolean } = { focusableIfEmpty: true };

const MONO = { fontFamily: "monospace", fontSize: "12px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" } as const;

/** *lines* cut into the text of one focus stop each, within both of the caps above. */
export function outputStops(lines: string[]): string[] {
  const stops: string[] = [];
  let stop = "";
  let held = 0;
  for (const line of lines) {
    if (held === LINES_PER_STOP || (held > 0 && stop.length + line.length >= CHARS_PER_STOP)) {
      stops.push(stop);
      held = 0;
    }
    stop = held++ > 0 ? `${stop}\n${line}` : line;
  }
  if (held > 0) stops.push(stop);
  return stops;
}

const Part: FC<{ heading: string; section: UpdateOutputSection }> = ({ heading, section: { lines, earlier } }) => (
  <div style={{ marginTop: "12px" }}>
    <b>{heading}</b>
    {earlier > 0 && (
      <div style={{ color: MUTED, fontSize: "12px" }}>
        {earlier === 1 ? "1 earlier line is" : `${earlier} earlier lines are`} not shown.
      </div>
    )}
    {outputStops(lines).map((text, i) => (
      <Focusable key={i} {...TEXT_STOP} style={MONO}>
        {text}
      </Focusable>
    ))}
  </div>
);

interface UpdateOutputModalProps {
  /** The backend's answer on its way; `null` where the call did not arrive. Never rejects. */
  read: Promise<UpdateOutput | null>;
  /** The version the update tried to install, which names the rollback's second part. */
  attemptedVersion: string;
  closeModal?: () => void;
}

export const UpdateOutputModal: FC<UpdateOutputModalProps> = ({ read, attemptedVersion, closeModal }) => {
  const [output, setOutput] = useState<UpdateOutput | null>();
  useEffect(() => detach(read.then(setOutput)), [read]);
  let title = TITLE;
  let body: ReactNode = output === undefined ? OUTPUT_READING : OUTPUT_UNREAD;
  if (output?.success && output.missing === null) {
    title += ` — ${clockTime(output.ran_at)}`;
    body = (
      <>
        <Part heading="The installer" section={output.installer} />
        {output.new_version && (
          <Part heading={`${attemptedVersion}, when it tried to start`} section={output.new_version} />
        )}
      </>
    );
  } else if (output?.success) {
    body = OUTPUT_MISSING[output.missing] ?? OUTPUT_UNREAD;
  } else if (output?.reason === "not_found") {
    body = OUTPUT_GONE;
  }
  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ maxWidth: "720px", maxHeight: "76vh", overflowY: "auto", padding: "16px" }}>
        <div style={{ fontSize: "16px", fontWeight: "bold" }}>{title}</div>
        {typeof body === "string" ? <Focusable {...TEXT_STOP}>{body}</Focusable> : body}
        <DialogButton style={{ marginTop: "16px" }} onClick={() => closeModal?.()}>
          Close
        </DialogButton>
      </div>
    </ModalRoot>
  );
};

/** Whether a press's answer is still on its way: a press meanwhile opens no second window. */
let reading = false;

/**
 * Open the window at once, saying it is reading, and fill it with the
 * backend's answer for one failure — the record stamped *rolledBackAt*, or
 * this backend's latest attempt with `null`. A call that fails is logged, and
 * the window says so.
 */
export function showUpdateOutput(rolledBackAt: string | null, attemptedVersion: string): void {
  if (reading) return;
  reading = true;
  const read = getUpdateOutput(rolledBackAt)
    .catch((e: unknown) => {
      logError(`Failed to read the installer's output: ${e}`);
      return null;
    })
    .finally(() => {
      reading = false;
    });
  showModal(<UpdateOutputModal read={read} attemptedVersion={attemptedVersion} />);
}
