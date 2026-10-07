/**
 * Controller settings — Steam Input mode dropdown and the "Apply to All
 * Shortcuts" trigger. Pure renderer: parent owns the mode value and the status
 * string.
 */

import { FC } from "react";
import { PanelSection, PanelSectionRow, ButtonItem, Field, DropdownItem } from "@decky/ui";

interface ControllerSectionProps {
  steamInputMode: string;
  steamInputStatus: string;
  /** An apply run is in flight. The button is dead and says so while it is, and
   *  the parent refuses a second press independently — a disabled control still
   *  reports one on the device. */
  applying: boolean;
  onModeChange: (mode: string) => void;
  onApplyMode: () => void;
}

export const ControllerSection: FC<ControllerSectionProps> = ({
  steamInputMode,
  steamInputStatus,
  applying,
  onModeChange,
  onApplyMode,
}) => {
  return (
    <PanelSection title="Controller">
      <PanelSectionRow>
        <DropdownItem
          label="Steam Input Mode"
          description="Controls how Steam handles controller input for ROM shortcuts"
          rgOptions={[
            { data: "default", label: "Default (Recommended)" },
            { data: "force_on", label: "Force On" },
            { data: "force_off", label: "Force Off" },
          ]}
          selectedOption={steamInputMode}
          onChange={(option) => onModeChange(option.data)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onApplyMode} disabled={applying}>
          {applying ? "Applying to all shortcuts…" : "Apply to All Shortcuts"}
        </ButtonItem>
      </PanelSectionRow>
      {steamInputStatus && (
        <PanelSectionRow>
          {/* Focusable for the reason every read-only row on a wide pane is: the
              region scrolls by moving focus, and rows follow this one. */}
          <Field label={steamInputStatus} focusable={true} />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
