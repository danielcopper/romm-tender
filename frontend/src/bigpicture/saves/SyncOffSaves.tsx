/**
 * Big Picture's SAVES tab view while save sync is off
 * (`docs/architecture/save-file-sync-architecture.md`, "This device's save files (save sync off)"):
 * lists this device's save files or the refusal/not-downloaded sentence (`SyncOffSection` in
 * `SaveManagementCard.tsx` renders the desktop counterpart). Each row is focusable for controller navigation.
 */

import type { FC, ReactElement } from "react";
import { DialogButton } from "@decky/ui";
import type { LocalSaveFile, LocalSaveFiles } from "../../types";
import { formatBytes, formatTimestamp } from "../../utils/formatters";
import { scrollFocusedToCenter } from "../../utils/scrollHelpers";
import { NOT_DOWNLOADED_NO_SAVE, NO_SAVE_ON_DEVICE, SAVE_SYNC_OFF } from "../../utils/saveSyncStatement";
import { infoRow } from "./SaveFileRow";
import { SaveSyncRefusal } from "./SaveSyncRefusal";

function localSaveRow(file: LocalSaveFile): ReactElement {
  return (
    <DialogButton
      key={`${file.directory}/${file.filename}`}
      data-testid="local-save-row"
      style={{
        background: "transparent",
        border: "none",
        padding: "8px 0",
        textAlign: "left" as const,
        width: "100%",
        cursor: "default",
        display: "block",
      }}
      noFocusRing={false}
      onFocus={scrollFocusedToCenter}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
        <div style={{ fontSize: "13px", color: "#dcdedf", fontWeight: 600, flex: 1, minWidth: 0 }}>{file.filename}</div>
        <span style={{ fontSize: "11px", color: "#8f98a0", flexShrink: 0 }}>{formatBytes(file.size)}</span>
      </div>
      {infoRow("modified", "Last changed:", formatTimestamp(file.modified_at), "#8f98a0")}
      {infoRow(
        "folder",
        "Folder:",
        <span style={{ fontFamily: "monospace", wordBreak: "break-all" as const }}>{file.directory}</span>,
        "#5a6066",
      )}
    </DialogButton>
  );
}

function filesBody(answer: LocalSaveFiles): ReactElement {
  if (!answer.installed) {
    return (
      <div className="romm-panel-muted" style={{ fontSize: "12px", marginTop: "4px" }}>
        {NOT_DOWNLOADED_NO_SAVE}
      </div>
    );
  }
  if (answer.files.length === 0) {
    return (
      <div className="romm-panel-muted" style={{ fontSize: "12px", marginTop: "4px" }}>
        {NO_SAVE_ON_DEVICE}
      </div>
    );
  }
  return <div style={{ marginTop: "4px" }}>{answer.files.map(localSaveRow)}</div>;
}

interface SyncOffSavesProps {
  // `null` while no answer is held (`PanelState.localSaves`): the line stands alone.
  answer: LocalSaveFiles | null;
}

export const SyncOffSaves: FC<SyncOffSavesProps> = ({ answer }) => {
  if (answer && !answer.available && answer.installed) {
    return <SaveSyncRefusal lead={SAVE_SYNC_OFF} message={answer.message ?? ""} reason={answer.reason} />;
  }
  return (
    <div data-testid="saves-sync-off" style={{ padding: "0 0 12px" }}>
      <div className="romm-panel-section-title">Save Sync</div>
      <div className="romm-panel-muted" style={{ fontSize: "12px" }}>
        {SAVE_SYNC_OFF}
      </div>
      {answer ? filesBody(answer) : null}
    </div>
  );
};
