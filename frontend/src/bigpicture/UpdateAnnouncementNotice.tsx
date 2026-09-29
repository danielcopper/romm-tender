import { FC } from "react";
import { PanelSectionRow, ButtonItem } from "@decky/ui";
import { UpdateCardBody } from "./UpdateCard";
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
      <UpdateCardBody
        testId="update-announcement-notice"
        color="#3d9df6"
        wash="rgba(61, 157, 246, 0.15)"
        title={updateAnnouncementSentence(announcement)}
      />
      <PanelSectionRow>
        <ButtonItem layout="below" bottomSeparator="none" onClick={handleDismiss}>
          Dismiss
        </ButtonItem>
      </PanelSectionRow>
    </>
  );
};
