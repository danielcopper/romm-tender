/**
 * Updates — the home of the update notice on Main: the installed and the
 * available version, the daily-check switch and Check now. Pure renderer: the
 * page owns the store read, the press in flight and its result line.
 */

import { FC } from "react";
import { PanelSection, PanelSectionRow, ButtonItem, Field, ToggleField } from "@decky/ui";
import type { UpdateNoticeState } from "../../utils/updateNoticeStore";

/** Shown only to a run from a checkout, which is never offered an install. */
export const NOT_INSTALLED_PROGRAM = "Updates install only into the installed program.";

interface UpdatesSectionProps {
  update: UpdateNoticeState;
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

export const UpdatesSection: FC<UpdatesSectionProps> = ({ update, checking, result, onEnabledChange, onCheckNow }) => (
  <PanelSection title="Updates">
    <PanelSectionRow>
      {/* Read-only rows are focusable for the reason every one on a wide pane
          is: the region scrolls by moving focus. */}
      <Field label="Installed" focusable={true}>
        <span data-testid="updates-installed">{update.currentVersion || "—"}</span>
      </Field>
    </PanelSectionRow>
    <PanelSectionRow>
      <Field label="Available" focusable={true}>
        <span data-testid="updates-available">{availableValue(update)}</span>
      </Field>
    </PanelSectionRow>
    {!update.installedProgram && (
      <PanelSectionRow>
        <Field label={<span data-testid="updates-not-installed">{NOT_INSTALLED_PROGRAM}</span>} focusable={true} />
      </PanelSectionRow>
    )}
    <PanelSectionRow>
      <ToggleField
        label="Check for updates daily"
        description="Asks GitHub at most once a day, when Tender loads, whether a newer release is out."
        checked={update.enabled}
        onChange={onEnabledChange}
      />
    </PanelSectionRow>
    <PanelSectionRow>
      <ButtonItem layout="below" onClick={onCheckNow} disabled={checking}>
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
