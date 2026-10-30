/**
 * SavesTab — slot-based collapsible save file browser: a stacked list of
 * collapsible slot panels.
 *
 * - Active slot expanded by default, inactive slots collapsed.
 * - New Slot asks for the name in a modal, with inline error feedback.
 */

import { useState, useEffect, useRef, FC, type ReactElement } from "react";
import { DialogButton, Focusable, showModal } from "@decky/ui";
import { debugLog, logError, logWarn } from "../api/backend";
import { getRommConnectionState, onRommConnectionChange } from "../utils/connectionState";
import type { SaveStatus, SyncConflict, SaveSlotSummary, LastKnownSlots } from "../types";
import { scrollFocusedToCenter } from "../utils/scrollHelpers";
import { MUTED_COLOR, sortSaveSlots, filterSaveSlotsForDisplay } from "../utils/saveHelpers";
import { renderLastKnownSlots } from "./saves/LastKnownSlotList";
import { NewSlotModal } from "./saves/NewSlotModal";
import { SlotPanel } from "./saves/SlotPanel";
import { ConnectingIndicator } from "./saves/ConnectingIndicator";
import { renderSaveFileRow } from "./saves/SaveFileRow";
import { useCopyToSlot } from "./saves/useCopyToSlot";
import { detach } from "../utils/detach";
import { showToast } from "../utils/toast";
import { announceSaveSync } from "../utils/saveSyncEvents";
import { strandedVersionNotice } from "../utils/saveSlotWording";
import { executeSlotSwitch, findStrandedVersion, type StrandedVersion } from "../utils/saveSlotFlow";

interface SavesTabProps {
  /** The group's sticky Steam appId — drives the stranded-version check. */
  appId: number;
  romId: number;
  saveStatus: SaveStatus | null;
  conflicts: SyncConflict[];
  activeSlot: string | null;
  /** Whether `activeSlot` is an answer for this ROM. False while none has
   *  landed — the panel starts every ROM on a placeholder slot name, and this
   *  tab is what would otherwise render it as a fact (#1747). */
  activeSlotKnown: boolean;
  availableSlots: SaveSlotSummary[];
  /** The slots as of the last contact, shown only while no live answer has
   *  landed — read-only, and never a source for `activeSlot` (#1755). */
  lastKnownSlots: LastKnownSlots | null;
  slotsLoading: boolean;
  /** What the active slot reads while it holds no save (#2149 D3). */
  noSaveLine: string;
  onSlotSwitched: (newSlot: string, newStatus: SaveStatus) => void;
}

export const SavesTab: FC<SavesTabProps> = ({
  appId,
  romId,
  saveStatus,
  conflicts,
  activeSlot,
  activeSlotKnown,
  availableSlots,
  lastKnownSlots,
  slotsLoading,
  noSaveLine,
  onSlotSwitched,
}) => {
  const [newSlotError, setNewSlotError] = useState<string | null>(null);
  const newSlotErrorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [isOffline, setIsOffline] = useState(getRommConnectionState() === "offline");
  const [strandedVersion, setStrandedVersion] = useState<StrandedVersion | null>(null);

  const handleVersionRestored = () => {
    announceSaveSync(romId);
  };

  const handleSlotDeleted = () => {
    announceSaveSync(romId);
  };

  // Offline banner reacts live to any reachability signal via the shared store
  // (#1345) — appears when a call reports the server unreachable, clears when the
  // recovery probe or a successful call reconnects, without a tab re-entry.
  useEffect(() => onRommConnectionChange((s) => setIsOffline(s === "offline")), []);

  useEffect(() => {
    return () => {
      if (newSlotErrorTimerRef.current) clearTimeout(newSlotErrorTimerRef.current);
    };
  }, []);

  // Stranded-saves reminder: after a version switch the previously-bound
  // install is an INACTIVE sibling whose saves stay on disk and sync only once
  // the user switches back to it. Re-runs on saves-tab load (mount) and on a
  // version switch (romId changes).
  useEffect(() => {
    let cancelled = false;
    const isCancelled = () => cancelled;
    const check = async (): Promise<void> => {
      try {
        const stranded = await findStrandedVersion(appId, isCancelled);
        if (!isCancelled()) setStrandedVersion(stranded);
      } catch (e) {
        if (!isCancelled()) setStrandedVersion(null);
        logWarn(`SavesTab: stranded-version check failed for appId ${appId}: ${e}`);
      }
    };
    detach(check());
    return () => {
      cancelled = true;
    };
  }, [appId, romId]);

  // Copy-to-slot opener, shared by every save row across the slot panels. The
  // picker's target list is derived from availableSlots (minus the source slot
  // and the legacy "" bucket); on success it dispatches romm_data_changed so the
  // parent re-fetches and both the source and target views refresh.
  const openCopyModal = useCopyToSlot(romId, availableSlots);

  // --- Offline banner ---
  const offlineBanner = isOffline ? (
    <div
      key="offline-banner"
      style={{
        padding: "8px",
        background: "rgba(217, 65, 38, 0.15)",
        borderRadius: "4px",
        border: "1px solid rgba(217, 65, 38, 0.4)",
        marginBottom: "12px",
        fontSize: "12px",
        color: "#d94126",
      }}
    >
      {"RomM is offline — slot switching is disabled until the server is reachable. This prevents save sync conflicts."}
    </div>
  ) : null;

  // --- Stranded-version reminder banner ---
  const strandedBanner = strandedVersion ? (
    <div
      key="stranded-banner"
      style={{
        padding: "8px",
        background: "rgba(255, 136, 0, 0.15)",
        borderRadius: "4px",
        border: "1px solid rgba(255, 136, 0, 0.3)",
        marginBottom: "12px",
        fontSize: "12px",
        color: "#ff8800",
      }}
    >
      {strandedVersionNotice(strandedVersion)}
    </div>
  ) : null;

  // --- Loading state ---
  // A spinner + live retry progress (#1345) instead of bare italic text — the
  // slot fetch pays the backend retry ladder, so surface "Connecting to RomM…
  // (attempt N/M)" while it is in flight.
  if (slotsLoading) {
    return (
      <Focusable noFocusRing={true}>
        {offlineBanner}
        {strandedBanner}
        <ConnectingIndicator key="connecting" />
      </Focusable>
    );
  }

  // --- Sort: active first, named alphabetically, legacy "" bucket last (#1478) ---
  const sorted = sortSaveSlots(availableSlots, activeSlot, activeSlotKnown);

  // --- New Slot button handler ---
  const handleNewSlot = () => {
    showModal(
      <NewSlotModal
        onSubmit={(name: string) => {
          // Switching into the slot-less legacy bucket is retired (#1276): an
          // empty name is not a valid slot target, so ignore it rather than
          // offering legacy mode. Legacy saves stay viewable in their own panel.
          if (!name) return;
          (async () => {
            const outcome = await executeSlotSwitch(romId, name, "create");
            if (outcome.ok) {
              onSlotSwitched(name, outcome.saveStatus);
              return;
            }
            if (outcome.partial) {
              // Toasted as a slot panel toasts it, so both surfaces show a
              // partial answer the same way.
              showToast(outcome.message);
              return;
            }
            if (outcome.reason !== null) {
              detach(debugLog(`SavesTab: new slot switch failed: ${outcome.reason}`));
            }
            setNewSlotError(outcome.message);
            if (newSlotErrorTimerRef.current) clearTimeout(newSlotErrorTimerRef.current);
            newSlotErrorTimerRef.current = setTimeout(() => setNewSlotError(null), 5000);
          })().catch((e) => logError(`SavesTab: new slot '${name}' threw after the switch: ${e}`));
        }}
      />,
    );
  };

  // --- The slots as of the last contact ---
  // Only while nothing live has landed, and only as history: the rows are not
  // pressable and nothing here feeds `activeSlot`, so the tab still names no
  // slot as the current one (#1747, #1755).
  const lastKnownSection = !activeSlotKnown && lastKnownSlots ? renderLastKnownSlots(lastKnownSlots) : null;

  // --- Save files with no slot panel to live under ---
  // Legacy mode has no slot; an unanswered active slot has none this tab may
  // name (#1747). Either way the locally tracked files are still what the user
  // has, so they are shown — just not filed under a slot.
  let unslottedFilesSection: ReactElement | null = null;
  if (activeSlot === null || !activeSlotKnown) {
    if (saveStatus && saveStatus.files.length > 0) {
      unslottedFilesSection = (
        <div key="unslotted-files" style={{ marginBottom: "12px" }}>
          {saveStatus.files.map((f) => {
            const conflict = conflicts.find((c) => c.filename === f.filename);
            return renderSaveFileRow(f, conflict, saveStatus.last_sync_check_at);
          })}
        </div>
      );
    } else {
      unslottedFilesSection = (
        <div key="no-files" style={{ fontSize: "13px", color: MUTED_COLOR, fontStyle: "italic", marginBottom: "12px" }}>
          {noSaveLine}
        </div>
      );
    }
  }

  return (
    <Focusable noFocusRing={true} style={{ display: "flex", flexDirection: "column" as const, gap: "0" }}>
      {offlineBanner}
      {strandedBanner}

      {/* Save files that belong to no slot panel — above the panels, if any. */}
      {unslottedFilesSection}

      {/* The last contact's slots, where there are no live panels to show. */}
      {lastKnownSection}

      {/* Slot panels — skip the "" (legacy) panel when already in legacy mode */}
      {filterSaveSlotsForDisplay(sorted, activeSlot).map((slot) => {
        const isActive = activeSlot !== null && slot.slot === activeSlot;
        return (
          <SlotPanel
            // Whether the slot is active is part of the key: a panel reads
            // `defaultExpanded` once, so only a remount opens the new active slot
            // and closes the old one, and every other panel keeps its state.
            key={`panel-${slot.slot}-${isActive ? "active" : "inactive"}`}
            romId={romId}
            slot={slot}
            isActive={isActive}
            defaultExpanded={isActive}
            saveStatus={isActive ? saveStatus : null}
            conflicts={isActive ? conflicts : []}
            noSaveLine={noSaveLine}
            isOffline={isOffline}
            onSlotSwitched={onSlotSwitched}
            onVersionRestored={handleVersionRestored}
            onSlotDeleted={handleSlotDeleted}
            onCopy={openCopyModal}
          />
        );
      })}

      {/* New Slot button + error feedback */}
      <div key="new-slot-area" style={{ marginTop: "10px" }}>
        <DialogButton
          key="new-slot-btn"
          style={{
            padding: "6px 12px",
            minWidth: "auto",
            fontSize: "12px",
            width: "auto",
          }}
          noFocusRing={false}
          onFocus={scrollFocusedToCenter}
          onClick={handleNewSlot}
          // Creating a slot is a server write, so offline it can only end in the
          // failure the banner above already predicts — after the full retry
          // ladder. The sibling actions on this tab (slot switch, copy-to-slot)
          // are disabled the same way.
          disabled={isOffline}
        >
          + New Slot
        </DialogButton>
        {newSlotError ? (
          <div key="new-slot-error" style={{ fontSize: "11px", color: "#d94126", marginTop: "4px" }}>
            {newSlotError}
          </div>
        ) : null}
      </div>
    </Focusable>
  );
};
