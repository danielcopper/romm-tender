/**
 * Controller settings — Steam Input mode dropdown, "Apply to All Shortcuts"
 * trigger, and the RetroArch input_driver warning + auto-fix affordance.
 * Pure renderer: parent owns the mode value, status strings, and warning data.
 *
 * This is the input_driver fix's only home. Main names the condition and jumps
 * here; the button that changes the config exists nowhere else.
 */

import { FC } from "react";
import { PanelSection, PanelSectionRow, ButtonItem, Field, DropdownItem } from "@decky/ui";
import type { RetroArchInputCheck } from "../../types";

interface ControllerSectionProps {
  steamInputMode: string;
  steamInputStatus: string;
  retroarchWarning: RetroArchInputCheck | null;
  retroarchFixStatus: string;
  /** An apply run is in flight. The button is dead and says so while it is, and
   *  the parent refuses a second press independently — a disabled control still
   *  reports one on the device. */
  applying: boolean;
  onModeChange: (mode: string) => void;
  onApplyMode: () => void;
  onFixInputDriver: () => void;
}

export const ControllerSection: FC<ControllerSectionProps> = ({
  steamInputMode,
  steamInputStatus,
  retroarchWarning,
  retroarchFixStatus,
  applying,
  onModeChange,
  onApplyMode,
  onFixInputDriver,
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
      {retroarchWarning?.warning && (
        <>
          <PanelSectionRow>
            <Field
              label={`RetroArch input_driver: "${retroarchWarning.current}"`}
              description="Controller navigation in RetroArch menus may not work with this setting."
              focusable={true}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={onFixInputDriver}>
              Fix input_driver to sdl2
            </ButtonItem>
          </PanelSectionRow>
          {retroarchFixStatus && (
            <PanelSectionRow>
              <Field label={retroarchFixStatus} focusable={true} />
            </PanelSectionRow>
          )}
        </>
      )}
    </PanelSection>
  );
};
