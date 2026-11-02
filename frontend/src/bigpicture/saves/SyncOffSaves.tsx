/**
 * The SAVES tab while save sync is off: "Save sync is off.", and this device's save files for the game — or, for a
 * downloaded game whose saves no sync would carry, the sentence its sync is refused with (#2527 D1). A game not
 * downloaded reads that no save lies here, refused or not (D5). It offers nothing to press that needs save sync; each
 * row is focusable only so a controller can reach it.
 */

import type { FC, ReactElement } from "react";
import { DialogButton } from "@decky/ui";
import type { LocalSaveFile, LocalSaveFiles } from "../../types";
import { formatBytes, formatTimestamp } from "../../utils/formatters";
import { scrollFocusedToCenter } from "../../utils/scrollHelpers";
import { NO_SAVE_ON_DEVICE } from "../../utils/saveSyncStatement";
import { infoRow } from "./SaveFileRow";
import { SaveSyncRefusal } from "./SaveSyncRefusal";

export const SAVE_SYNC_OFF = "Save sync is off.";

/** The line a game that is not downloaded reads under {@link SAVE_SYNC_OFF}. */
export const NOT_DOWNLOADED_NO_SAVE = "Not downloaded — no save on this device.";

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
  // `null` while no answer is held: the line stands alone until the read lands.
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
