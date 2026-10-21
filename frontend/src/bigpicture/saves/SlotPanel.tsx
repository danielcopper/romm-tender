/**
 * A single collapsible save slot in the SavesTab list. Owns expand/collapse,
 * lazy-loads slot saves for inactive slots, and drives the activate / delete
 * flows; the parent SavesTab handles slot creation and the offline banner.
 */

import { useState, useRef, useEffect, FC, type ReactElement } from "react";
import { ConfirmModal, DialogButton, showModal } from "@decky/ui";
import { getSlotSaves, debugLog, logError } from "../../api/backend";
import type { SaveStatus, SyncConflict, SaveSlotSummary, SlotSaveFile } from "../../types";
import { scrollFocusedToCenter } from "../../utils/scrollHelpers";
import { MUTED_COLOR, computeSyncSummary, displaySlot } from "../../utils/saveHelpers";
import { formatSlotDeleteDescription } from "../../utils/saveSlotWording";
import { executeSlotDelete, executeSlotSwitch, loadSlotDeleteInfo } from "../../utils/saveSlotFlow";
import { renderSaveFileRow } from "./SaveFileRow";
import { InactiveSlotBody } from "./InactiveSlotBody";
import { VersionHistoryPanel } from "./VersionHistoryPanel";
import { renderCopyToSlotButton, type CopyToSlotHandler } from "./CopyToSlotButton";
import { detach } from "../../utils/detach";

/**
 * Multi-file note (#908 interim guard) — replaces the Previous-Versions /
 * rollback UI for a save that spans more than one file. Lists the N component
 * filenames and explains that per-version rollback isn't available yet.
 */
function renderMultiFileNote(componentFiles: string[]): ReactElement {
  const n = componentFiles.length;
  return (
    <div key="multi-file-note" style={{ marginTop: "6px", marginLeft: "8px" }}>
      <div style={{ fontSize: "11px", color: "#8f98a0", fontWeight: 600 }}>{`Files in this save (${n})`}</div>
      {componentFiles.map((fn) => (
        <div
          key={`comp-${fn}`}
          style={{
            fontSize: "11px",
            color: "#8f98a0",
            fontFamily: "monospace",
            wordBreak: "break-all" as const,
            marginTop: "1px",
          }}
        >
          {fn}
        </div>
      ))}
      <div style={{ fontSize: "11px", color: MUTED_COLOR, fontStyle: "italic" as const, marginTop: "4px" }}>
        {`This save spans ${n} files. Per-version rollback isn't available for multi-file saves yet.`}
      </div>
    </div>
  );
}

function renderActiveSlotBody(
  saveStatus: SaveStatus | null,
  conflicts: SyncConflict[],
  romId: number,
  slot: string,
  isOffline: boolean,
  onVersionRestored: () => void,
  onCopy: CopyToSlotHandler,
): (ReactElement | null)[] {
  if (saveStatus && saveStatus.files.length > 0) {
    // Multi-file save (#908 guard): the slot's current save is one game state
    // spread across N files. The siblings are components, not prior versions,
    // so suppress the per-file VersionHistoryPanel/rollback AND the copy action
    // (copying one component would produce an incoherent set) and show the
    // component list + a calm note instead.
    if (saveStatus.multi_file) {
      const componentFiles = saveStatus.component_files ?? [];
      return [
        ...saveStatus.files.map((f) => {
          const conflict = conflicts.find((c) => c.filename === f.filename);
          return <div key={f.filename}>{renderSaveFileRow(f, conflict, saveStatus.last_sync_check_at)}</div>;
        }),
        renderMultiFileNote(componentFiles),
      ];
    }
    return saveStatus.files.map((f) => {
      const conflict = conflicts.find((c) => c.filename === f.filename);
      return (
        <div key={f.filename}>
          {renderSaveFileRow(f, conflict, saveStatus.last_sync_check_at)}
          {/* Copy-to-slot on the active slot's current save (source = this slot).
              Rendered as a sibling of the save-file row (never nested in its
              DialogButton) so Deck focus stays flat. Only when it has a server save. */}
          {f.server_save_id != null ? (
            <div
              key={`copy-active-${f.filename}`}
              style={{ display: "flex", justifyContent: "flex-end" as const, marginTop: "4px" }}
            >
              {renderCopyToSlotButton(`copy-active-${f.filename}`, f.server_save_id, {
                onCopy,
                sourceSlot: slot,
                isOffline,
              })}
            </div>
          ) : null}
          <VersionHistoryPanel
            key={`vhp-${f.filename}`}
            romId={romId}
            slot={slot}
            filename={f.filename}
            isOffline={isOffline}
            onRestored={onVersionRestored}
            onCopy={onCopy}
          />
        </div>
      );
    });
  }
  return [
    <div key="no-files" style={{ fontSize: "13px", color: MUTED_COLOR, fontStyle: "italic" }}>
      No save files tracked yet
    </div>,
  ];
}

interface SlotPanelProps {
  romId: number;
  slot: SaveSlotSummary;
  isActive: boolean;
  defaultExpanded: boolean;
  // Active slot data (only set when isActive === true)
  saveStatus: SaveStatus | null;
  conflicts: SyncConflict[];
  isOffline: boolean;
  // Callbacks
  onSlotSwitched: (newSlot: string, newStatus: SaveStatus) => void;
  onVersionRestored: () => void;
  onSlotDeleted: () => void;
  /** Opens the copy-to-slot picker for a save row in this slot. */
  onCopy: CopyToSlotHandler;
}

export const SlotPanel: FC<SlotPanelProps> = ({
  romId,
  slot,
  isActive,
  defaultExpanded,
  saveStatus,
  conflicts,
  isOffline,
  onSlotSwitched,
  onVersionRestored,
  onSlotDeleted,
  onCopy,
}) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [slotFiles, setSlotFiles] = useState<SlotSaveFile[] | null>(null);
  const [loadingSlot, setLoadingSlot] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const switchErrorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (switchErrorTimerRef.current) clearTimeout(switchErrorTimerRef.current);
    };
  }, []);

  const slotName = slot.slot;

  const handleToggle = async () => {
    const willExpand = !expanded;
    setExpanded(willExpand);

    // Lazy-load slot saves for inactive slots on first expand
    if (willExpand && !isActive && slotFiles === null) {
      setLoadingSlot(true);
      try {
        const result = await getSlotSaves(romId, slotName);
        setSlotFiles(result.success ? result.saves : []);
      } catch (e) {
        detach(debugLog(`SavesTab: failed to load slot saves for ${slotName}: ${e}`));
        setSlotFiles([]);
      } finally {
        setLoadingSlot(false);
      }
    }
  };

  const handleActivate = async () => {
    setSwitching(true);
    setSwitchError(null);
    try {
      const outcome = await executeSlotSwitch(romId, slotName, "activate");
      if (outcome.ok) {
        onSlotSwitched(slotName, outcome.saveStatus);
        return;
      }
      setSwitchError(outcome.message);
      if (switchErrorTimerRef.current) clearTimeout(switchErrorTimerRef.current);
      switchErrorTimerRef.current = setTimeout(() => setSwitchError(null), 5000);
    } finally {
      setSwitching(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      const info = await loadSlotDeleteInfo(romId, slotName);
      if (!info) return;

      showModal(
        <ConfirmModal
          strTitle="Delete Slot"
          strDescription={formatSlotDeleteDescription(info)}
          strOKButtonText="Delete"
          strCancelButtonText="Cancel"
          onOK={() => {
            (async () => {
              if (await executeSlotDelete(romId, slotName)) onSlotDeleted();
            })().catch((e) => logError(`SavesTab: slot '${slotName}' threw after its delete: ${e}`));
          }}
        />,
      );
    } finally {
      setDeleting(false);
    }
  };

  const { syncSummaryText, syncSummaryColor } = computeSyncSummary(isActive, saveStatus, conflicts);

  // Active slot: prefer the server-side save count (SaveSlotSummary.count, kept
  // fresh by the parent's romm_data_changed → getSaveSlots refresh) so the header
  // reflects every save in the slot — a copy adds a version that the tracked-file
  // count (files.length, usually 1) would never show. Fall back to the tracked
  // file count when the summary count is absent (0 — e.g. the synthesized
  // active-slot placeholder before getSaveSlots resolves).
  const fileCount = isActive ? slot.count || (saveStatus?.files.length ?? 0) : (slotFiles?.length ?? slot.count);

  // The slot-less legacy bucket is read-only (#1276, #1478) and
  // demoted: muted styling + a read-only note; it sorts last (in SavesTab).
  const isLegacy = slotName === "";

  const panelClasses = [
    "romm-slot-panel",
    isActive ? "romm-slot-panel-active" : "",
    isLegacy ? "romm-slot-panel-legacy" : "",
  ]
    .filter(Boolean)
    .join(" ");

  // --- Source badge ---
  const sourceBadge =
    slot.source === "local" ? (
      <span key="src" className="romm-slot-badge romm-slot-badge-local">
        local
      </span>
    ) : (
      <span key="src" className="romm-slot-badge romm-slot-badge-server">
        server
      </span>
    );

  // --- Slot header ---
  const headerEl = (
    <DialogButton
      key="header"
      className="romm-slot-header"
      style={{
        background: "transparent",
        border: "none",
        padding: "10px 12px",
        textAlign: "left" as const,
        width: "100%",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
      noFocusRing={false}
      onFocus={scrollFocusedToCenter}
      onClick={() => {
        detach(handleToggle());
      }}
    >
      {/* Left: slot name + badges */}
      <div className="romm-slot-header-left">
        <span className="romm-slot-name">{displaySlot(slotName)}</span>
        {isActive ? (
          <span key="active" className="romm-slot-badge romm-slot-badge-active">
            active
          </span>
        ) : null}
        {sourceBadge}
      </div>
      {/* Right: file count + chevron */}
      <div className="romm-slot-header-right">
        <span className="romm-slot-count">{`${fileCount} save${fileCount === 1 ? "" : "s"}`}</span>
        <span className="romm-slot-chevron">{expanded ? "▾" : "▸"}</span>
      </div>
    </DialogButton>
  );

  // --- Sync summary line (active slot only) ---
  const syncSummaryEl =
    isActive && syncSummaryText ? (
      <div key="sync-summary" className="romm-slot-sync-summary" style={{ color: syncSummaryColor }}>
        {syncSummaryText}
      </div>
    ) : null;

  // --- Read-only note (legacy bucket only, always visible) ---
  const legacyNoteEl = isLegacy ? (
    <div key="legacy-note" className="romm-slot-legacy-note">
      Saves uploaded to RomM without a slot. RomM keeps them as a manual archive and never syncs them between devices.
    </div>
  ) : null;

  // --- Slot body ---
  let bodyEl: ReactElement | null = null;
  if (expanded) {
    bodyEl = isActive ? (
      <div key="body" className="romm-slot-body">
        {renderActiveSlotBody(saveStatus, conflicts, romId, slotName, isOffline, onVersionRestored, onCopy).filter(
          Boolean,
        )}
      </div>
    ) : (
      <InactiveSlotBody
        key="body"
        loadingSlot={loadingSlot}
        slotFiles={slotFiles}
        switching={switching}
        switchError={switchError}
        isOffline={isOffline}
        // Legacy bucket is read-only (#1276 / #1478) — no Activate/Delete.
        isLegacy={isLegacy}
        deleting={deleting}
        handleActivate={() => {
          handleActivate().catch((e) => logError(`SavesTab: slot '${slotName}' threw after its switch: ${e}`));
        }}
        handleDelete={() => {
          handleDelete().catch((e) => logError(`SavesTab: slot '${slotName}' threw before its delete: ${e}`));
        }}
        // Copy source = this (inactive/legacy) slot; the picker excludes it as a target.
        copy={{ onCopy, sourceSlot: slotName, isOffline }}
      />
    );
  }

  return (
    <div key={`slot-${slotName}`} className={panelClasses}>
      {headerEl}
      {syncSummaryEl}
      {legacyNoteEl}
      {bodyEl}
    </div>
  );
};
