/**
 * Expandable per-save version history sub-panel. Lazy-loads previous versions
 * when first expanded and drives the restore flow (with conflict pre-flight
 * fallback to the standard sync-conflict modal).
 */

import { FC, type ReactElement } from "react";
import { DialogButton } from "@decky/ui";
import type { SaveVersionEntry } from "../../types";
import { showSyncConflictModal } from "../../shared/SyncConflictModal";
import { scrollFocusedToCenter } from "../../utils/scrollHelpers";
import { formatBytes, formatTimestamp } from "../../utils/formatters";
import { formatAttributionSegment, pickLastSyncer } from "../../utils/saveHelpers";
import { VERSIONS_OFFLINE_TEXT } from "../../utils/saveSlotWording";
import { useFileVersionHistory, type FileVersionHistoryTarget } from "../../utils/useFileVersionHistory";
import { renderCopyToSlotButton, type CopyToSlotHandler } from "./CopyToSlotButton";
import { detach } from "../../utils/detach";
import { ThrobberLabel } from "../../shared/Throbber";

interface VersionHistoryPanelProps extends FileVersionHistoryTarget {
  /** Opens the copy-to-slot picker for a version row's save id (source = this slot). */
  onCopy?: CopyToSlotHandler;
}

export const VersionHistoryPanel: FC<VersionHistoryPanelProps> = (props) => {
  const { slot, filename, isOffline, onCopy } = props;
  const { expanded, versions, loading, restoringVersionId, loadError, handleToggle, fetchVersions, handleRestore } =
    useFileVersionHistory(props, showSyncConflictModal);

  const versionCount = versions?.length ?? 0;

  const renderVersionRow = (v: SaveVersionEntry): ReactElement => {
    const lastSyncer = pickLastSyncer(v.device_syncs);
    const deviceName = lastSyncer?.device_name ?? null;
    const isThisRestoring = restoringVersionId === v.id;

    // Line 1: #id · emulator · size
    const headerParts: string[] = [`#${v.id}`];
    if (v.emulator) headerParts.push(v.emulator);
    if (v.file_size_bytes != null) headerParts.push(formatBytes(v.file_size_bytes));

    // Line 2: Last updated: <timestamp>[ · <device label> ✓]  — see formatAttributionSegment
    const lastUpdatedParts: string[] = [formatTimestamp(v.updated_at)];
    const attrSegment = formatAttributionSegment(v.uploaded_by_us, deviceName);
    if (attrSegment !== null) lastUpdatedParts.push(attrSegment);

    return (
      <div
        key={`ver-${v.id}`}
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: "8px",
          padding: "6px 0",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        {/* Info column (grows) */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Line 1: #id · emulator · size */}
          <div style={{ fontSize: "12px", color: "#c7cdd3", fontWeight: 600 }}>{headerParts.join(" · ")}</div>
          {/* Line 2: last updated + device */}
          <div
            style={{
              fontSize: "11px",
              color: "#8f98a0",
              marginTop: "2px",
            }}
          >
            <span style={{ color: "#697075" }}>Last updated: </span>
            {lastUpdatedParts.join(" · ")}
          </div>
          {/* Line 3: server filename (technical, bottom) */}
          <div
            style={{
              fontSize: "11px",
              color: "#8f98a0",
              fontFamily: "monospace",
              wordBreak: "break-all" as const,
              marginTop: "2px",
            }}
          >
            {v.file_name}
          </div>
        </div>
        {/* Action column (fixed right): Restore + optional Copy-to-slot. */}
        <div style={{ display: "flex", flexDirection: "column" as const, gap: "4px", flexShrink: 0 }}>
          {/* Restore button (disabled when offline) */}
          <DialogButton
            key="restore"
            style={{
              padding: "2px 8px",
              minWidth: "auto",
              fontSize: "11px",
              width: "auto",
              flexShrink: 0,
            }}
            noFocusRing={false}
            onFocus={scrollFocusedToCenter}
            disabled={isThisRestoring || restoringVersionId !== null || isOffline}
            onClick={() => {
              detach(handleRestore(v));
            }}
          >
            <ThrobberLabel busy={isThisRestoring}>{isThisRestoring ? "Restoring..." : "Restore"}</ThrobberLabel>
          </DialogButton>
          {/* Copy-to-slot button (source = this slot); disabled offline via the shared helper. */}
          {onCopy ? renderCopyToSlotButton(`copy-ver-${v.id}`, v.id, { onCopy, sourceSlot: slot, isOffline }) : null}
        </div>
      </div>
    );
  };

  const renderBody = (): ReactElement | ReactElement[] => {
    if (isOffline) {
      return (
        <div style={{ fontSize: "11px", color: "#8f98a0", fontStyle: "italic" as const }}>{VERSIONS_OFFLINE_TEXT}</div>
      );
    }
    if (loading) {
      return (
        <div style={{ fontSize: "11px", color: "#8f98a0" }}>
          <ThrobberLabel busy>Loading...</ThrobberLabel>
        </div>
      );
    }
    if (loadError !== null) {
      // Distinct from the empty-list case: surface a retry affordance so
      // the user isn't misled into thinking there are no versions when
      // the server was actually unreachable.
      return (
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "11px", color: "#c46161", fontStyle: "italic" as const }}>{loadError}</span>
          <DialogButton
            style={{ padding: "2px 8px", minWidth: "auto", fontSize: "11px", width: "auto", flexShrink: 0 }}
            noFocusRing={false}
            onFocus={scrollFocusedToCenter}
            onClick={() => {
              detach(fetchVersions());
            }}
          >
            Retry
          </DialogButton>
        </div>
      );
    }
    if (versionCount === 0) {
      return (
        <div style={{ fontSize: "11px", color: "#8f98a0", fontStyle: "italic" as const }}>
          No older versions available
        </div>
      );
    }
    return (versions ?? []).map(renderVersionRow);
  };

  return (
    <div key={`history-${filename}`} style={{ marginTop: "4px", marginLeft: "8px" }}>
      {/* Expander toggle */}
      <DialogButton
        style={{
          background: "transparent",
          border: "none",
          padding: "2px 0",
          textAlign: "left" as const,
          width: "100%",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: "4px",
          fontSize: "11px",
          color: "#8f98a0",
        }}
        noFocusRing={false}
        onFocus={scrollFocusedToCenter}
        onClick={handleToggle}
      >
        <span>{expanded ? "▾" : "▸"}</span>
        <span>{expanded && versions !== null ? `Previous Versions (${versionCount})` : "Previous Versions"}</span>
      </DialogButton>

      {/* Version list (lazy-loaded) */}
      {expanded ? <div style={{ marginTop: "4px" }}>{renderBody()}</div> : null}
    </div>
  );
};
