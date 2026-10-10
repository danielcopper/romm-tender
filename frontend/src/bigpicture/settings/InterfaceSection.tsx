/**
 * Where Tender shows up in Steam's own interface: today, whether its Quick
 * Access entry sits above or below Decky Loader's. Pure renderer: parent owns
 * the value and the save.
 *
 * Shown whether or not Decky Loader is installed — the panel does not ask —
 * which is why the line under the toggle says where it applies.
 */

import { FC } from "react";
import { PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import type { QamEntryPosition } from "../../types";

interface InterfaceSectionProps {
  qamEntryPosition: QamEntryPosition;
  onQamEntryPositionChange: (position: QamEntryPosition) => void;
}

export const InterfaceSection: FC<InterfaceSectionProps> = ({ qamEntryPosition, onQamEntryPositionChange }) => {
  return (
    <PanelSection title="Interface">
      <PanelSectionRow>
        <ToggleField
          label="Place Tender above Decky Loader"
          description="Applies only where Decky Loader is installed."
          checked={qamEntryPosition === "above_decky"}
          onChange={(above) => onQamEntryPositionChange(above ? "above_decky" : "below_decky")}
        />
      </PanelSectionRow>
    </PanelSection>
  );
};
