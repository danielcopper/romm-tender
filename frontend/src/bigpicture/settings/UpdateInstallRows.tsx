import { FC } from "react";
import { PanelSectionRow, ButtonItem, Field, ProgressBar } from "@decky/ui";
import { AMBER } from "../layout/pane";
import type { UpdateInstallAttempt } from "../../api/backend";
import { formatBytes } from "../../utils/formatters";
import {
  INSTALL_FAILURE_SENTENCES,
  RESTARTING_LINE,
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

/**
 * The install under Settings › Updates: the button, what it waits for, and the
 * steps of an attempt. Every row is a focus stop, because the pane scrolls only
 * by moving focus. A step's bar rides its field's description so the step and
 * its bar are one stop.
 */
export const UpdateInstallRows: FC<{ install: UpdateInstall }> = ({ install }) => {
  const { attempt } = install;
  const underWay = attempt !== null && attempt.step !== "failed";
  const showButton = install.offered && install.version !== null && !underWay;
  const pausedHint = pausedDownloadsHint(install.pausedDownloads);

  return (
    <>
      {showButton && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={install.install}
            disabled={install.pressing || install.waitReasons.length > 0}
          >
            {install.tryAgain ? "Try again" : `Install update ${install.version}`}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {showButton && pausedHint && (
        <PanelSectionRow>
          <Field label={<span data-testid="updates-paused-hint">{pausedHint}</span>} focusable={true} />
        </PanelSectionRow>
      )}
      {showButton && install.waitReasons.length > 0 && (
        <PanelSectionRow>
          <Field
            label={WAITING_FOR}
            description={
              <div data-testid="updates-waiting">
                {install.waitReasons.map((wait) => (
                  <div key={wait.reason} data-testid="updates-wait-reason">
                    {waitReasonLine(wait)}
                  </div>
                ))}
              </div>
            }
            focusable={true}
          />
        </PanelSectionRow>
      )}
      {install.refusal && (
        <PanelSectionRow>
          <Field label={<span data-testid="updates-install-refusal">{install.refusal}</span>} focusable={true} />
        </PanelSectionRow>
      )}
      {attempt !== null &&
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
      {attempt?.step === "installer_started" && (
        <PanelSectionRow>
          <Field label={<span data-testid="updates-restarting">{RESTARTING_LINE}</span>} focusable={true} />
        </PanelSectionRow>
      )}
      {attempt?.step === "failed" && attempt.failure !== null && (
        <PanelSectionRow>
          <Field
            label={
              <span data-testid="updates-install-failure" style={{ color: AMBER }}>
                {INSTALL_FAILURE_SENTENCES[attempt.failure]}
              </span>
            }
            focusable={true}
          />
        </PanelSectionRow>
      )}
    </>
  );
};
