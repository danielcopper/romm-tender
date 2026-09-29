import { FC } from "react";
import { PanelSectionRow, DialogButton, Field, Focusable } from "@decky/ui";
import { dismissStoppedUpdateCard, useStoppedUpdateAttempt } from "../utils/stoppedUpdateStore";
import { INSTALL_FAILURE_SENTENCES } from "../utils/updateInstallView";
import { updateDidNotGoThrough } from "../utils/updateOutcomeStore";
import { logError } from "../api/backend";
import { AMBER, AMBER_WASH } from "./layout/pane";

/**
 * The notice on Main that an update's installer stopped without updating.
 *
 * The installer stops Tender before it replaces it, so an installer that gave
 * up after that point is only found by the next start. The card says so in the
 * words the rolled-back notice uses, points at the journal, and jumps to
 * Settings › Updates, where the attempt stands as failed with Try again.
 * Dismiss removes the backend's record, so the next start does not raise it
 * again.
 */
export const UpdateStoppedNotice: FC<{ onOpenUpdates: () => void }> = ({ onOpenUpdates }) => {
  const attempt = useStoppedUpdateAttempt();
  if (attempt === null) return null;

  const handleDismiss = () => {
    dismissStoppedUpdateCard().catch((e) => logError(`Failed to dismiss the stopped update notice: ${e}`));
  };

  return (
    <>
      <PanelSectionRow>
        <Focusable onActivate={() => {}}>
          <div
            data-testid="update-stopped-notice"
            style={{
              padding: "8px 12px",
              backgroundColor: AMBER_WASH,
              borderLeft: `3px solid ${AMBER}`,
              borderRadius: "4px",
              fontSize: "12px",
            }}
          >
            <div style={{ fontWeight: "bold", color: AMBER, marginBottom: "4px" }}>
              {updateDidNotGoThrough(attempt.attemptedVersion, attempt.fromVersion)}
            </div>
            <div style={{ color: "rgba(255, 255, 255, 0.7)" }}>{INSTALL_FAILURE_SENTENCES.installer_stopped}</div>
          </div>
        </Focusable>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field bottomSeparator="none" childrenLayout="below" childrenContainerWidth="max">
          <Focusable flow-children="horizontal" style={{ display: "flex", gap: "8px" }}>
            <DialogButton style={{ flex: 1, minWidth: 0, padding: "8px 0" }} onClick={onOpenUpdates}>
              Open Updates
            </DialogButton>
            <DialogButton style={{ flex: 1, minWidth: 0, padding: "8px 0" }} onClick={handleDismiss}>
              Dismiss
            </DialogButton>
          </Focusable>
        </Field>
      </PanelSectionRow>
    </>
  );
};
