/**
 * Updates — the home of the two update notices on Main: the installed and the
 * available version, an update the installer rolled back, the install, the
 * daily-check switch and Check now. The page owns the notice and outcome reads,
 * Check now's press and its result line; the install's state is read here,
 * because it is polled only while this section is on screen.
 */

import { FC } from "react";
import { PanelSection, PanelSectionRow, ButtonItem, Field, ToggleField } from "@decky/ui";
import { AMBER } from "../layout/pane";
import { UpdateInstallRows, installButtonShown, installStateUnread } from "./UpdateInstallRows";
import { useUpdateInstall } from "./useUpdateInstall";
import { INSTALL_STATE_UNREAD } from "../../utils/updateInstallView";
import type { UpdateNoticeState } from "../../utils/updateNoticeStore";
import { updateFailureReason, updateFailureSentence, type UpdateOutcomeState } from "../../utils/updateOutcomeStore";

/** Shown only to a run from a checkout, which is never offered an install. */
export const NOT_INSTALLED_PROGRAM = "Development build — install updates with the installer.";

interface UpdatesSectionProps {
  update: UpdateNoticeState;
  /** A rolled-back update is stated here whether or not its notice on Main was dismissed. */
  outcome: UpdateOutcomeState;
  /** A Check now is in flight; the button is dead and says so while it is. */
  checking: boolean;
  /** What the last Check now found, or `""`. */
  result: string;
  onEnabledChange: (enabled: boolean) => void;
  onCheckNow: () => void;
}

/** The Available row's value: a version only where one is newer, and otherwise what is known. */
function availableValue(update: UpdateNoticeState): string {
  if (!update.enabled) return "Not checked — the daily check is off";
  if (update.latestVersion === null) return "Not known yet";
  return update.newer ? update.latestVersion : "None newer";
}

export const UpdatesSection: FC<UpdatesSectionProps> = ({
  update,
  outcome,
  checking,
  result,
  onEnabledChange,
  onCheckNow,
}) => {
  const install = useUpdateInstall();
  return (
    <PanelSection title="Updates">
      <PanelSectionRow>
        {/* Read-only rows are focusable for the reason every one on a wide pane
            is: the region scrolls by moving focus. */}
        <Field label="Installed" focusable={true}>
          <span data-testid="updates-installed">{update.currentVersion || "—"}</span>
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        {/* Where the install has no button to carry it, a read that did not
            answer is said here: a row of its own would come and go with the
            reads under a reader's focus. */}
        <Field
          label="Available"
          focusable={true}
          {...(!installButtonShown(install) && installStateUnread(install)
            ? { description: <span data-testid="updates-install-unread">{INSTALL_STATE_UNREAD}</span> }
            : {})}
        >
          <span data-testid="updates-available">{availableValue(update)}</span>
        </Field>
      </PanelSectionRow>
      {outcome.failure !== null && (
        <PanelSectionRow>
          <Field
            label={
              <span data-testid="updates-last-update" style={{ color: AMBER }}>
                {updateFailureSentence(outcome.failure)}
              </span>
            }
            description={updateFailureReason(outcome.failure)}
            focusable={true}
          />
        </PanelSectionRow>
      )}
      {!update.installedProgram && (
        <PanelSectionRow>
          <Field label={<span data-testid="updates-not-installed">{NOT_INSTALLED_PROGRAM}</span>} focusable={true} />
        </PanelSectionRow>
      )}
      <UpdateInstallRows install={install} />
      <PanelSectionRow>
        <ToggleField
          label="Check for updates daily"
          description="Asks GitHub at most once a day whether a newer release is out."
          checked={update.enabled}
          onChange={onEnabledChange}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        {/* Dead while an attempt is under way, in the handler too since a
            disabled control still reports a press on the device: why is
            docs/architecture/qam-panel.md, Settings. */}
        <ButtonItem
          layout="below"
          onClick={() => {
            if (!install.underWay) onCheckNow();
          }}
          disabled={checking || install.underWay}
        >
          {checking ? "Checking…" : "Check now"}
        </ButtonItem>
      </PanelSectionRow>
      {result && (
        <PanelSectionRow>
          <Field label={<span data-testid="updates-result">{result}</span>} focusable={true} />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
