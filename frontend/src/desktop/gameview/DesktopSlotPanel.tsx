/**
 * Collapsible slot accordion panel and tracked-file row renderer for the
 * Desktop Saves card. Handles active slot file details, multi-file note,
 * inactive slot lazy-loading, Activate Slot / Delete Slot busy states, and
 * delegates version history to DesktopVersionHistory.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties, type FC, type ReactNode } from "react";
import { getSlotSaves, logWarn } from "../../api/backend";
import type { SaveFileStatus, SaveSlotSummary, SaveStatus, SlotSaveFile, SyncConflict } from "../../types";
import { detach } from "../../utils/detach";
import { formatBytes, formatTimestamp } from "../../utils/formatters";
import {
  MUTED_COLOR,
  buildLastSyncedValue,
  computeSyncSummary,
  displaySlot,
  formatSaveTimeAgo,
  statusLabel,
} from "../../utils/saveHelpers";
import { executeSlotDelete, executeSlotSwitch, loadSlotDeleteInfo } from "../../utils/saveSlotFlow";
import { LEGACY_SLOT_NOTE, multiFileSaveHeading, multiFileSaveNote } from "../../utils/saveSlotWording";
import { NO_SAVE_ON_DEVICE } from "../../utils/saveSyncStatement";
import { showToast } from "../../utils/toast";
import { DesktopVersionHistory } from "./DesktopVersionHistory";
import { SvgThrobber } from "./SvgThrobber";
import type { AskDialog } from "./dialogs/askDialog";
import { desktopDeleteSlotDialog } from "./dialogs/desktopDialogs";
import { BUTTON_STYLE } from "./styles";

interface DesktopSlotPanelProps {
  romId: number;
  slot: SaveSlotSummary;
  isActive: boolean;
  saveStatus: SaveStatus | null;
  noSaveLine: string;
  isOffline: boolean;
  defaultExpanded: boolean;
  slotConfirmed: boolean | null;
  saveSyncEnabled: boolean;
  confirmingSlot: boolean;
  ask: AskDialog;
  onConfirmSlot: (slotName: string) => void;
  onSlotActivated: (slotName: string, newStatus: SaveStatus) => Promise<void>;
  onSlotDeleted: () => void;
  onVersionRestored: () => void;
  onCopySave: (saveId: number, sourceSlot: string) => void;
}

const INFO_LABEL_STYLE: CSSProperties = {
  color: "#6e7f91",
  width: "90px",
  flexShrink: 0,
};

const INLINE_ACTION_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_STYLE,
  display: "inline-flex",
  alignItems: "center",
  gap: "6px",
};

const PILL_BADGE_BASE: CSSProperties = {
  fontSize: "10px",
  padding: "1px 6px",
  borderRadius: "2px",
  letterSpacing: "0.04em",
  textTransform: "uppercase",
};

function slotActionButtonStyle(disabled: boolean, danger = false): CSSProperties {
  return {
    ...INLINE_ACTION_BUTTON_STYLE,
    ...(danger ? { borderColor: "rgba(217, 65, 38, 0.4)", color: "#ff6b6b" } : {}),
    opacity: disabled ? 0.5 : 1,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}

function smallActionButtonStyle(disabled: boolean): CSSProperties {
  return {
    ...BUTTON_STYLE,
    padding: "2px 8px",
    fontSize: "11px",
    opacity: disabled ? 0.5 : 1,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}

function confirmSlotButtonStyle(disabled: boolean): CSSProperties {
  return {
    ...BUTTON_STYLE,
    backgroundColor: "rgba(26, 159, 255, 0.2)",
    borderColor: "rgba(26, 159, 255, 0.5)",
    color: "#1a9fff",
    opacity: disabled ? 0.5 : 1,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}

function renderInfoRow(label: string, value: ReactNode, valueColor = "#dfe3e6") {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
      <span style={INFO_LABEL_STYLE}>{label}</span>
      <div style={{ color: valueColor, flex: 1, minWidth: 0 }}>{value}</div>
    </div>
  );
}

function renderLastSyncedNode(value: string) {
  if (value === "Never") {
    return <span style={{ color: "#d4a72c" }}>{value}</span>;
  }
  const checkIdx = value.indexOf(" ✓");
  if (checkIdx !== -1) {
    const head = value.slice(0, checkIdx);
    const tail = value.slice(checkIdx + 2);
    return (
      <span>
        {head} <span style={{ color: "#5ba32b" }}>✓</span>
        {tail}
      </span>
    );
  }
  return value;
}

function formatServerSaveSummary(file: SaveFileStatus): string {
  const base = file.server_emulator ? `#${file.server_save_id} · ${file.server_emulator}` : `#${file.server_save_id}`;
  return file.server_size != null ? `${base} (${formatBytes(file.server_size)})` : base;
}

function formatInactiveFileDetails(f: SlotSaveFile): string {
  return [
    `#${f.id}`,
    f.size != null ? formatBytes(f.size) : null,
    f.updated_at ? `Updated ${formatSaveTimeAgo(f.updated_at)}` : null,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

/** Shared tracked-file block used both inside the active slot and for unslotted local files. */
export function renderDesktopSaveFileRow(
  file: SaveFileStatus,
  conflict: SyncConflict | undefined,
  lastSyncCheckAt: string | null,
  extraFooter?: ReactNode,
) {
  const { color: statusColor, label: statusLabelText } = statusLabel(file.status, file.last_sync_at);
  const isSynced = file.status === "synced" || file.status === "skip";
  const conflictActive = file.status === "conflict" || Boolean(conflict);
  const checkedHint = !isSynced && lastSyncCheckAt ? formatSaveTimeAgo(lastSyncCheckAt) : null;
  const lastSyncedValue = buildLastSyncedValue(file, isSynced);
  const sizeBytes = file.local_size ?? file.server_size;

  return (
    <div
      key={file.filename}
      style={{
        marginBottom: "14px",
        paddingBottom: "12px",
        borderBottom: "1px solid rgba(255, 255, 255, 0.06)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "10px",
          marginBottom: "8px",
        }}
      >
        <span style={{ fontWeight: 600, color: "#ffffff", fontSize: "14px", wordBreak: "break-word" }}>
          {file.filename}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexShrink: 0 }}>
          {sizeBytes != null && <span style={{ color: MUTED_COLOR, fontSize: "12px" }}>{formatBytes(sizeBytes)}</span>}
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "5px",
              fontSize: "11px",
              fontWeight: 600,
              color: statusColor,
            }}
          >
            <span
              style={{
                width: "6px",
                height: "6px",
                borderRadius: "50%",
                backgroundColor: statusColor,
                display: "inline-block",
              }}
            />
            {statusLabelText}
          </span>
        </div>
      </div>

      {conflictActive && (
        <div style={{ color: "#d94126", fontSize: "12px", fontWeight: 600, marginBottom: "8px" }}>
          Conflict detected on this file
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "12px" }}>
        {renderInfoRow("Last synced:", renderLastSyncedNode(lastSyncedValue))}
        {checkedHint ? renderInfoRow("Checked:", checkedHint, MUTED_COLOR) : null}
        {file.server_updated_at ? renderInfoRow("Last updated:", formatTimestamp(file.server_updated_at)) : null}
        {file.server_save_id != null
          ? renderInfoRow(
              "Server save:",
              <div>
                <div>{formatServerSaveSummary(file)}</div>
                {file.server_file_name && (
                  <div style={{ color: MUTED_COLOR, fontFamily: "monospace", fontSize: "11px" }}>
                    {file.server_file_name}
                  </div>
                )}
              </div>,
            )
          : null}
        {file.local_path
          ? renderInfoRow(
              "Local path:",
              <span style={{ fontFamily: "monospace", fontSize: "11px", wordBreak: "break-all" }}>
                {file.local_path}
              </span>,
              MUTED_COLOR,
            )
          : null}
      </div>

      {extraFooter}
    </div>
  );
}

const SlotPanelHeader: FC<{
  slotName: string;
  slotSource: SaveSlotSummary["source"];
  isActive: boolean;
  expanded: boolean;
  syncSummaryText: string | null;
  syncSummaryColor: string;
  fileCount: number;
  onToggle: () => void;
}> = ({ slotName, slotSource, isActive, expanded, syncSummaryText, syncSummaryColor, fileCount, onToggle }) => (
  <button
    type="button"
    aria-expanded={expanded}
    style={{
      width: "100%",
      border: "none",
      textAlign: "left",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "10px 14px",
      cursor: "pointer",
      userSelect: "none",
      backgroundColor: "transparent",
      font: "inherit",
    }}
    onClick={onToggle}
  >
    <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
      <span aria-hidden="true" style={{ fontSize: "12px", color: MUTED_COLOR, width: "12px" }}>
        {expanded ? "▾" : "▸"}
      </span>
      <span style={{ fontSize: "14px", fontWeight: 600, color: isActive ? "#ffffff" : "#dfe3e6" }}>
        {displaySlot(slotName)}
      </span>
      {isActive && (
        <span style={{ ...PILL_BADGE_BASE, fontWeight: 700, backgroundColor: "#5ba32b", color: "#ffffff" }}>
          ACTIVE
        </span>
      )}
      <span
        style={{
          ...PILL_BADGE_BASE,
          fontWeight: 600,
          backgroundColor: "rgba(255, 255, 255, 0.08)",
          color: MUTED_COLOR,
        }}
      >
        {slotSource === "local" ? "LOCAL" : "SERVER"}
      </span>
    </div>

    <div style={{ display: "flex", alignItems: "center", gap: "16px", fontSize: "12px", flexShrink: 0 }}>
      {isActive && syncSummaryText && (
        <span style={{ color: syncSummaryColor, fontWeight: 500 }}>{syncSummaryText}</span>
      )}
      <span style={{ color: MUTED_COLOR }}>{`${fileCount} save${fileCount === 1 ? "" : "s"}`}</span>
    </div>
  </button>
);

const ActiveSlotFilesSection: FC<{
  romId: number;
  slotName: string;
  saveStatus: SaveStatus | null;
  conflicts: SyncConflict[];
  isOffline: boolean;
  ask: AskDialog;
  onVersionRestored: () => void;
  onCopySave: (saveId: number, sourceSlot: string) => void;
}> = ({ romId, slotName, saveStatus, conflicts, isOffline, ask, onVersionRestored, onCopySave }) => {
  if (!saveStatus) return null;
  if (saveStatus.files.length === 0) {
    if (!saveStatus.server_query_failed) return null;
    return <div style={{ color: MUTED_COLOR, fontStyle: "italic", fontSize: "12px" }}>{NO_SAVE_ON_DEVICE}</div>;
  }

  return (
    <>
      {saveStatus.files.map((file) => {
        const conflict = conflicts.find((c) => c.filename === file.filename);
        const footer = saveStatus.multi_file ? null : (
          <>
            {file.server_save_id != null && (
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "8px" }}>
                <button
                  type="button"
                  style={{
                    ...BUTTON_STYLE,
                    opacity: isOffline ? 0.5 : 1,
                    cursor: isOffline ? "not-allowed" : "pointer",
                  }}
                  disabled={isOffline}
                  onClick={() => onCopySave(file.server_save_id!, slotName)}
                >
                  Copy to slot...
                </button>
              </div>
            )}
            <DesktopVersionHistory
              romId={romId}
              slot={slotName}
              filename={file.filename}
              isOffline={isOffline}
              ask={ask}
              onRestored={onVersionRestored}
              onCopySave={onCopySave}
            />
          </>
        );
        return renderDesktopSaveFileRow(file, conflict, saveStatus.last_sync_check_at, footer);
      })}

      {saveStatus.multi_file && (
        <div data-testid="desktop-multi-file-note" style={{ marginTop: "6px" }}>
          <div style={{ fontSize: "11px", color: MUTED_COLOR, fontWeight: 600 }}>
            {multiFileSaveHeading((saveStatus.component_files ?? []).length)}
          </div>
          {(saveStatus.component_files ?? []).map((fn) => (
            <div
              key={`comp-${fn}`}
              style={{
                fontSize: "11px",
                color: MUTED_COLOR,
                fontFamily: "monospace",
                wordBreak: "break-all",
                marginTop: "2px",
              }}
            >
              {fn}
            </div>
          ))}
          <div style={{ fontSize: "11px", color: MUTED_COLOR, fontStyle: "italic", marginTop: "4px" }}>
            {multiFileSaveNote((saveStatus.component_files ?? []).length)}
          </div>
        </div>
      )}
    </>
  );
};

const ActiveSlotBody: FC<{
  romId: number;
  slotName: string;
  saveStatus: SaveStatus | null;
  conflicts: SyncConflict[];
  isOffline: boolean;
  saveSyncEnabled: boolean;
  slotConfirmed: boolean | null;
  confirmingSlot: boolean;
  activeHasSaves: boolean;
  ask: AskDialog;
  onConfirmSlot: (slotName: string) => void;
  onVersionRestored: () => void;
  onCopySave: (saveId: number, sourceSlot: string) => void;
}> = ({
  romId,
  slotName,
  saveStatus,
  conflicts,
  isOffline,
  saveSyncEnabled,
  slotConfirmed,
  confirmingSlot,
  activeHasSaves,
  ask,
  onConfirmSlot,
  onVersionRestored,
  onCopySave,
}) => (
  <div>
    {saveSyncEnabled && slotConfirmed === false && (
      <div
        data-testid="active-slot-unconfirmed-box"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "12px",
          padding: "8px 12px",
          marginBottom: "12px",
          backgroundColor: "rgba(26, 159, 255, 0.1)",
          border: "1px solid rgba(26, 159, 255, 0.3)",
          borderRadius: "4px",
        }}
      >
        {activeHasSaves ? (
          <>
            <span style={{ fontSize: "12px", color: "#dfe3e6" }}>
              This slot is active but not confirmed for save sync.
            </span>
            <button
              type="button"
              style={confirmSlotButtonStyle(isOffline || confirmingSlot)}
              disabled={isOffline || confirmingSlot}
              onClick={() => onConfirmSlot(slotName)}
            >
              {confirmingSlot ? "Confirming..." : "Confirm Slot"}
            </button>
          </>
        ) : (
          <span style={{ fontSize: "12px", color: "#dfe3e6" }}>
            This slot is active and will be used when a save is present.
          </span>
        )}
      </div>
    )}

    <ActiveSlotFilesSection
      romId={romId}
      slotName={slotName}
      saveStatus={saveStatus}
      conflicts={conflicts}
      isOffline={isOffline}
      ask={ask}
      onVersionRestored={onVersionRestored}
      onCopySave={onCopySave}
    />
  </div>
);

const InactiveSlotFilesList: FC<{
  slotName: string;
  slotFiles: SlotSaveFile[] | null;
  loadingSlot: boolean;
  isOffline: boolean;
  onCopySave: (saveId: number, sourceSlot: string) => void;
}> = ({ slotName, slotFiles, loadingSlot, isOffline, onCopySave }) => {
  if (loadingSlot) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          color: MUTED_COLOR,
          fontSize: "12px",
          marginBottom: "10px",
        }}
      >
        <SvgThrobber style={{ width: "12px", height: "12px" }} />
        <span>Loading saves…</span>
      </div>
    );
  }
  if (slotFiles === null) return null;
  if (slotFiles.length === 0) {
    return (
      <div style={{ color: MUTED_COLOR, fontStyle: "italic", fontSize: "12px", marginBottom: "12px" }}>
        No saves in this slot
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "14px" }}>
      {slotFiles.map((f) => (
        <div
          key={`srv-${f.id}`}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "10px",
            padding: "4px 0",
            borderBottom: "1px solid rgba(255, 255, 255, 0.04)",
          }}
        >
          <div>
            <div style={{ color: "#dfe3e6", fontWeight: 500, fontSize: "13px" }}>{f.filename}</div>
            <div style={{ color: MUTED_COLOR, fontSize: "11px" }}>{formatInactiveFileDetails(f)}</div>
          </div>
          <button
            type="button"
            style={smallActionButtonStyle(isOffline)}
            disabled={isOffline}
            onClick={() => onCopySave(f.id, slotName)}
          >
            Copy to slot...
          </button>
        </div>
      ))}
    </div>
  );
};

export const DesktopSlotPanel: FC<DesktopSlotPanelProps> = ({
  romId,
  slot,
  isActive,
  saveStatus,
  noSaveLine,
  isOffline,
  defaultExpanded,
  slotConfirmed,
  saveSyncEnabled,
  confirmingSlot,
  ask,
  onConfirmSlot,
  onSlotActivated,
  onSlotDeleted,
  onVersionRestored,
  onCopySave,
}) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const expandedRef = useRef(defaultExpanded);
  const setOpen = (open: boolean) => {
    expandedRef.current = open;
    setExpanded(open);
  };

  const [slotFiles, setSlotFiles] = useState<SlotSaveFile[] | null>(null);
  const [loadingSlot, setLoadingSlot] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const switchErrorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const slotSavesSeqRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (switchErrorTimerRef.current) clearTimeout(switchErrorTimerRef.current);
    };
  }, []);

  const slotName = slot.slot;
  const isLegacy = slotName === "";

  const loadInactiveSlotSaves = useCallback(async () => {
    const seq = ++slotSavesSeqRef.current;
    setLoadingSlot(true);
    try {
      const result = await getSlotSaves(romId, slotName);
      if (!mountedRef.current || seq !== slotSavesSeqRef.current) return;
      setSlotFiles(result.success ? result.saves : []);
    } catch (e) {
      logWarn(`DesktopSlotPanel: failed to load slot saves for ${slotName}: ${e}`);
      if (!mountedRef.current || seq !== slotSavesSeqRef.current) return;
      setSlotFiles([]);
    } finally {
      if (mountedRef.current && seq === slotSavesSeqRef.current) setLoadingSlot(false);
    }
  }, [romId, slotName]);

  useEffect(() => {
    if (isActive) return;
    const onDataChanged = ({ detail }: WindowEventMap["romm_data_changed"]) => {
      if (detail.type !== "save_sync") return;
      if (detail.rom_id !== undefined && detail.rom_id !== romId) return;
      setSlotFiles(null);
      if (expandedRef.current) {
        detach(loadInactiveSlotSaves());
      }
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => globalThis.removeEventListener("romm_data_changed", onDataChanged);
  }, [isActive, romId, loadInactiveSlotSaves]);

  const handleToggle = () => {
    const willExpand = !expanded;
    setOpen(willExpand);
    if (willExpand && !isActive && slotFiles === null) {
      detach(loadInactiveSlotSaves());
    }
  };

  const handleActivate = async () => {
    setSwitching(true);
    setSwitchError(null);
    try {
      const outcome = await executeSlotSwitch(romId, slotName, "activate");
      if (!mountedRef.current) return;
      if (outcome.ok) {
        await onSlotActivated(slotName, outcome.saveStatus);
        return;
      }
      if (outcome.partial) {
        showToast(outcome.message);
        return;
      }
      setSwitchError(outcome.message);
      if (switchErrorTimerRef.current) clearTimeout(switchErrorTimerRef.current);
      switchErrorTimerRef.current = setTimeout(() => {
        if (mountedRef.current) setSwitchError(null);
      }, 5000);
    } finally {
      if (mountedRef.current) setSwitching(false);
    }
  };

  const handleDelete = async () => {
    const isMounted = () => mountedRef.current;
    setDeleting(true);
    try {
      const info = await loadSlotDeleteInfo(romId, slotName);
      if (!info || !isMounted()) return;
      const confirmed = await desktopDeleteSlotDialog(ask)(info);
      if (!confirmed || !isMounted()) return;
      if (await executeSlotDelete(romId, slotName)) {
        onSlotDeleted();
      }
    } finally {
      if (isMounted()) setDeleting(false);
    }
  };

  const conflicts: SyncConflict[] = isActive ? (saveStatus?.conflicts ?? []) : [];
  const { syncSummaryText, syncSummaryColor } = computeSyncSummary(isActive, saveStatus, conflicts, noSaveLine);
  const fileCount = isActive ? slot.count || (saveStatus?.files.length ?? 0) : (slotFiles?.length ?? slot.count);
  const activeHasSaves = (saveStatus?.files.length ?? 0) > 0 || slot.count > 0;
  const activateDisabled = switching || deleting || isOffline;
  const deleteDisabled = deleting || switching;

  return (
    <div
      data-testid={`desktop-slot-panel-${slotName || "legacy"}`}
      style={{
        backgroundColor: isActive ? "rgba(0, 0, 0, 0.22)" : "rgba(0, 0, 0, 0.14)",
        borderRadius: "3px",
        border: isActive ? "1px solid rgba(255, 255, 255, 0.12)" : "1px solid rgba(255, 255, 255, 0.05)",
        overflow: "hidden",
      }}
    >
      <SlotPanelHeader
        slotName={slotName}
        slotSource={slot.source}
        isActive={isActive}
        expanded={expanded}
        syncSummaryText={syncSummaryText}
        syncSummaryColor={syncSummaryColor}
        fileCount={fileCount}
        onToggle={handleToggle}
      />

      {isLegacy && (
        <div
          style={{
            padding: "6px 14px 10px 34px",
            color: MUTED_COLOR,
            fontStyle: "italic",
            fontSize: "11px",
          }}
        >
          {LEGACY_SLOT_NOTE}
        </div>
      )}

      {expanded && (
        <div
          style={{
            padding: "12px 16px 16px 34px",
            borderTop: "1px solid rgba(255, 255, 255, 0.06)",
            fontSize: "13px",
          }}
        >
          {isActive ? (
            <ActiveSlotBody
              romId={romId}
              slotName={slotName}
              saveStatus={saveStatus}
              conflicts={conflicts}
              isOffline={isOffline}
              saveSyncEnabled={saveSyncEnabled}
              slotConfirmed={slotConfirmed}
              confirmingSlot={confirmingSlot}
              activeHasSaves={activeHasSaves}
              ask={ask}
              onConfirmSlot={onConfirmSlot}
              onVersionRestored={onVersionRestored}
              onCopySave={onCopySave}
            />
          ) : (
            <div>
              <InactiveSlotFilesList
                slotName={slotName}
                slotFiles={slotFiles}
                loadingSlot={loadingSlot}
                isOffline={isOffline}
                onCopySave={onCopySave}
              />

              {!isLegacy && (
                <div>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <button
                      type="button"
                      style={slotActionButtonStyle(activateDisabled)}
                      disabled={activateDisabled}
                      onClick={() => detach(handleActivate())}
                    >
                      {switching && <SvgThrobber style={{ width: "12px", height: "12px" }} />}
                      <span>{switching ? "Switching..." : "Activate Slot"}</span>
                    </button>
                    <button
                      type="button"
                      style={slotActionButtonStyle(deleteDisabled, true)}
                      disabled={deleteDisabled}
                      onClick={() => detach(handleDelete())}
                    >
                      {deleting && <SvgThrobber style={{ width: "12px", height: "12px" }} />}
                      <span>{deleting ? "Deleting..." : "Delete Slot"}</span>
                    </button>
                  </div>
                  {switchError && (
                    <div style={{ color: "#d94126", fontSize: "11px", marginTop: "6px" }}>{switchError}</div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
