/**
 * The window "Show what the installer said" opens: the installer's run for one
 * failed update and, after a rollback, what the version it tried printed while
 * it tried to start, both read back from the journal by the backend.
 *
 * Which run is shown, why a rollback has two parts and the lines a part is cut
 * to are `docs/architecture/qam-panel.md`, Settings; the backend picks the run
 * and hides the admission token before any line reaches the panel.
 */

import { FC, ReactNode } from "react";
import { DialogButton, Focusable, ModalRoot, showModal } from "@decky/ui";
import { getUpdateOutput, logError, type UpdateOutput, type UpdateOutputSection } from "../../api/backend";
import { clockTime } from "../../utils/updateInstallView";
import { MUTED } from "../layout/pane";

const TITLE = "What the installer said";

/** What the window says where the journal holds no run of the installer's for this failure. */
export const OUTPUT_MISSING = {
  rotated: "This output is no longer in the system journal — it keeps only the last hours of logs.",
  terminal: "This update was run in a terminal, so its output is there, not in the journal.",
} as const;

/** What the window says where the journal could not be read, or the answer did not arrive. */
export const OUTPUT_UNREAD = "The installer's output could not be read.";

/**
 * How many lines one focus stop holds. The window scrolls only by moving focus
 * (`docs/architecture/qam-panel.md`, "Two widths"), so a part of several
 * hundred lines is walked a screenful at a time.
 */
const LINES_PER_STOP = 12;

/**
 * What makes text a focus stop: `focusableIfEmpty`, which `FocusableProps`
 * does not declare, as the cleanup's details region does
 * (`RemovedGamesCleanup.tsx`, `DETAILS_REGION_STOP`).
 */
const TEXT_STOP: { focusableIfEmpty: boolean } = { focusableIfEmpty: true };

const MONO = { fontFamily: "monospace", fontSize: "12px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" } as const;

const Part: FC<{ heading: string; section: UpdateOutputSection }> = ({ heading, section: { lines, earlier } }) => {
  const stops: string[] = [];
  for (let i = 0; i < lines.length; i += LINES_PER_STOP) stops.push(lines.slice(i, i + LINES_PER_STOP).join("\n"));
  return (
    <div style={{ marginTop: "12px" }}>
      <b>{heading}</b>
      {earlier > 0 && (
        <div style={{ color: MUTED, fontSize: "12px" }}>
          {earlier === 1 ? "1 earlier line is" : `${earlier} earlier lines are`} not shown.
        </div>
      )}
      {stops.map((text, i) => (
        <Focusable key={i} {...TEXT_STOP} style={MONO}>
          {text}
        </Focusable>
      ))}
    </div>
  );
};

interface UpdateOutputModalProps {
  /** The backend's answer, or `null` where the call did not arrive. */
  output: UpdateOutput | null;
  /** The version the update tried to install, which names the rollback's second part. */
  attemptedVersion: string;
  closeModal?: () => void;
}

export const UpdateOutputModal: FC<UpdateOutputModalProps> = ({ output, attemptedVersion, closeModal }) => {
  let title = TITLE;
  let body: ReactNode = OUTPUT_UNREAD;
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
    body = OUTPUT_MISSING[output.missing];
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

/**
 * Ask the backend for the installer's output of one failure — the record
 * stamped *rolledBackAt*, or this backend's latest attempt with `null` — and
 * open the window over it. A call that fails opens it too, saying so.
 */
export async function showUpdateOutput(rolledBackAt: string | null, attemptedVersion: string): Promise<void> {
  let output: UpdateOutput | null = null;
  try {
    output = await getUpdateOutput(rolledBackAt);
  } catch (e) {
    logError(`Failed to read the installer's output: ${e}`);
  }
  showModal(<UpdateOutputModal output={output} attemptedVersion={attemptedVersion} />);
}
