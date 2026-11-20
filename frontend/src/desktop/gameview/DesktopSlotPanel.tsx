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
import { useSlotPanelState, type SlotPanelState } from "../../utils/useSlotPanelState";
import { DesktopCopyToSlotButton, DesktopVersionHistory } from "./DesktopVersionHistory";
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

interface DesktopSlotSharedProps {
  romId: number;
  saveStatus: SaveStatus | null;
  isOffline: boolean;
  slotConfirmed: boolean | null;
  confirmingSlot: boolean;
  ask: AskDialog;
  onConfirmSlot: (slotName: string) => void;
  onVersionRestored: () => void;
  onCopySave: (saveId: number, sourceSlot: string) => void;
}

interface DesktopSlotPanelProps extends DesktopSlotSharedProps {
  slot: SaveSlotSummary;
  isActive: boolean;
  noSaveLine: string;
  defaultExpanded: boolean;
  onSlotActivated: (slotName: string, newStatus: SaveStatus) => Promise<void>;
  onSlotDeleted: () => void;
}

const INFO_ROW_GRID_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "90px minmax(0, 1fr)",
  gap: "12px",
};

const DesktopInfoRow: FC<{ label: string; color?: string; children: ReactNode }> = ({
  label,
  color = BODY_TEXT_COLOR,
  children,
}) => (
  <div style={INFO_ROW_GRID_STYLE}>
    <span style={{ color: SUBTLE_LABEL_COLOR }}>{label}</span>
    <div style={{ color, minWidth: 0 }}>{children}</div>
  </div>
);

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
        <DesktopInfoRow label="Last synced:">{renderLastSyncedNode(lastSyncedValue)}</DesktopInfoRow>
        {checkedHint ? (
          <DesktopInfoRow label="Checked:" color={MUTED_COLOR}>
            {checkedHint}
          </DesktopInfoRow>
        ) : null}
        {file.server_updated_at ? (
          <DesktopInfoRow label="Last updated:">{formatTimestamp(file.server_updated_at)}</DesktopInfoRow>
        ) : null}
        {file.server_save_id != null ? (
          <DesktopInfoRow label="Server save:">
            <div>
              <div>{formatServerSaveSummary(file)}</div>
              {file.server_file_name ? (
                <div style={{ color: MUTED_COLOR, fontFamily: "monospace", fontSize: "11px" }}>
                  {file.server_file_name}
                </div>
              ) : null}
            </div>
          </DesktopInfoRow>
        ) : null}
        {file.local_path ? (
          <DesktopInfoRow label="Local path:" color={MUTED_COLOR}>
            <span style={{ fontFamily: "monospace", fontSize: "11px", wordBreak: "break-all" }}>{file.local_path}</span>
          </DesktopInfoRow>
        ) : null}
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

interface ActiveSlotBodyProps extends DesktopSlotSharedProps {
  slotName: string;
  conflicts: SyncConflict[];
  activeHasSaves: boolean;
}

const ActiveSlotBody: FC<ActiveSlotBodyProps> = ({
  romId,
  slotName,
  saveStatus,
  conflicts,
  isOffline,
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
                  <DesktopCopyToSlotButton
                    style={{
                      ...BUTTON_STYLE,
                      opacity: isOffline ? 0.5 : 1,
                      cursor: isOffline ? "not-allowed" : "pointer",
                    }}
                    disabled={isOffline}
                    onClick={() => onCopySave(file.server_save_id!, slotName)}
                  />
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
      {slotConfirmed === false ? (
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

function resolveSlotFileCount(
  isActive: boolean,
  slotCount: number,
  saveStatus: SaveStatus | null,
  slotFiles: SlotSaveFile[] | null,
): number {
  // Synthesized active-slot placeholder carries count=0 before getSaveSlots settles.
  if (!isActive) return slotFiles?.length ?? slotCount;
  return slotCount > 0 ? slotCount : (saveStatus?.files.length ?? 0);
}

function hasActiveSlotSaves(saveStatus: SaveStatus | null, slotCount: number): boolean {
  return (saveStatus?.files.length ?? 0) > 0 || slotCount > 0;
}

interface InactiveSlotSectionProps {
  slotName: string;
  isLegacy: boolean;
  isOffline: boolean;
  panel: SlotPanelState;
  onDelete: () => Promise<void>;
  onCopySave: (saveId: number, sourceSlot: string) => void;
}

const InactiveSlotSection: FC<InactiveSlotSectionProps> = ({
  slotName,
  isLegacy,
  isOffline,
  panel,
  onDelete,
  onCopySave,
}) => {
  const activateDisabled = panel.switching || panel.deleting || isOffline;
  const deleteDisabled = panel.deleting || panel.switching;
  return (
    <div>
      <InactiveSlotFilesList
        slotName={slotName}
        slotFiles={panel.slotFiles}
        loadingSlot={panel.loadingSlot}
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
              onClick={() => detach(panel.handleActivate())}
            >
              {panel.switching ? <SvgThrobber style={{ width: "12px", height: "12px" }} /> : null}
              <span>{panel.switching ? "Switching..." : "Activate Slot"}</span>
            </button>
            <button
              type="button"
              style={actionButtonStyle(deleteDisabled, true)}
              disabled={deleteDisabled}
              onClick={() => detach(onDelete())}
            >
              {panel.deleting ? <SvgThrobber style={{ width: "12px", height: "12px" }} /> : null}
              <span>{panel.deleting ? "Deleting..." : "Delete Slot"}</span>
            </button>
          </div>
          {panel.switchError ? (
            <div style={{ color: CONFLICT_RED, fontSize: "11px", marginTop: "6px" }}>{panel.switchError}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

const SlotExpandedFrame: FC<{
  expanded: boolean;
  isActive: boolean;
  activeProps: ActiveSlotBodyProps;
  inactiveProps: InactiveSlotSectionProps;
}> = ({ expanded, isActive, activeProps, inactiveProps }) => {
  if (!expanded) return null;
  return (
    <div
      style={{
        padding: "12px 16px 16px 34px",
        borderTop: "1px solid rgba(255, 255, 255, 0.06)",
        fontSize: "13px",
      }}
    >
      {isActive ? <ActiveSlotBody {...activeProps} /> : <InactiveSlotSection {...inactiveProps} />}
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
  const panel = useSlotPanelState({
    romId,
    slotName,
    isActive,
    defaultExpanded,
    refreshOnSaveSync: true,
    logPrefix: "DesktopSlotPanel",
    onSlotActivated: (newStatus) => onSlotActivated(slotName, newStatus),
  });

  const startDeleteFlow = () =>
    panel.handleDelete(async (info) => {
      const confirmed = await desktopDeleteSlotDialog(ask)(info);
      if (!confirmed || !panel.isMounted()) return;
      if (await executeSlotDelete(romId, slotName)) {
        onSlotDeleted();
      }
    });

  const conflicts: SyncConflict[] = isActive ? (saveStatus?.conflicts ?? []) : [];
  const { syncSummaryText, syncSummaryColor } = computeSyncSummary(isActive, saveStatus, conflicts, noSaveLine);
  const fileCount = resolveSlotFileCount(isActive, slot.count, saveStatus, panel.slotFiles);
  const activeHasSaves = hasActiveSlotSaves(saveStatus, slot.count);

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
        expanded={panel.expanded}
        syncSummaryText={syncSummaryText}
        syncSummaryColor={syncSummaryColor}
        fileCount={fileCount}
        onToggle={panel.handleToggle}
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

      <SlotExpandedFrame
        expanded={panel.expanded}
        isActive={isActive}
        activeProps={{
          romId,
          slotName,
          saveStatus,
          conflicts,
          isOffline,
          slotConfirmed,
          confirmingSlot,
          activeHasSaves,
          ask,
          onConfirmSlot,
          onVersionRestored,
          onCopySave,
        }}
        inactiveProps={{
          slotName,
          isLegacy,
          isOffline,
          panel,
          onDelete: startDeleteFlow,
          onCopySave,
        }}
      />
    </div>
  );
};
