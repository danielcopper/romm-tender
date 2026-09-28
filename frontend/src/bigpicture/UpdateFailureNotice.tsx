import { FC } from "react";
import { PanelSectionRow, DialogButton, Focusable } from "@decky/ui";
import {
  UPDATE_FAILURE_REASON,
  dismissUpdateFailureRecord,
  failureCardShows,
  updateFailureSentence,
  useUpdateOutcomeState,
} from "../utils/updateOutcomeStore";
import { logError } from "../api/backend";
import { AMBER } from "./layout/pane";

/**
 * The notice on Main that the installer rolled an update back.
 *
 * States the fact and where its reason is, and jumps to its home, Settings ›
 * Updates, which states it too. Dismiss is per record, so the next rollback
 * raises it again; the record going away — a later update that answered —
 * takes it down as well.
 */
export const UpdateFailureNotice: FC<{ onOpenUpdates: () => void }> = ({ onOpenUpdates }) => {
  const state = useUpdateOutcomeState();

  // `failureCardShows` implies a record; this is the type narrowing, not a second condition.
  if (!failureCardShows(state) || state.failure === null) return null;
  const failure = state.failure;

  const handleDismiss = () => {
    dismissUpdateFailureRecord(failure.rolledBackAt).catch((e) =>
      logError(`Failed to dismiss the rolled-back update notice: ${e}`),
    );
  };

  return (
    <>
      <PanelSectionRow>
        <Focusable onActivate={() => {}}>
          <div
            data-testid="update-failure-notice"
            style={{
              padding: "8px 12px",
              backgroundColor: "rgba(212, 167, 44, 0.15)",
              borderLeft: `3px solid ${AMBER}`,
              borderRadius: "4px",
              fontSize: "12px",
            }}
          >
            <div style={{ fontWeight: "bold", color: AMBER, marginBottom: "4px" }}>
              {updateFailureSentence(failure)}
            </div>
            <div style={{ color: "rgba(255, 255, 255, 0.7)" }}>{UPDATE_FAILURE_REASON}</div>
          </div>
        </Focusable>
      </PanelSectionRow>
      <PanelSectionRow>
        <Focusable flow-children="horizontal" style={{ display: "flex", gap: "8px" }}>
          <DialogButton style={{ flex: 1, minWidth: 0, padding: "8px 0" }} onClick={onOpenUpdates}>
            Open Updates
          </DialogButton>
          <DialogButton style={{ flex: 1, minWidth: 0, padding: "8px 0" }} onClick={handleDismiss}>
            Dismiss
          </DialogButton>
        </Focusable>
      </PanelSectionRow>
    </>
  );
};
