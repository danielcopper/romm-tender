import { FC } from "react";
import { PanelSectionRow, ButtonItem, Field, ProgressBar } from "@decky/ui";
import { AMBER } from "../layout/pane";
import type { UpdateInstallAttempt } from "../../api/backend";
import { formatBytes } from "../../utils/formatters";
import {
  GAME_STARTS_CANCEL,
  INSTALL_FAILURE_SENTENCES,
  INSTALL_STATE_UNREAD,
  NOT_BACK_LINE,
  RESTARTING_LINE,
  TAKING_LONG_LINE,
  WAITING_FOR,
  downloadPercent,
  installStepRows,
  pausedDownloadsHint,
  waitReasonLine,
  type InstallStepRow,
} from "../../utils/updateInstallView";
import type { UpdateInstall } from "./useUpdateInstall";

function stepValue(step: InstallStepRow, attempt: UpdateInstallAttempt): string {
  switch (step.status) {
    case "pending":
      return "";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    case "current": {
      if (step.id !== "download") return "…";
      const percent = downloadPercent(attempt);
      return percent === null ? formatBytes(attempt.bytes_done) : `${percent}%`;
    }
  }
}

/** The download's bar, while it is the step under way; indeterminate where no size was announced. */
function downloadBar(step: InstallStepRow, attempt: UpdateInstallAttempt) {
  if (step.id !== "download" || step.status !== "current") return {};
  const percent = downloadPercent(attempt);
  return {
    description: <ProgressBar indeterminate={percent === null} {...(percent !== null ? { nProgress: percent } : {})} />,
  };
}

function buttonLabel(install: UpdateInstall): string {
  if (install.underWay) return "Installing…";
  return install.tryAgain ? "Try again" : `Install update ${install.version}`;
}

/** The line under the steps: what the attempt's state means for the reader, one row for all of them. */
function statusLine(install: UpdateInstall, attempt: UpdateInstallAttempt) {
  switch (attempt.step) {
    case "downloading":
    case "verifying":
      return <span data-testid="updates-game-hint">{GAME_STARTS_CANCEL}</span>;
    case "installer_started": {
      if (!install.overdue) return <span data-testid="updates-restarting">{RESTARTING_LINE}</span>;
      return (
        <span data-testid="updates-restarting" style={{ color: AMBER }}>
          {install.readFailed ? NOT_BACK_LINE : TAKING_LONG_LINE}
        </span>
      );
    }
    case "failed":
      return (
        <span data-testid="updates-install-failure" style={{ color: AMBER }}>
          {attempt.failure === null ? "" : INSTALL_FAILURE_SENTENCES[attempt.failure]}
        </span>
      );
  }
}

/** Whether the install's button is there: while the backend offers a release, and through an attempt. */
export function installButtonShown(install: UpdateInstall): boolean {
  return (install.offered && install.version !== null) || install.underWay;
}

/** Whether to say the state could not be read: a read failed, and no installer's restart explains it. */
export function installStateUnread(install: UpdateInstall): boolean {
  return install.readFailed && !install.restarting;
}

/**
 * The install under Settings › Updates: the button, what it waits for, and the
 * steps of an attempt. Which row carries what, and which rows can leave while
 * they hold focus, is `docs/architecture/qam-panel.md`, Settings. Where there
 * is no button, the section says the state could not be read on its Available
 * row instead.
 */
export const UpdateInstallRows: FC<{ install: UpdateInstall }> = ({ install }) => {
  const { attempt } = install;
  const showButton = installButtonShown(install);
  // A failed attempt for a version no longer offered says nothing about the one that is.
  const showAttempt = attempt !== null && (install.underWay || attempt.version === install.version);
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
  const hasDescription = waiting || pausedHint !== "" || install.refusal !== "" || unread;

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
      {attempt !== null &&
        showAttempt &&
        installStepRows(attempt).map((step) => (
          <PanelSectionRow key={step.id}>
            <Field label={step.label} focusable={true} bottomSeparator="none" {...downloadBar(step, attempt)}>
              <span
                data-testid={`updates-step-${step.id}`}
                data-status={step.status}
                style={step.status === "failed" ? { color: AMBER } : undefined}
              >
                {stepValue(step, attempt)}
              </span>
            </Field>
          </PanelSectionRow>
        ))}
      {attempt !== null && showAttempt && (
        <PanelSectionRow key="status">
          <Field label={statusLine(install, attempt)} focusable={true} />
        </PanelSectionRow>
      )}
    </>
  );
};
