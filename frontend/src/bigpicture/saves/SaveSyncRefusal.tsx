/**
 * The SAVES tab of a game whose saves no sync carries: "Save Sync", the sentence its sync is refused with, and no slot
 * to choose (#2153 D2, D7) — whether or not a slot was set up for it before (#2149 D5).
 */

import type { FC } from "react";
import { refusalHint } from "../../utils/saveSyncStatement";

interface SaveSyncRefusalProps {
  message: string;
  reason: string | undefined;
}

export const SaveSyncRefusal: FC<SaveSyncRefusalProps> = ({ message, reason }) => {
  const hint = refusalHint(reason);
  return (
    <div style={{ padding: "0 0 12px" }}>
      <div className="romm-panel-section-title">Save Sync</div>
      <div className="romm-panel-muted" style={{ fontSize: "12px" }}>
        {message}
      </div>
      {hint === null ? null : (
        <div
          className="romm-panel-muted"
          data-testid="save-sync-refusal-hint"
          style={{ fontSize: "12px", marginTop: "6px" }}
        >
          {hint}
        </div>
      )}
    </div>
  );
};
