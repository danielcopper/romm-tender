import type { ReactNode } from "react";
import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { DISPLAY_NAME } from "../utils/toast";

/**
 * What Tender's Quick Access panel shows once it has thrown: the failure and
 * Reload, its one action. Why the panel needs a boundary and what Reload keeps
 * is `docs/architecture/qam-panel.md`, "The boundary".
 */
export function panelFailureCard(failure: string, reload: () => void): ReactNode {
  return (
    <PanelSection title={DISPLAY_NAME}>
      <PanelSectionRow>
        <div style={{ fontSize: "13px", lineHeight: 1.4 }}>
          {DISPLAY_NAME} ran into an error and stopped drawing this panel. The rest of the Quick Access menu is
          unaffected.
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "11px", opacity: 0.75, wordBreak: "break-word" }}>{failure}</div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={reload}>
          Reload {DISPLAY_NAME}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
