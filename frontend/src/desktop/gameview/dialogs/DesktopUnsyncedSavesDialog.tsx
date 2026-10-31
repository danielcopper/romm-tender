/**
 * The desktop client's unsynced-saves prompt, asked when switching away from a
 * version whose local saves haven't been synced to RomM, or couldn't be
 * checked. Big Picture's `UnsyncedSavesSwitchModal` is the other drawing of it.
 */

import type { FC } from "react";
import {
  CANCEL_LABEL,
  SWITCH_ANYWAY_LABEL,
  SYNC_AND_SWITCH_LABEL,
  UNSYNCED_SAVES_TITLE,
  noSyncReason,
  unsyncedSavesDescription,
} from "../../../utils/launchPromptWording";
import type { UnsyncedSavesChoice, UnsyncedSavesPromptArgs } from "../../../utils/versionSwitch";
import { DIALOG_ACTIONS_STYLE, DIALOG_TEXT_STYLE, DesktopDialog, dialogButtonStyle } from "./DesktopDialog";

interface DesktopUnsyncedSavesDialogProps extends UnsyncedSavesPromptArgs {
  onChoice: (choice: UnsyncedSavesChoice) => void;
}

export const DesktopUnsyncedSavesDialog: FC<DesktopUnsyncedSavesDialogProps> = ({
  versionName,
  stranding,
  serverReachable,
  versionGone,
  onChoice,
}) => {
  const noSync = noSyncReason(serverReachable, versionGone);
  return (
    <DesktopDialog
      titleId="tender-desktop-unsynced-saves-title"
      title={UNSYNCED_SAVES_TITLE}
      onDismiss={() => onChoice("cancel")}
    >
      <div style={DIALOG_TEXT_STYLE}>{unsyncedSavesDescription({ versionName, stranding, noSync })}</div>
      <div style={DIALOG_ACTIONS_STYLE}>
        {noSync === null ? (
          <>
            <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice("sync_and_switch")}>
              {SYNC_AND_SWITCH_LABEL}
            </button>
            <button type="button" style={dialogButtonStyle("secondary")} onClick={() => onChoice("switch_anyway")}>
              {SWITCH_ANYWAY_LABEL}
            </button>
          </>
        ) : (
          <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice("switch_anyway")}>
            {SWITCH_ANYWAY_LABEL}
          </button>
        )}
        <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice("cancel")}>
          {CANCEL_LABEL}
        </button>
      </div>
    </DesktopDialog>
  );
};
