/**
 * Plugin-wide developer/diagnostic toggles. Houses the log-level dropdown and
 * the update check; future additions belong here when they're orthogonal to any
 * other panel. Pure renderer: parent owns the current values.
 */

import { FC } from "react";
import { PanelSection, PanelSectionRow, DropdownItem, ToggleField } from "@decky/ui";

interface AdvancedSectionProps {
  logLevel: string;
  onLogLevelChange: (level: string) => void;
  updateCheckEnabled: boolean;
  onUpdateCheckEnabledChange: (enabled: boolean) => void;
}

export const AdvancedSection: FC<AdvancedSectionProps> = ({
  logLevel,
  onLogLevelChange,
  updateCheckEnabled,
  onUpdateCheckEnabledChange,
}) => {
  return (
    <PanelSection title="Advanced">
      <PanelSectionRow>
        <ToggleField
          label="Check for plugin updates"
          // Names the destination because this is the plugin's only outgoing
          // request that goes neither to the user's own RomM server nor to
          // SteamGridDB, which is what makes it worth a switch at all.
          description="Asks GitHub whether a newer Tender release exists — at most once a day, when Tender starts up — and shows a card when there is one."
          checked={updateCheckEnabled}
          onChange={onUpdateCheckEnabledChange}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label="Log Level"
          description="Controls how much detail is written to plugin logs"
          rgOptions={[
            { data: "error", label: "Error" },
            { data: "warn", label: "Warn" },
            { data: "info", label: "Info" },
            { data: "debug", label: "Debug" },
          ]}
          selectedOption={logLevel}
          onChange={(option) => onLogLevelChange(option.data)}
        />
      </PanelSectionRow>
    </PanelSection>
  );
};
