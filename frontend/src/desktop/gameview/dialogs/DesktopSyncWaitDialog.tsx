/**
 * The desktop drawing of `LaunchPrompts.askSyncWait` (`utils/launchVerdict.ts`).
 * `shared/SyncWaitModal` is its gamepad drawing.
 */

import { useEffect, useState, type FC } from "react";
import type { SyncFinish, SyncWaitChoice, SyncWaitReason } from "../../../utils/launchGate";
import {
  CANCEL_LABEL,
  SYNC_READY_DESCRIPTION,
  SYNC_READY_PLAY_LABEL,
  SYNC_READY_TITLE,
  SYNC_WAIT_LABEL,
  SYNC_WAIT_PLAY_ANYWAY_LABEL,
  SYNC_WAIT_TITLE,
  syncWaitDescription,
} from "../../../utils/launchPromptWording";
import { DIALOG_ACTIONS_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopSyncWaitDialogProps {
  reason: SyncWaitReason;
  finished: Promise<SyncFinish>;
  onChoice: (choice: SyncWaitChoice) => void;
}

export const DesktopSyncWaitDialog: FC<DesktopSyncWaitDialogProps> = ({ reason, finished, onChoice }) => {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let open = true;
    void finished.then((finish) => {
      if (!open) return;
      if (finish === "ready") {
        setReady(true);
        return;
      }
      open = false;
      onChoice("answered");
    });
    return () => {
      open = false;
    };
  }, [finished, onChoice]);

  if (ready) {
    return (
      <DesktopDialog
        titleId="tender-desktop-sync-wait-title"
        title={SYNC_READY_TITLE}
        onDismiss={() => onChoice("cancel")}
      >
        <div style={DIALOG_TEXT_STYLE}>{SYNC_READY_DESCRIPTION}</div>
        <div style={DIALOG_ACTIONS_STYLE}>
          <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice("answered")}>
            {SYNC_READY_PLAY_LABEL}
          </button>
          <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice("cancel")}>
            {CANCEL_LABEL}
          </button>
        </div>
      </DesktopDialog>
    );
  }
  return (
    <DesktopDialog
      titleId="tender-desktop-sync-wait-title"
      title={SYNC_WAIT_TITLE}
      onDismiss={() => onChoice("cancel")}
    >
      <div style={DIALOG_TEXT_STYLE}>{syncWaitDescription(reason)}</div>
      <div style={DIALOG_ACTIONS_STYLE}>
        <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice("wait")}>
          {SYNC_WAIT_LABEL}
        </button>
        <button type="button" style={dialogButtonStyle("secondary")} onClick={() => onChoice("play_anyway")}>
          {SYNC_WAIT_PLAY_ANYWAY_LABEL}
        </button>
        <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice("cancel")}>
          {CANCEL_LABEL}
        </button>
      </div>
    </DesktopDialog>
  );
};
