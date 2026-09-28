import { FC } from "react";
import { PanelSectionRow, ButtonItem, Focusable } from "@decky/ui";
import {
  dismissUpdateAnnouncementCard,
  updateAnnouncementSentence,
  useUpdateOutcomeState,
} from "../utils/updateOutcomeStore";
import { logError } from "../api/backend";

/**
 * The notice on Main that this start runs on a version that moved — an update,
 * or a return to an earlier release.
 *
 * It has no home, so it carries no jump, only Dismiss. How long it stands:
 * docs/architecture/qam-panel.md, "Notices and homes".
 */
export const UpdateAnnouncementNotice: FC = () => {
  const { announcement } = useUpdateOutcomeState();
  if (announcement === null) return null;

  const handleDismiss = () => {
    dismissUpdateAnnouncementCard().catch((e) => logError(`Failed to dismiss the update announcement: ${e}`));
  };

  return (
    <>
      <PanelSectionRow>
        <Focusable onActivate={() => {}}>
          <div
            data-testid="update-announcement-notice"
            style={{
              padding: "8px 12px",
              backgroundColor: "rgba(61, 157, 246, 0.15)",
              borderLeft: "3px solid #3d9df6",
              borderRadius: "4px",
              fontSize: "12px",
            }}
          >
            <div style={{ fontWeight: "bold", color: "#3d9df6" }}>{updateAnnouncementSentence(announcement)}</div>
          </div>
        </Focusable>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" bottomSeparator="none" onClick={handleDismiss}>
          Dismiss
        </ButtonItem>
      </PanelSectionRow>
    </>
  );
};
