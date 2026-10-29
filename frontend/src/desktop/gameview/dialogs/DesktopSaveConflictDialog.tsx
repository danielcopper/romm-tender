/**
 * The desktop drawing of one conflict `LaunchPrompts.resolveConflicts` walks
 * (`utils/launchVerdict.ts`). The resolution runs from inside the dialog, so
 * anything short of a resolution that landed is shown here and the user can
 * retry or cancel; a resolution closes it only once it has landed.
 * `shared/SyncConflictModal` is its gamepad drawing.
 */

import { useState, type FC } from "react";
import {
  CONFLICT_EXPLANATION,
  conflictTitle,
  localSaveDetail,
  resolveOneConflict,
  serverSaveDetail,
  serverSaveLabel,
  type SyncConflictAction,
  type SyncConflictResolution,
} from "../../../utils/saveConflictFlow";
import { detach } from "../../../utils/detach";
import { CANCEL_LABEL } from "../../../utils/launchPromptWording";
import { showToast } from "../../../utils/toast";
import type { SyncConflict } from "../../../types";
import { DIALOG_MUTED_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopSaveConflictDialogProps {
  conflict: SyncConflict;
  /** Only for a conflict the launch check found, as `resolveOneConflict` takes it. */
  beforeStart: boolean;
  onDone: (resolution: SyncConflictResolution) => void;
}

const SIDE_STYLE = {
  padding: "10px 12px",
  borderRadius: "4px",
  marginBottom: "10px",
  display: "flex",
  flexDirection: "column" as const,
  gap: "6px",
  alignItems: "flex-start" as const,
};

export const DesktopSaveConflictDialog: FC<DesktopSaveConflictDialogProps> = ({ conflict, beforeStart, onDone }) => {
  const [resolving, setResolving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleResolve = async (action: SyncConflictAction) => {
    if (resolving) return;
    setResolving(true);
    setErrorMessage(null);
    const outcome = await resolveOneConflict(conflict, action, beforeStart);
    if (!outcome.ok) {
      setErrorMessage(outcome.message);
      setResolving(false);
      return;
    }
    showToast(outcome.toast);
    onDone(action);
  };

  const cancel = () => onDone("cancel");

  return (
    <DesktopDialog
      titleId="tender-desktop-save-conflict-title"
      title={conflictTitle(conflict)}
      onDismiss={resolving ? undefined : cancel}
    >
      <div style={DIALOG_TEXT_STYLE}>{CONFLICT_EXPLANATION}</div>

      <div
        className="tender-desktop-save-conflict-local"
        style={{ ...SIDE_STYLE, background: "rgba(91, 163, 43, 0.12)", border: "1px solid rgba(91, 163, 43, 0.35)" }}
      >
        <div style={{ fontSize: "13px", fontWeight: 700, color: "#8bc53f" }}>Your local save</div>
        <div style={DIALOG_MUTED_STYLE}>{localSaveDetail(conflict)}</div>
        <button
          type="button"
          style={dialogButtonStyle("secondary", resolving)}
          disabled={resolving}
          onClick={() => detach(handleResolve("keep_local"))}
        >
          Keep Local
        </button>
      </div>

      <div
        className="tender-desktop-save-conflict-server"
        style={{ ...SIDE_STYLE, background: "rgba(26, 159, 255, 0.12)", border: "1px solid rgba(26, 159, 255, 0.35)" }}
      >
        <div style={{ fontSize: "13px", fontWeight: 700, color: "#66c0f4" }}>{serverSaveLabel(conflict)}</div>
        <div style={DIALOG_MUTED_STYLE}>{serverSaveDetail(conflict)}</div>
        <button
          type="button"
          style={dialogButtonStyle("secondary", resolving)}
          disabled={resolving}
          onClick={() => detach(handleResolve("use_server"))}
        >
          Use Server
        </button>
      </div>

      {errorMessage ? (
        <div
          role="alert"
          className="tender-desktop-save-conflict-error"
          style={{
            padding: "8px 10px",
            background: "rgba(200, 60, 60, 0.15)",
            border: "1px solid rgba(255, 107, 107, 0.35)",
            borderRadius: "4px",
            marginBottom: "12px",
            fontSize: "12px",
            color: "#ff8a80",
            lineHeight: 1.4,
          }}
        >
          {errorMessage}
        </div>
      ) : null}

      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button type="button" style={dialogButtonStyle("quiet", resolving)} disabled={resolving} onClick={cancel}>
          {CANCEL_LABEL}
        </button>
      </div>
    </DesktopDialog>
  );
};
