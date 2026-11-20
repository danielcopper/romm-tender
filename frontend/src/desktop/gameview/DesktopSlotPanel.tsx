/**
 * Collapsible slot accordion panel and tracked-file row renderer for the
 * Desktop Saves card. Handles active slot file details, multi-file note,
 * inactive slot lazy-loading, Activate Slot / Delete Slot busy states, and
 * delegates version history to DesktopVersionHistory.
 */

import type { CSSProperties, FC, ReactNode } from "react";
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
import { executeSlotDelete } from "../../utils/saveSlotFlow";
import { LEGACY_SLOT_NOTE, multiFileSaveHeading, multiFileSaveNote } from "../../utils/saveSlotWording";
import { NO_SAVE_ON_DEVICE } from "../../utils/saveSyncStatement";
import { useSlotPanelState } from "../../utils/useSlotPanelState";
import { DesktopVersionHistory } from "./DesktopVersionHistory";
import { SvgThrobber } from "./SvgThrobber";
import type { AskDialog } from "./dialogs/askDialog";
import { desktopDeleteSlotDialog } from "./dialogs/desktopDialogs";
import {
  ACTIVE_PILL_BADGE_STYLE,
  ATTENTION_AMBER,
  BODY_TEXT_COLOR,
  BUTTON_STYLE,
  CONFLICT_RED,
  MUTED_PILL_BADGE_STYLE,
  SUBTLE_LABEL_COLOR,
  SYNCED_GREEN,
  actionButtonStyle,
  confirmActionButtonStyle,
  smallActionButtonStyle,
} from "./styles";

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
  color: SUBTLE_LABEL_COLOR,
  width: "90px",
  flexShrink: 0,
};

function renderInfoRow(label: string, value: ReactNode, valueColor = BODY_TEXT_COLOR) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: `${INFO_LABEL_STYLE.width} minmax(0, 1fr)`, gap: "12px" }}>
      <span style={{ color: INFO_LABEL_STYLE.color }}>{label}</span>
      <div style={{ color: valueColor, minWidth: 0 }}>{value}</div>
    </div>
  );
}

function renderLastSyncedNode(value: string) {
  if (value === "Never") {
    return <span style={{ color: ATTENTION_AMBER }}>{value}</span>;
  }
  const checkIdx = value.indexOf(" ✓");
  if (checkIdx !== -1) {
    const head = value.slice(0, checkIdx);
    const tail = value.slice(checkIdx + 2);
    return (
      <span>
        {head} <span style={{ color: SYNCED_GREEN }}>✓</span>
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
          {sizeBytes != null ? (
            <span style={{ color: MUTED_COLOR, fontSize: "12px" }}>{formatBytes(sizeBytes)}</span>
          ) : null}
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

      {conflictActive ? (
        <div style={{ color: CONFLICT_RED, fontSize: "12px", fontWeight: 600, marginBottom: "8px" }}>
          Conflict detected on this file
        </div>
      ) : null}

      <div style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "12px" }}>
        {renderInfoRow("Last synced:", renderLastSyncedNode(lastSyncedValue))}
        {checkedHint ? renderInfoRow("Checked:", checkedHint, MUTED_COLOR) : null}
        {file.server_updated_at ? renderInfoRow("Last updated:", formatTimestamp(file.server_updated_at)) : null}
        {file.server_save_id != null
          ? renderInfoRow(
              "Server save:",
              <div>
                <div>{formatServerSaveSummary(file)}</div>
                {file.server_file_name ? (
                  <div style={{ color: MUTED_COLOR, fontFamily: "monospace", fontSize: "11px" }}>
                    {file.server_file_name}
                  </div>
                ) : null}
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
      <span style={{ fontSize: "14px", fontWeight: 600, color: isActive ? "#ffffff" : BODY_TEXT_COLOR }}>
        {displaySlot(slotName)}
      </span>
      {isActive ? <span style={ACTIVE_PILL_BADGE_STYLE}>ACTIVE</span> : null}
      <span style={MUTED_PILL_BADGE_STYLE}>{slotSource === "local" ? "LOCAL" : "SERVER"}</span>
    </div>

    <div style={{ display: "flex", alignItems: "center", gap: "16px", fontSize: "12px", flexShrink: 0 }}>
      {isActive && syncSummaryText ? (
        <span style={{ color: syncSummaryColor, fontWeight: 500 }}>{syncSummaryText}</span>
      ) : null}
      <span style={{ color: MUTED_COLOR }}>{`${fileCount} save${fileCount === 1 ? "" : "s"}`}</span>
    </div>
  </button>
);

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
}) => {
  const renderFilesSection = () => {
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
              {file.server_save_id != null ? (
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
              ) : null}
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

        {saveStatus.multi_file ? (
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
        ) : null}
      </>
    );
  };

  return (
    <div>
      {saveSyncEnabled && slotConfirmed === false ? (
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
              <span style={{ fontSize: "12px", color: BODY_TEXT_COLOR }}>
                This slot is active but not confirmed for save sync.
              </span>
              <button
                type="button"
                style={confirmActionButtonStyle(isOffline || confirmingSlot)}
                disabled={isOffline || confirmingSlot}
                onClick={() => onConfirmSlot(slotName)}
              >
                {confirmingSlot ? "Confirming..." : "Confirm Slot"}
              </button>
            </>
          ) : (
            <span style={{ fontSize: "12px", color: BODY_TEXT_COLOR }}>
              This slot is active and will be used when a save is present.
            </span>
          )}
        </div>
      ) : null}

      {renderFilesSection()}
    </div>
  );
};

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
            <div style={{ color: BODY_TEXT_COLOR, fontWeight: 500, fontSize: "13px" }}>{f.filename}</div>
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
  const slotName = slot.slot;
  const isLegacy = slotName === "";
  const {
    expanded,
    slotFiles,
    loadingSlot,
    switching,
    switchError,
    deleting,
    isMounted,
    handleToggle,
    handleActivate,
    handleDelete,
  } = useSlotPanelState({
    romId,
    slotName,
    isActive,
    defaultExpanded,
    refreshOnSaveSync: true,
    logPrefix: "DesktopSlotPanel",
    onSlotActivated: (newStatus) => onSlotActivated(slotName, newStatus),
  });

  const startDeleteFlow = () =>
    handleDelete(async (info) => {
      const confirmed = await desktopDeleteSlotDialog(ask)(info);
      if (!confirmed || !isMounted()) return;
      if (await executeSlotDelete(romId, slotName)) {
        onSlotDeleted();
      }
    });

  const conflicts: SyncConflict[] = isActive ? (saveStatus?.conflicts ?? []) : [];
  const { syncSummaryText, syncSummaryColor } = computeSyncSummary(isActive, saveStatus, conflicts, noSaveLine);
  // Synthesized active-slot placeholder carries count=0 before getSaveSlots settles.
  const fileCount = isActive
    ? slot.count > 0
      ? slot.count
      : (saveStatus?.files.length ?? 0)
    : (slotFiles?.length ?? slot.count);
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

      {isLegacy ? (
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
      ) : null}

      {expanded ? (
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

              {!isLegacy ? (
                <div>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <button
                      type="button"
                      style={actionButtonStyle(activateDisabled)}
                      disabled={activateDisabled}
                      onClick={() => detach(handleActivate())}
                    >
                      {switching ? <SvgThrobber style={{ width: "12px", height: "12px" }} /> : null}
                      <span>{switching ? "Switching..." : "Activate Slot"}</span>
                    </button>
                    <button
                      type="button"
                      style={actionButtonStyle(deleteDisabled, true)}
                      disabled={deleteDisabled}
                      onClick={() => detach(startDeleteFlow())}
                    >
                      {deleting ? <SvgThrobber style={{ width: "12px", height: "12px" }} /> : null}
                      <span>{deleting ? "Deleting..." : "Delete Slot"}</span>
                    </button>
                  </div>
                  {switchError ? (
                    <div style={{ color: CONFLICT_RED, fontSize: "11px", marginTop: "6px" }}>{switchError}</div>
                  ) : null}
                </div>
              ) : null}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
};
