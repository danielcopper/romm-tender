import { FC, ReactNode, useEffect, useReducer } from "react";
import { PanelSectionRow, ButtonItem, Field, ProgressBar } from "@decky/ui";
import { AMBER, AMBER_WASH, GREEN, MUTED } from "../layout/pane";
import type { UpdateInstallAttempt } from "../../api/backend";
import { attemptSeenAt, installerSeenAt } from "../../utils/updateInstallStore";
import {
  GAME_STARTS_CANCEL,
  INSTALL_FAILURE_SENTENCES,
  INSTALL_STATE_UNREAD,
  NOT_BACK_LINE,
  TAKING_LONG_LINE,
  WAITING_FOR,
  downloadPercent,
  failedStep,
  installSteps,
  pausedDownloadsHint,
  restartWaitLine,
  waitReasonLine,
  type InstallStepId,
  type InstallStepStatus,
} from "../../utils/updateInstallView";
import {
  updateDidNotGoThrough,
  updateFailureReason,
  updateFailureSentence,
  type RolledBackUpdate,
} from "../../utils/updateOutcomeStore";
import { cardFrame } from "../UpdateCard";
import type { UpdateInstall } from "./useUpdateInstall";

function buttonLabel(install: UpdateInstall): string {
  if (install.underWay) return "Installing…";
  return install.tryAgain ? "Try again" : "Install update";
}

/** Whether the install's button is there: while the backend offers a release, and through an attempt. */
export function installButtonShown(install: UpdateInstall): boolean {
  return (install.offered && install.version !== null) || install.underWay;
}

/** Whether to say the state could not be read: a read failed, and no installer's restart explains it. */
export function installStateUnread(install: UpdateInstall): boolean {
  return install.readFailed && !install.restarting;
}

const MARKS: Record<InstallStepStatus, [string, string?]> = {
  done: ["✓", GREEN],
  current: ["●"],
  pending: ["○", MUTED],
  failed: ["✗", AMBER],
};

const SMALL = { fontSize: "12px", color: MUTED, paddingTop: "6px" } as const;

/** `m:ss` of a duration in milliseconds. */
function clock(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** What the block under the button shows: an attempt's progress, or a failure. */
interface Block {
  caption: string;
  /** Beside the caption: the percent and the clock of an attempt under way. */
  aside?: string;
  /** The bar's percent, `null` for an indeterminate one; absent exactly for a failure, which has no bar. */
  percent?: number | null;
  /** The step under way, or the one marked failed; `null` where nothing says which. */
  at: InstallStepId | null;
  note: ReactNode;
}

/**
 * An attempt under way. After `installer_started` the backend reports nothing
 * more, so the phase is the panel's inference: while reads still answer, the
 * installer is running its pre-install check, which it does before it stops
 * this backend; once they fail, Tender is restarting.
 */
function progressBlock(install: UpdateInstall, attempt: UpdateInstallAttempt, earlier: string): Block {
  const percent = attempt.step === "downloading" ? downloadPercent(attempt) : null;
  const elapsed = clock(Date.now() - (attemptSeenAt() ?? Date.now()));
  const gone = install.readFailed;
  const block: Block = {
    caption: gone ? "Tender is restarting" : "Checking the new version",
    aside: percent === null ? elapsed : `${percent} % · ${elapsed}`,
    percent,
    at: gone ? "install" : "check",
    note: install.overdue ? (
      <span style={{ color: AMBER }}>{gone ? NOT_BACK_LINE : TAKING_LONG_LINE}</span>
    ) : (
      gone && restartWaitLine(earlier)
    ),
  };
  if (attempt.step !== "installer_started") {
    block.at = attempt.step === "verifying" ? "verify" : "download";
    block.caption = `${block.at === "verify" ? "Verifying" : "Downloading"} ${attempt.version}`;
    block.note = GAME_STARTS_CANCEL;
  }
  return block;
}

const failedTo = (version: string, outcome = "nothing was changed") => `Update to ${version} failed — ${outcome}.`;

function attemptFailure(attempt: UpdateInstallAttempt, earlier: string): Block {
  const kind = attempt.failure;
  return {
    // An installer that stopped cannot say what it left behind, so its title claims no more than Main's card does.
    caption:
      kind && kind !== "installer_stopped"
        ? failedTo(attempt.version)
        : updateDidNotGoThrough(attempt.version, earlier),
    at: kind && failedStep(kind, installerSeenAt() !== null),
    note: kind && INSTALL_FAILURE_SENTENCES[kind],
  };
}

/** The installer's record, where no attempt of this backend's is shown. */
function recordFailure(record: RolledBackUpdate): Block {
  const { kind, attemptedVersion } = record;
  return {
    caption:
      kind === "rollback"
        ? failedTo(attemptedVersion, `Tender went back to ${record.restoredVersion}`)
        : kind === "check"
          ? failedTo(attemptedVersion)
          : updateFailureSentence(record),
    at: kind === "rollback" ? "install" : kind === "check" ? "check" : null,
    note: updateFailureReason(record),
  };
}

/**
 * The install under Settings › Updates: the button, what it waits for, and one
 * block for what is happening — an attempt's progress, or what failed. Which
 * row carries what, and which rows can leave while they hold focus, is
 * `docs/architecture/qam-panel.md`, Settings. Where there is no button, the
 * section says the state could not be read on its Available row instead.
 */
export const UpdateInstallRows: FC<{ install: UpdateInstall; record: RolledBackUpdate | null; installed: string }> = ({
  install,
  record,
  installed,
}) => {
  const { attempt } = install;
  const showButton = installButtonShown(install);
  const earlier = installed || "the earlier version";
  // A failed attempt for a version no longer offered says nothing about the one that is.
  const block =
    attempt !== null && install.underWay
      ? progressBlock(install, attempt, earlier)
      : attempt?.step === "failed" && attempt.version === install.version
        ? attemptFailure(attempt, earlier)
        : record && recordFailure(record);
  const failed = block?.percent === undefined;
  // The clock beside the caption moves once a second while an attempt is under way.
  const [, tick] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    if (!install.underWay) return;
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [install.underWay]);
  const pausedHint = pausedDownloadsHint(install.pausedDownloads);
  const waiting = !install.underWay && install.waitReasons.length > 0;
  const unread = installStateUnread(install);

  const description = (
    <>
      {waiting && (
        <div data-testid="updates-waiting">
          <div>{WAITING_FOR}</div>
          {install.waitReasons.map((wait) => (
            <div key={wait.reason} data-testid="updates-wait-reason">
              {waitReasonLine(wait)}
            </div>
          ))}
        </div>
      )}
      {pausedHint && <div data-testid="updates-paused-hint">{pausedHint}</div>}
      {install.refusal && <div data-testid="updates-install-refusal">{install.refusal}</div>}
      {unread && <div data-testid="updates-install-unread">{INSTALL_STATE_UNREAD}</div>}
    </>
  );
  const hasDescription = waiting || pausedHint || install.refusal || unread;

  return (
    <>
      {showButton && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={install.install}
            disabled={install.pressing || install.underWay || install.waitReasons.length > 0}
            {...(hasDescription ? { description } : {})}
          >
            {buttonLabel(install)}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {block && (
        <PanelSectionRow>
          {/* One field for every state of the block, so the focus stop stays
              put while its content changes under it. */}
          <Field focusable={true} childrenLayout="below" childrenContainerWidth="max">
            <div style={failed ? cardFrame(AMBER, AMBER_WASH) : undefined}>
              <div style={{ display: "flex", justifyContent: "space-between", paddingBottom: "6px" }}>
                <span data-testid="updates-caption" style={failed ? { fontWeight: "bold", color: AMBER } : undefined}>
                  {block.caption}
                </span>
                <span data-testid="updates-elapsed" style={{ color: MUTED, fontVariantNumeric: "tabular-nums" }}>
                  {block.aside}
                </span>
              </div>
              {!failed && (
                <ProgressBar
                  indeterminate={block.percent === null}
                  {...(block.percent !== null ? { nProgress: block.percent } : {})}
                />
              )}
              {block.at && (
                <div style={{ fontSize: "12px", paddingTop: "6px", display: "flex", gap: "14px" }}>
                  {installSteps(block.at, failed).map(({ id, label, status }) => (
                    <span key={id} data-testid={`updates-step-${id}`} style={{ color: MARKS[status][1] }}>
                      {MARKS[status][0]} {label}
                    </span>
                  ))}
                </div>
              )}
              {block.note && (
                <div data-testid="updates-note" style={SMALL}>
                  {block.note}
                </div>
              )}
            </div>
          </Field>
        </PanelSectionRow>
      )}
    </>
  );
};
