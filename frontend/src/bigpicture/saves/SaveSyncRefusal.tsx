/**
 * Big Picture's SAVES tab refusal view (`docs/architecture/save-file-sync-architecture.md`,
 * "Where the check happens"): shows "Save Sync", the refusal sentence, and any hint
 * (`RefusalBox` in `SaveManagementCard.tsx` renders the desktop counterpart).
 */

import type { FC } from "react";
import { refusalHint } from "../../utils/saveSyncStatement";

interface SaveSyncRefusalProps {
  message: string;
  reason: string | undefined;
  // A line above the sentence.
  lead?: string;
}

export const SaveSyncRefusal: FC<SaveSyncRefusalProps> = ({ message, reason, lead }) => {
  const hint = refusalHint(reason);
  return (
    <div style={{ padding: "0 0 12px" }}>
      <div className="romm-panel-section-title">Save Sync</div>
      {lead === undefined ? null : (
        <div className="romm-panel-muted" style={{ fontSize: "12px", marginBottom: "4px" }}>
          {lead}
        </div>
      )}
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
