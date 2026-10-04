/**
 * Expandable per-save version history sub-panel. Lazy-loads previous versions
 * when first expanded and drives the restore flow (with conflict pre-flight
 * fallback to the standard sync-conflict modal).
 */

import { useState, useEffect, FC, type ReactElement } from "react";
import { DialogButton } from "@decky/ui";
import { showToast } from "../../utils/toast";
import { debugLog, savesListFileVersions, savesRollbackToVersion } from "../../api/backend";
import type { SaveVersionEntry, RollbackStatus, ListFileVersionsResult } from "../../types";
import { showSyncConflictModal } from "../../shared/SyncConflictModal";
import { scrollFocusedToCenter } from "../../utils/scrollHelpers";
import { formatBytes, formatTimestamp } from "../../utils/formatters";
import { formatAttributionSegment, pickLastSyncer, formatRollbackFeedback } from "../../utils/saveHelpers";
import { renderCopyToSlotButton, type CopyToSlotHandler } from "./CopyToSlotButton";
import { detach } from "../../utils/detach";

interface VersionHistoryPanelProps {
  romId: number;
  slot: string;
  filename: string;
  isOffline: boolean;
  onRestored: () => void;
  /** Opens the copy-to-slot picker for a version row's save id (source = this slot). */
  onCopy?: CopyToSlotHandler;
}

export const VersionHistoryPanel: FC<VersionHistoryPanelProps> = ({
  romId,
  slot,
  filename,
  isOffline,
  onRestored,
  onCopy,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [versions, setVersions] = useState<SaveVersionEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadVersions = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const result: ListFileVersionsResult = await savesListFileVersions(romId, slot, filename);
      if (result.status === "ok" || result.status === "multi_file_unsupported") {
        // multi_file_unsupported carries an empty list — a multi-file slot's
        // siblings are components, not prior versions (#908). The panel is
        // hidden for multi-file slots anyway; this is the defensive backstop.
        setVersions(result.versions);
      } else if (result.status === "not_found") {
        // The server answered — blaming the connection here would be a lie (#1570).
        detach(debugLog(`VersionHistoryPanel: server has no such entry for ${filename}: ${result.message}`));
        setVersions(null);
        setLoadError("RomM couldn't find this game's save data.");
      } else {
        detach(debugLog(`VersionHistoryPanel: server unreachable for ${filename}: ${result.message}`));
        setVersions(null);
        setLoadError("Couldn't reach RomM. Tap retry.");
      }
    } catch (e) {
      detach(debugLog(`VersionHistoryPanel: failed to load versions for ${filename}: ${e}`));
      setVersions(null);
      setLoadError("Couldn't reach RomM. Tap retry.");
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = async () => {
    const willExpand = !expanded;
    setExpanded(willExpand);
    if (willExpand && versions === null && loadError === null && !isOffline) {
      await loadVersions();
    }
  };

  // Self-refresh when this ROM's saves change. A copy into this slot (or a sync
  // / external rollback) adds or removes a version, but the list is cached on
  // first expand — without this it stays stale in-session until the game page is
  // re-entered. The signal is a DOM CustomEvent (`globalThis.dispatchEvent`),
  // NOT a backend emit, so we listen on `globalThis`. Invalidate on any
  // change for this ROM; reload immediately when the panel is open, otherwise the
  // next expand lazy-loads the fresh list. `expanded` is a dep so the handler
  // always sees the live open/closed state (re-subscribing on toggle is cheap).
  useEffect(() => {
    const onDataChanged = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.rom_id !== romId) return;
      setVersions(null);
      setLoadError(null);
      if (expanded && !isOffline) detach(loadVersions());
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => globalThis.removeEventListener("romm_data_changed", onDataChanged);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loadVersions is stable per (romId, slot, filename); romId/isOffline/expanded are the real deps
  }, [romId, isOffline, expanded]);

  const handleRestore = async (version: SaveVersionEntry) => {
    setRestoring(version.id);
    try {
      const result: RollbackStatus = await savesRollbackToVersion(romId, slot, version.id);
      const feedback = formatRollbackFeedback(result, version.updated_at);
      if (feedback.kind === "conflict") {
        await showSyncConflictModal(feedback.conflict);
      } else {
        showToast(feedback.message);
      }
      if (result.status === "ok" || result.status === "put_failed") {
        setVersions(null);
        setExpanded(false);
        onRestored();
      }
    } catch (e) {
      detach(debugLog(`VersionHistoryPanel: restore error for save ${version.id}: ${e}`));
      showToast("Couldn't confirm the restore.", { subtext: e instanceof Error ? e.message : String(e) });
    } finally {
      setRestoring(null);
    }
  };

  const versionCount = versions?.length ?? 0;

  const renderVersionRow = (v: SaveVersionEntry): ReactElement => {
    const lastSyncer = pickLastSyncer(v.device_syncs);
    const deviceName = lastSyncer?.device_name ?? null;
    const isThisRestoring = restoring === v.id;

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
            disabled={isThisRestoring || restoring !== null || isOffline}
            onClick={() => {
              detach(handleRestore(v));
            }}
          >
            {isThisRestoring ? "Restoring..." : "Restore"}
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
        <div style={{ fontSize: "11px", color: "#8f98a0", fontStyle: "italic" as const }}>
          Offline — versions unavailable
        </div>
      );
    }
    if (loading) {
      return <div style={{ fontSize: "11px", color: "#8f98a0" }}>Loading...</div>;
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
              detach(loadVersions());
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
        onClick={() => {
          detach(handleToggle());
        }}
      >
        <span>{expanded ? "▾" : "▸"}</span>
        <span>{expanded && versions !== null ? `Previous Versions (${versionCount})` : "Previous Versions"}</span>
      </DialogButton>

      {/* Version list (lazy-loaded) */}
      {expanded ? <div style={{ marginTop: "4px" }}>{renderBody()}</div> : null}
    </div>
  );
};
