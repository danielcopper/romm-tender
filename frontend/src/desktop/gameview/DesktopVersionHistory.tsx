/**
 * Expandable per-save version history subsection for DesktopSlotPanel.
 * Lazy-loads prior server versions on expand, shows offline / distinct error
 * states (#1570) with Retry, and supports Restore and Copy to slot... on rows.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties, type FC } from "react";
import type { SaveVersionEntry } from "../../types";
import { detach } from "../../utils/detach";
import { formatBytes, formatTimestamp } from "../../utils/formatters";
import { MUTED_COLOR, formatAttributionSegment, pickLastSyncer } from "../../utils/saveHelpers";
import { executeVersionRestore, loadFileVersions } from "../../utils/saveSlotFlow";
import { SvgThrobber } from "./SvgThrobber";
import type { AskDialog } from "./dialogs/askDialog";
import { desktopSlotConflictDialog } from "./dialogs/desktopDialogs";
import { BUTTON_STYLE } from "./styles";

export interface DesktopVersionHistoryProps {
  romId: number;
  slot: string;
  filename: string;
  isOffline: boolean;
  ask: AskDialog;
  onRestored: () => void;
  onCopySave: (saveId: number, sourceSlot: string) => void;
}

const TABLE_GRID_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(140px, 1.4fr) 68px minmax(140px, 1fr) auto",
  gap: "8px",
  alignItems: "center",
};

const SMALL_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_STYLE,
  padding: "2px 8px",
  fontSize: "11px",
  display: "inline-flex",
  alignItems: "center",
  gap: "4px",
  whiteSpace: "nowrap",
};

function renderAttributionWithCheckmark(attr: string | null) {
  if (attr === null) return null;
  if (attr === "✓") {
    return (
      <>
        {" · "}
        <span style={{ color: "#5ba32b" }}>✓</span>
      </>
    );
  }
  if (attr.endsWith(" ✓")) {
    return (
      <>
        {` · ${attr.slice(0, -2)} `}
        <span style={{ color: "#5ba32b" }}>✓</span>
      </>
    );
  }
  return ` · ${attr}`;
}

export const DesktopVersionHistory: FC<DesktopVersionHistoryProps> = ({
  romId,
  slot,
  filename,
  isOffline,
  ask,
  onRestored,
  onCopySave,
}) => {
  const [expanded, setExpanded] = useState(false);
  // Synchronous ref avoids re-loading when a successful restore collapses the
  // subsection and announces `save_sync` in the same turn.
  const expandedRef = useRef(false);
  const setOpen = (open: boolean) => {
    expandedRef.current = open;
    setExpanded(open);
  };

  const [versions, setVersions] = useState<SaveVersionEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoringVersionId, setRestoringVersionId] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const readSeqRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const fetchVersions = useCallback(async () => {
    const seq = ++readSeqRef.current;
    setLoading(true);
    setLoadError(null);
    const outcome = await loadFileVersions(romId, slot, filename);
    if (!mountedRef.current || seq !== readSeqRef.current) return;
    if (outcome.ok) {
      setVersions(outcome.versions);
    } else {
      setVersions(null);
      setLoadError(outcome.error);
    }
    setLoading(false);
  }, [romId, slot, filename]);

  const handleToggle = () => {
    const willExpand = !expanded;
    setOpen(willExpand);
    if (willExpand && versions === null && loadError === null && !isOffline) {
      detach(fetchVersions());
    }
  };

  useEffect(() => {
    const onDataChanged = ({ detail }: WindowEventMap["romm_data_changed"]) => {
      if (detail.type !== "save_sync") return;
      if (detail.rom_id !== undefined && detail.rom_id !== romId) return;
      setVersions(null);
      setLoadError(null);
      if (expandedRef.current && !isOffline) {
        detach(fetchVersions());
      }
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => globalThis.removeEventListener("romm_data_changed", onDataChanged);
  }, [romId, isOffline, fetchVersions]);

  const handleRestore = async (version: SaveVersionEntry) => {
    setRestoringVersionId(version.id);
    try {
      if (await executeVersionRestore(romId, slot, version, desktopSlotConflictDialog(ask))) {
        if (!mountedRef.current) return;
        setVersions(null);
        setOpen(false);
        onRestored();
      }
    } finally {
      if (mountedRef.current) setRestoringVersionId(null);
    }
  };

  const renderBody = () => {
    if (isOffline) {
      return (
        <div style={{ color: MUTED_COLOR, fontStyle: "italic", fontSize: "11px" }}>Offline — versions unavailable</div>
      );
    }
    if (loading && versions === null) {
      return (
        <div style={{ display: "flex", alignItems: "center", gap: "6px", color: MUTED_COLOR, fontSize: "11px" }}>
          <SvgThrobber style={{ width: "12px", height: "12px" }} />
          <span>Loading versions…</span>
        </div>
      );
    }
    if (loadError !== null) {
      return (
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          <span style={{ fontSize: "11px", color: "#ff6b6b" }}>{loadError}</span>
          <button type="button" style={SMALL_BUTTON_STYLE} onClick={() => detach(fetchVersions())}>
            Retry
          </button>
        </div>
      );
    }
    if (versions !== null && versions.length === 0) {
      return (
        <div style={{ color: MUTED_COLOR, fontStyle: "italic", fontSize: "11px" }}>No older versions available</div>
      );
    }
    if (versions !== null && versions.length > 0) {
      return (
        <div>
          <div
            style={{
              ...TABLE_GRID_STYLE,
              fontSize: "10px",
              fontWeight: 600,
              color: "#6e7f91",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              paddingBottom: "6px",
              borderBottom: "1px solid rgba(255, 255, 255, 0.06)",
            }}
          >
            <span>VERSION</span>
            <span>SIZE</span>
            <span>LAST UPDATED</span>
            <span style={{ textAlign: "right" }}>ACTION</span>
          </div>
          {versions.map((v) => {
            const lastSyncer = pickLastSyncer(v.device_syncs);
            const attr = formatAttributionSegment(v.uploaded_by_us, lastSyncer?.device_name);
            const isThisRestoring = restoringVersionId === v.id;
            const actionDisabled = restoringVersionId !== null;

            return (
              <div
                key={`ver-row-${v.id}`}
                style={{
                  ...TABLE_GRID_STYLE,
                  fontSize: "12px",
                  padding: "6px 0",
                  borderBottom: "1px solid rgba(255, 255, 255, 0.04)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{ color: "#dfe3e6", fontWeight: 500 }}
                  >{`#${v.id}${v.emulator ? ` · ${v.emulator}` : ""}`}</div>
                  <div
                    style={{
                      color: MUTED_COLOR,
                      fontFamily: "monospace",
                      fontSize: "11px",
                      wordBreak: "break-all",
                    }}
                  >
                    {v.file_name}
                  </div>
                </div>
                <span style={{ color: MUTED_COLOR }}>
                  {v.file_size_bytes != null ? formatBytes(v.file_size_bytes) : "—"}
                </span>
                <span style={{ color: MUTED_COLOR }}>
                  {formatTimestamp(v.updated_at)}
                  {renderAttributionWithCheckmark(attr)}
                </span>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "6px" }}>
                  <button
                    type="button"
                    style={{
                      ...SMALL_BUTTON_STYLE,
                      opacity: actionDisabled ? 0.5 : 1,
                      cursor: actionDisabled ? "not-allowed" : "pointer",
                    }}
                    disabled={actionDisabled}
                    onClick={() => detach(handleRestore(v))}
                  >
                    {isThisRestoring && <SvgThrobber style={{ width: "10px", height: "10px" }} />}
                    <span>{isThisRestoring ? "Restoring..." : "Restore"}</span>
                  </button>
                  <button
                    type="button"
                    style={{
                      ...SMALL_BUTTON_STYLE,
                      opacity: actionDisabled ? 0.5 : 1,
                      cursor: actionDisabled ? "not-allowed" : "pointer",
                    }}
                    disabled={actionDisabled}
                    onClick={() => onCopySave(v.id, slot)}
                  >
                    Copy to slot...
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      );
    }
    return null;
  };

  return (
    <div style={{ marginTop: "10px" }}>
      <button
        type="button"
        style={{
          background: "transparent",
          border: "none",
          color: "#dfe3e6",
          cursor: "pointer",
          fontWeight: 600,
          fontSize: "12px",
          padding: 0,
          display: "inline-flex",
          alignItems: "center",
          gap: "4px",
        }}
        onClick={handleToggle}
      >
        <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
        <span>{expanded && versions !== null ? `Previous Versions (${versions.length})` : "Previous Versions"}</span>
      </button>

      {expanded && (
        <div
          style={{
            marginTop: "8px",
            backgroundColor: "rgba(0, 0, 0, 0.2)",
            borderRadius: "3px",
            padding: "8px 12px",
          }}
        >
          {renderBody()}
        </div>
      )}
    </div>
  );
};
