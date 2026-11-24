import type { CSSProperties, FC } from "react";
import { logError } from "../../api/backend";
import { detach } from "../../utils/detach";
import { formatBytes, formatTimestamp } from "../../utils/formatters";
import { MUTED_COLOR, attributionParts, pickLastSyncer, type AttributionParts } from "../../utils/saveHelpers";
import { NO_OLDER_VERSIONS_TEXT, VERSIONS_OFFLINE_TEXT } from "../../utils/saveSlotWording";
import { useFileVersionHistory, type FileVersionHistoryTarget } from "../../utils/useFileVersionHistory";
import { SvgThrobber } from "./SvgThrobber";
import type { AskDialog } from "./dialogs/askDialog";
import { desktopSlotConflictDialog } from "./dialogs/desktopDialogs";
import { BODY_TEXT_COLOR, DANGER_TEXT_RED, SUBTLE_LABEL_COLOR, SYNCED_GREEN, smallActionButtonStyle } from "./styles";

export const DesktopCopyToSlotButton: FC<{
  style: CSSProperties;
  disabled: boolean;
  onClick: () => void;
}> = ({ style, disabled, onClick }) => (
  <button type="button" style={style} disabled={disabled} onClick={onClick}>
    Copy to slot...
  </button>
);

interface DesktopVersionHistoryProps extends FileVersionHistoryTarget {
  ask: AskDialog;
  onCopySave: (saveId: number, sourceSlot: string) => void;
}

const TABLE_GRID_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(140px, 1.4fr) 68px minmax(140px, 1fr) auto",
  gap: "8px",
  alignItems: "center",
};

/** An attribution segment after the time it follows, its ✓ in green. */
export const DesktopAttribution: FC<{ parts: AttributionParts | null }> = ({ parts }) => {
  if (parts === null) return null;
  const text = parts.text ?? "";
  if (!parts.check) return <>{` · ${text}`}</>;
  return (
    <>
      {text ? ` · ${text} ` : " · "}
      <span style={{ color: SYNCED_GREEN }}>✓</span>
    </>
  );
};

export const DesktopVersionHistory: FC<DesktopVersionHistoryProps> = (props) => {
  const { slot, ask, onCopySave, isOffline } = props;
  const { expanded, versions, loading, restoringVersionId, loadError, handleToggle, fetchVersions, handleRestore } =
    useFileVersionHistory(props, desktopSlotConflictDialog(ask));

  const renderBody = () => {
    if (isOffline) {
      return <div style={{ color: MUTED_COLOR, fontStyle: "italic", fontSize: "11px" }}>{VERSIONS_OFFLINE_TEXT}</div>;
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
          <span style={{ fontSize: "11px", color: DANGER_TEXT_RED }}>{loadError}</span>
          <button type="button" style={smallActionButtonStyle()} onClick={() => detach(fetchVersions())}>
            Retry
          </button>
        </div>
      );
    }
    if (versions !== null && versions.length === 0) {
      return <div style={{ color: MUTED_COLOR, fontStyle: "italic", fontSize: "11px" }}>{NO_OLDER_VERSIONS_TEXT}</div>;
    }
    if (versions !== null && versions.length > 0) {
      return (
        <div>
          <div
            style={{
              ...TABLE_GRID_STYLE,
              fontSize: "10px",
              fontWeight: 600,
              color: SUBTLE_LABEL_COLOR,
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
            const attr = attributionParts(v.uploaded_by_us, lastSyncer?.device_name);
            const isThisRestoring = restoringVersionId === v.id;
            const actionDisabled = restoringVersionId !== null;
            const versionHeading = v.emulator ? `#${v.id} · ${v.emulator}` : `#${v.id}`;
            const actionStyle = smallActionButtonStyle(actionDisabled);

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
                  <div style={{ color: BODY_TEXT_COLOR, fontWeight: 500 }}>{versionHeading}</div>
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
                  <DesktopAttribution parts={attr} />
                </span>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "6px" }}>
                  <button
                    type="button"
                    style={actionStyle}
                    disabled={actionDisabled}
                    onClick={() => {
                      handleRestore(v).catch((e) =>
                        logError(`DesktopVersionHistory: restoring version ${v.id} in slot '${slot}' threw: ${e}`),
                      );
                    }}
                  >
                    {isThisRestoring ? <SvgThrobber style={{ width: "10px", height: "10px" }} /> : null}
                    <span>{isThisRestoring ? "Restoring..." : "Restore"}</span>
                  </button>
                  <DesktopCopyToSlotButton
                    style={actionStyle}
                    disabled={actionDisabled}
                    onClick={() => onCopySave(v.id, slot)}
                  />
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
          color: BODY_TEXT_COLOR,
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

      {expanded ? (
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
      ) : null}
    </div>
  );
};
