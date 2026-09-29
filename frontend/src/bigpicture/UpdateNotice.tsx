import { FC } from "react";
import { PanelSectionRow, DialogButton, Field, Focusable } from "@decky/ui";
import { dismissUpdateForVersion, useUpdateNoticeState } from "../utils/updateNoticeStore";
import { failureTakesThePlaceOf, useUpdateOutcomeState } from "../utils/updateOutcomeStore";
import { logError } from "../api/backend";

/**
 * The notice on Main that a newer Tender release is out.
 *
 * A notice and nothing more: it names the release and jumps to its home,
 * Settings › Updates, which states both versions and holds the check's
 * controls. Dismiss is per version, so the next release raises it again. It
 * gives way to the rolled-back notice for the version that update tried.
 */
export const UpdateNotice: FC<{ onOpenUpdates: () => void }> = ({ onOpenUpdates }) => {
  const state = useUpdateNoticeState();
  const outcome = useUpdateOutcomeState();

  // `available` is decided by comparing a version, so it implies one; this is
  // the type narrowing, not a second condition.
  if (!state.available || state.latestVersion === null) return null;
  if (failureTakesThePlaceOf(state.latestVersion, outcome)) return null;
  const latestVersion = state.latestVersion;

  const handleDismiss = () => {
    dismissUpdateForVersion(latestVersion).catch((e) => logError(`Failed to dismiss the update notice: ${e}`));
  };

  return (
    <>
      <PanelSectionRow>
        <Focusable onActivate={() => {}}>
          <div
            data-testid="update-notice"
            style={{
              padding: "8px 12px",
              backgroundColor: "rgba(61, 157, 246, 0.15)",
              borderLeft: "3px solid #3d9df6",
              borderRadius: "4px",
              fontSize: "12px",
            }}
          >
            <div style={{ fontWeight: "bold", color: "#3d9df6", marginBottom: "4px" }}>
              Tender {latestVersion} is available
            </div>
            <div style={{ color: "rgba(255, 255, 255, 0.7)" }}>Installed version: {state.currentVersion}.</div>
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
