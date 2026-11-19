/**
 * SaveManagementCard — save slot browser and manager for the Desktop game view.
 *
 * Parity with the game-detail save sync flows:
 * - Returns `null` and issues zero reads while `detail.romId === null` or the
 *   backend reports a stranded panel (`useStrandedAnswer`).
 * - Save sync off (`!detail.saveSyncEnabled`): reads `getLocalSaveFiles(romId)`
 *   to list this device's save files or explain refusal / not-downloaded state.
 * - Save sync on: checks `getSaveSyncAvailability(romId)` (`refusesDownloadedGame`,
 *   `notDownloadedStatement`, `noSaveLine`), shows live retry progress while
 *   loading with a known-offline fast path and auto-reload on reconnect, renders
 *   unslotted local files and stale last-known snapshots, delegates collapsible
 *   slot panels and per-file version history to `DesktopSlotPanel`, renders
 *   inline slot-tracking confirmation and stranded-version warnings, and drives
 *   manual sync, delete local saves, `+ New Slot`, and `Copy to slot...`
 *   dialogs.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type Dispatch,
  type FC,
  type MutableRefObject,
  type RefObject,
  type SetStateAction,
} from "react";
import {
  confirmSlotChoice,
  debugLog,
  getLocalSaveFiles,
  getSaveSlots,
  getSaveSyncAvailability,
  isSaveTrackingConfigured,
  logWarn,
} from "../../api/backend";
import type {
  LastKnownSlots,
  LocalSaveFiles,
  SaveSlotSummary,
  SaveStatus,
  SaveSyncAvailability,
  WireReason,
} from "../../types";
import {
  beginServerLoad,
  reportServerReachable,
  setServerRetryProgress,
  settleServerLoad,
  useRommConnectionState,
  useServerRetryProgress,
  type ServerRetryProgress,
} from "../../utils/connectionState";
import { detach } from "../../utils/detach";
import { formatBytes, formatTimestamp } from "../../utils/formatters";
import { refreshSaveStatus, type GameDetailState } from "../../utils/gameDetailStore";
import { executeManualSaveSync } from "../../utils/manualSaveSync";
import { usePruneLeaseOwner } from "../../utils/pruneLease";
import { MUTED_COLOR, displaySlot, filterSaveSlotsForDisplay, sortSaveSlots } from "../../utils/saveHelpers";
import {
  executeCopySaveToSlot,
  executeDeleteLocalSaves,
  executeSlotSwitch,
  findStrandedVersion,
  type StrandedVersion,
} from "../../utils/saveSlotFlow";
import { LAST_KNOWN_SLOTS_STALE_NOTE, connectingToRommText, strandedVersionNotice } from "../../utils/saveSlotWording";
import { announceSaveSync } from "../../utils/saveSyncEvents";
import {
  NOT_DOWNLOADED_NO_SAVE,
  NO_SAVE_ON_DEVICE,
  SAVE_SYNC_OFF,
  emptySlotLine,
  noSaveLine,
  notDownloadedStatement,
  refusalHint,
  refusesDownloadedGame,
} from "../../utils/saveSyncStatement";
import { applyLoadSlotsResult, applyRefreshSlotResult, type LoadSlotsFields } from "../../utils/slotState";
import { useStrandedAnswer } from "../../utils/strandedPanelStore";
import { showToast } from "../../utils/toast";
import { DesktopSlotPanel, renderDesktopSaveFileRow } from "./DesktopSlotPanel";
import { SvgThrobber } from "./SvgThrobber";
import type { AskDialog } from "./dialogs/askDialog";
import { DesktopNewSlotDialog } from "./dialogs/DesktopNewSlotDialog";
import {
  desktopCopySlotDialog,
  desktopDeleteLocalSavesDialog,
  desktopSlotConflictDialog,
} from "./dialogs/desktopDialogs";
import { useDialogHost } from "./dialogs/useDialogHost";
import { BUTTON_STYLE, CARD_STYLE } from "./styles";

interface SaveManagementCardProps {
  appId: number;
  detail: GameDetailState;
  ask?: AskDialog | undefined;
}

type SlotState = LoadSlotsFields;

const INITIAL_SLOT_STATE: SlotState = {
  activeSlot: null,
  activeSlotKnown: false,
  availableSlots: [],
  lastKnownSlots: null,
  slotsLoading: false,
};

const HEADER_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_STYLE,
  display: "inline-flex",
  alignItems: "center",
  gap: "6px",
};

const REFUSAL_BOX_STYLE: CSSProperties = {
  padding: "10px 12px",
  borderRadius: "4px",
  backgroundColor: "rgba(212, 167, 44, 0.1)",
  border: "1px solid rgba(212, 167, 44, 0.35)",
};

const PILL_BADGE_BASE: CSSProperties = {
  fontSize: "10px",
  padding: "1px 6px",
  borderRadius: "2px",
  letterSpacing: "0.04em",
  textTransform: "uppercase",
};

const MUTED_PILL_BADGE_STYLE: CSSProperties = {
  ...PILL_BADGE_BASE,
  fontWeight: 600,
  backgroundColor: "rgba(255, 255, 255, 0.08)",
  color: MUTED_COLOR,
};

function headerButtonStyle(disabled: boolean, danger = false): CSSProperties {
  return {
    ...HEADER_BUTTON_STYLE,
    ...(danger ? { borderColor: "rgba(217, 65, 38, 0.4)", color: "#ff6b6b" } : {}),
    opacity: disabled ? 0.5 : 1,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}

function cardSubtitle(saveSyncEnabled: boolean, syncRefused: boolean, slotCount: number): string {
  if (!saveSyncEnabled) return "save sync off";
  if (syncRefused) return "save sync unavailable";
  return `${slotCount} slot${slotCount === 1 ? "" : "s"} · save sync on`;
}

function reportSlotResultReachability(result: { success: boolean; reason?: string }): void {
  if (result.success) {
    reportServerReachable(true);
  } else if (result.reason === "server_unreachable") {
    reportServerReachable(false);
  }
}

async function readTrackingConfigured(
  romId: number,
  isCurrent: () => boolean,
  onResult: (configured: boolean) => void,
): Promise<void> {
  let configured = true;
  try {
    const tracking = await isSaveTrackingConfigured(romId);
    configured = tracking.configured;
  } catch (e) {
    logWarn(`DesktopSaveManagementCard: failed to check tracking: ${e}`);
  }
  if (isCurrent()) onResult(configured);
}

async function readSurfaceAnswer<T>(
  reader: () => Promise<T>,
  label: string,
  romId: number,
  isLatest: () => boolean,
  setter: (value: T | null) => void,
): Promise<void> {
  try {
    const answer = await reader();
    if (isLatest()) setter(answer);
  } catch (e) {
    logWarn(`DesktopSaveManagementCard: ${label} read for romId=${romId} failed: ${e}`);
    if (isLatest()) setter(null);
  }
}

function useTabSwitchScroll(cardRef: RefObject<HTMLDivElement | null>): void {
  useEffect(() => {
    const onTabSwitch = (e: Event) => {
      if ((e as CustomEvent<{ tab?: string } | null>).detail?.tab === "saves") {
        cardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    };
    globalThis.addEventListener("romm_tab_switch", onTabSwitch);
    return () => globalThis.removeEventListener("romm_tab_switch", onTabSwitch);
  }, [cardRef]);
}

function useSaveSurfaceAnswers(
  romId: number,
  saveSyncEnabled: boolean,
): { localSavesAnswer: LocalSaveFiles | null; saveSyncAnswer: SaveSyncAvailability | null } {
  const [rawLocalSavesAnswer, setRawLocalSavesAnswer] = useState<LocalSaveFiles | null>(null);
  const [rawSaveSyncAnswer, setRawSaveSyncAnswer] = useState<SaveSyncAvailability | null>(null);

  useEffect(() => {
    let cancelled = false;
    let latestSeq = 0;
    const read = async () => {
      const seq = ++latestSeq;
      const isLatest = () => !cancelled && seq === latestSeq;
      if (!saveSyncEnabled) {
        await readSurfaceAnswer(() => getLocalSaveFiles(romId), "local saves", romId, isLatest, setRawLocalSavesAnswer);
        return;
      }
      await readSurfaceAnswer(
        () => getSaveSyncAvailability(romId),
        "save sync availability",
        romId,
        isLatest,
        setRawSaveSyncAnswer,
      );
    };
    detach(read());
    const onDataChanged = ({ detail: ev }: WindowEventMap["romm_data_changed"]) => {
      if (
        (ev.type === "save_sync" && (ev.rom_id === undefined || ev.rom_id === romId)) ||
        ev.type === "core_changed" ||
        ev.type === "emulator_sources"
      ) {
        detach(read());
      }
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => {
      cancelled = true;
      globalThis.removeEventListener("romm_data_changed", onDataChanged);
    };
  }, [romId, saveSyncEnabled]);

  return {
    localSavesAnswer: saveSyncEnabled ? null : rawLocalSavesAnswer,
    saveSyncAnswer: saveSyncEnabled ? rawSaveSyncAnswer : null,
  };
}

function useStrandedCheck(appId: number, romId: number, active: boolean): StrandedVersion | null {
  const [rawStrandedVersion, setRawStrandedVersion] = useState<StrandedVersion | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const isCancelled = () => cancelled;
    detach(
      findStrandedVersion(appId, isCancelled)
        .then((stranded) => {
          if (!isCancelled()) setRawStrandedVersion(stranded);
        })
        .catch((e) => {
          if (!isCancelled()) setRawStrandedVersion(null);
          logWarn(`DesktopSaveManagementCard: stranded-version check failed for appId ${appId}: ${e}`);
        }),
    );
    return () => {
      cancelled = true;
    };
  }, [appId, romId, active]);

  return active ? rawStrandedVersion : null;
}

interface SlotLoadRun {
  settled: boolean;
}

async function loadDesktopSlots(
  romId: number,
  run: SlotLoadRun,
  isCancelled: () => boolean,
  slotsLoadedRef: MutableRefObject<boolean>,
  setSlotState: Dispatch<SetStateAction<SlotState>>,
): Promise<void> {
  const load = beginServerLoad();
  setServerRetryProgress(null);
  setSlotState((prev) => ({ ...prev, slotsLoading: true }));
  try {
    const result = await getSaveSlots(romId);
    if (isCancelled()) return;
    run.settled = true;
    reportSlotResultReachability(result);
    applyLoadSlotsResult<SlotState>(result, setSlotState, slotsLoadedRef, (msg) => detach(debugLog(msg)));
  } catch (e) {
    logWarn(`DesktopSaveManagementCard: failed to load save slots: ${e}`);
    if (!isCancelled()) {
      run.settled = true;
      slotsLoadedRef.current = false;
      setSlotState((prev) => ({ ...prev, slotsLoading: false }));
    }
  } finally {
    settleServerLoad(load);
  }
}

async function loadOfflineSnapshot(
  romId: number,
  isCancelled: () => boolean,
  slotsLoadedRef: MutableRefObject<boolean>,
  setSlotState: Dispatch<SetStateAction<SlotState>>,
): Promise<void> {
  try {
    const res = await getSaveSlots(romId);
    if (isCancelled()) return;
    if (res.success) {
      reportServerReachable(true);
      slotsLoadedRef.current = true;
      applyLoadSlotsResult<SlotState>(res, setSlotState, slotsLoadedRef, (m) => detach(debugLog(m)));
    } else {
      applyRefreshSlotResult<SlotState>(res, setSlotState);
    }
  } catch (e) {
    logWarn(`DesktopSaveManagementCard: offline slot snapshot read failed: ${e}`);
  }
}

const RefusalBox: FC<{
  testId?: string;
  message: string | undefined;
  reason: WireReason | undefined;
}> = ({ testId, message, reason }) => {
  const hint = refusalHint(reason);
  return (
    <div data-testid={testId} style={REFUSAL_BOX_STYLE}>
      <div style={{ fontSize: "12px", color: "#d4a72c" }}>{message ?? ""}</div>
      {hint && <div style={{ fontSize: "12px", color: "#dfe3e6", marginTop: "6px" }}>{hint}</div>}
    </div>
  );
};

const SyncOffSection: FC<{
  syncOffRefused: boolean;
  localSavesAnswer: LocalSaveFiles | null;
}> = ({ syncOffRefused, localSavesAnswer }) => {
  let body = null;
  if (syncOffRefused && localSavesAnswer) {
    body = <RefusalBox message={localSavesAnswer.message} reason={localSavesAnswer.reason} />;
  } else if (localSavesAnswer && localSavesAnswer.files.length > 0) {
    body = (
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        {localSavesAnswer.files.map((f) => (
          <div
            key={`${f.directory}/${f.filename}`}
            data-testid="desktop-local-save-row"
            style={{
              padding: "10px 12px",
              backgroundColor: "rgba(0, 0, 0, 0.18)",
              borderRadius: "3px",
              border: "1px solid rgba(255, 255, 255, 0.06)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "8px",
                marginBottom: "4px",
              }}
            >
              <span style={{ fontSize: "13px", fontWeight: 600, color: "#ffffff" }}>{f.filename}</span>
              <span style={{ fontSize: "12px", color: MUTED_COLOR }}>{formatBytes(f.size)}</span>
            </div>
            <div
              style={{ fontSize: "12px", color: MUTED_COLOR }}
            >{`Last changed: ${formatTimestamp(f.modified_at)}`}</div>
            <div
              style={{
                fontSize: "11px",
                color: MUTED_COLOR,
                fontFamily: "monospace",
                wordBreak: "break-all",
                marginTop: "2px",
              }}
            >
              {`Folder: ${f.directory}`}
            </div>
          </div>
        ))}
      </div>
    );
  } else if (localSavesAnswer) {
    body = (
      <div style={{ fontSize: "12px", color: MUTED_COLOR }}>
        {localSavesAnswer.installed ? NO_SAVE_ON_DEVICE : NOT_DOWNLOADED_NO_SAVE}
      </div>
    );
  }

  return (
    <div data-testid="desktop-saves-sync-off">
      <div style={{ fontSize: "12px", color: MUTED_COLOR, marginBottom: "8px" }}>{SAVE_SYNC_OFF}</div>
      {body}
    </div>
  );
};

const LastKnownSection: FC<{ lastKnown: LastKnownSlots }> = ({ lastKnown }) => {
  const activeKey = lastKnown.activeSlot ?? "";
  return (
    <div key="last-known-slots" data-testid="desktop-last-known-slots" style={{ marginTop: "8px" }}>
      <div style={{ fontSize: "12px", color: MUTED_COLOR, fontStyle: "italic", marginBottom: "8px" }}>
        {LAST_KNOWN_SLOTS_STALE_NOTE}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {lastKnown.slots.map((slot) => {
          const isActive = slot.slot === activeKey;
          return (
            <div
              key={`last-known-${slot.slot}`}
              data-testid={`desktop-last-known-slot-${slot.slot || "legacy"}`}
              style={{
                backgroundColor: "rgba(0, 0, 0, 0.14)",
                borderRadius: "3px",
                border: "1px solid rgba(255, 255, 255, 0.05)",
                padding: "10px 14px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "14px", fontWeight: 600, color: "#dfe3e6" }}>{displaySlot(slot.slot)}</span>
                  {isActive && (
                    <span
                      style={{
                        ...PILL_BADGE_BASE,
                        fontWeight: 700,
                        backgroundColor: "#5ba32b",
                        color: "#ffffff",
                      }}
                    >
                      ACTIVE
                    </span>
                  )}
                  <span style={MUTED_PILL_BADGE_STYLE}>{slot.source === "local" ? "LOCAL" : "SERVER"}</span>
                  <span style={{ ...MUTED_PILL_BADGE_STYLE, backgroundColor: "rgba(255, 255, 255, 0.06)" }}>
                    LAST KNOWN
                  </span>
                </div>
                <span style={{ fontSize: "12px", color: MUTED_COLOR }}>
                  {`${slot.count} save${slot.count === 1 ? "" : "s"}`}
                </span>
              </div>
              {slot.latest_updated_at && (
                <div style={{ fontSize: "11px", color: MUTED_COLOR, marginTop: "4px" }}>
                  {`Newest save: ${formatTimestamp(slot.latest_updated_at)}`}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

const UnslottedFilesSection: FC<{
  saveStatus: SaveStatus | null;
  noSaveText: string;
}> = ({ saveStatus, noSaveText }) => {
  if (saveStatus && saveStatus.files.length > 0) {
    return (
      <div data-testid="desktop-unslotted-files">
        <div style={{ marginBottom: "12px" }}>
          {saveStatus.files.map((file) => {
            const conflict = (saveStatus.conflicts ?? []).find((c) => c.filename === file.filename);
            return renderDesktopSaveFileRow(file, conflict, saveStatus.last_sync_check_at);
          })}
        </div>
      </div>
    );
  }
  const line = emptySlotLine(saveStatus ?? null, noSaveText);
  return (
    <div data-testid="desktop-unslotted-files">
      {line !== null && (
        <div style={{ fontSize: "13px", color: MUTED_COLOR, fontStyle: "italic", marginBottom: "12px" }}>{line}</div>
      )}
    </div>
  );
};

const CardHeader: FC<{
  subtitleText: string;
  hidesHeaderSlotControls: boolean;
  showDeleteLocalSaves: boolean;
  isSyncing: boolean;
  isOffline: boolean;
  deletingLocalSaves: boolean;
  onSyncNow: () => void;
  onDeleteLocalSaves: () => void;
  onOpenNewSlot: () => void;
}> = ({
  subtitleText,
  hidesHeaderSlotControls,
  showDeleteLocalSaves,
  isSyncing,
  isOffline,
  deletingLocalSaves,
  onSyncNow,
  onDeleteLocalSaves,
  onOpenNewSlot,
}) => {
  const syncDisabled = isSyncing || isOffline || deletingLocalSaves;
  const deleteDisabled = deletingLocalSaves || isSyncing;
  const newSlotDisabled = isOffline || isSyncing;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "12px",
        flexWrap: "wrap",
        marginBottom: "16px",
        paddingBottom: "12px",
        borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "10px" }}>
        <h2
          style={{
            margin: 0,
            fontSize: "20px",
            fontWeight: 700,
            color: "#ffffff",
            letterSpacing: "-0.01em",
          }}
        >
          Saves
        </h2>
        <span style={{ fontSize: "12px", color: MUTED_COLOR }}>{subtitleText}</span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
        {!hidesHeaderSlotControls && (
          <button type="button" style={headerButtonStyle(syncDisabled)} disabled={syncDisabled} onClick={onSyncNow}>
            {isSyncing && <SvgThrobber style={{ width: "12px", height: "12px" }} />}
            <span>{isSyncing ? "Syncing…" : "Sync now"}</span>
          </button>
        )}
        {showDeleteLocalSaves && (
          <button
            type="button"
            style={headerButtonStyle(deleteDisabled, true)}
            disabled={deleteDisabled}
            onClick={onDeleteLocalSaves}
          >
            {deletingLocalSaves && <SvgThrobber style={{ width: "12px", height: "12px" }} />}
            <span>{deletingLocalSaves ? "Deleting…" : "Delete Local Saves"}</span>
          </button>
        )}
        {!hidesHeaderSlotControls && (
          <button
            type="button"
            style={headerButtonStyle(newSlotDisabled)}
            disabled={newSlotDisabled}
            onClick={onOpenNewSlot}
          >
            + New Slot
          </button>
        )}
      </div>
    </div>
  );
};

const SaveSyncOnSection: FC<{
  romId: number;
  isOffline: boolean;
  strandedVersion: StrandedVersion | null;
  slotConfirmed: boolean | null;
  activeSlot: string | null;
  activeSlotKnown: boolean;
  hasSaves: boolean;
  confirmingSlot: boolean;
  notDownloaded: ReturnType<typeof notDownloadedStatement> | null;
  notDownloadedHint: string | null;
  slotState: SlotState;
  retryProgress: ServerRetryProgress | null;
  displaySlots: SaveSlotSummary[];
  saveStatus: SaveStatus | null;
  noSaveText: string;
  saveSyncEnabled: boolean;
  ask: AskDialog;
  onConfirmSlot: (slotName: string) => void;
  onSlotActivated: (slotName: string, newStatus: SaveStatus) => Promise<void>;
  onSlotListChanged: () => void;
  onCopySave: (saveId: number, sourceSlot: string) => void;
}> = ({
  romId,
  isOffline,
  strandedVersion,
  slotConfirmed,
  activeSlot,
  activeSlotKnown,
  hasSaves,
  confirmingSlot,
  notDownloaded,
  notDownloadedHint,
  slotState,
  retryProgress,
  displaySlots,
  saveStatus,
  noSaveText,
  saveSyncEnabled,
  ask,
  onConfirmSlot,
  onSlotActivated,
  onSlotListChanged,
  onCopySave,
}) => {
  const showConnecting = slotState.slotsLoading && slotState.availableSlots.length === 0;
  const showUnslotted = !activeSlotKnown || activeSlot === null;
  const confirmDisabled = isOffline || confirmingSlot;

  return (
    <>
      {isOffline && (
        <div
          style={{
            padding: "8px 12px",
            background: "rgba(217, 65, 38, 0.15)",
            borderRadius: "4px",
            border: "1px solid rgba(217, 65, 38, 0.4)",
            marginBottom: "12px",
            fontSize: "12px",
            color: "#d94126",
          }}
        >
          RomM is offline — slot switching is disabled until the server is reachable. This prevents save sync conflicts.
        </div>
      )}

      {strandedVersion && (
        <div
          style={{
            padding: "8px 12px",
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
      )}

      {slotConfirmed === false && activeSlot !== null && hasSaves && (
        <div
          data-testid="unconfirmed-slot-banner"
          style={{
            padding: "8px 12px",
            background: "rgba(26, 159, 255, 0.15)",
            borderRadius: "4px",
            border: "1px solid rgba(26, 159, 255, 0.4)",
            marginBottom: "12px",
            fontSize: "12px",
            color: "#1a9fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "12px",
          }}
        >
          <div>
            <strong>Save slot tracking is not confirmed.</strong> Confirm the active slot or choose another slot to
            enable sync.
          </div>
          <button
            type="button"
            style={{
              ...BUTTON_STYLE,
              backgroundColor: "rgba(26, 159, 255, 0.2)",
              borderColor: "rgba(26, 159, 255, 0.5)",
              color: "#ffffff",
              whiteSpace: "nowrap",
              opacity: confirmDisabled ? 0.5 : 1,
              cursor: confirmDisabled ? "not-allowed" : "pointer",
            }}
            disabled={confirmDisabled}
            onClick={() => onConfirmSlot(activeSlot)}
          >
            {confirmingSlot ? "Confirming..." : `Confirm '${displaySlot(activeSlot)}'`}
          </button>
        </div>
      )}

      {notDownloaded && (
        <div data-testid="desktop-saves-not-downloaded" style={{ marginBottom: "12px" }}>
          <div style={{ fontSize: "12px", color: MUTED_COLOR }}>{notDownloaded.line}</div>
          {notDownloaded.refusal !== null && (
            <div style={{ fontSize: "12px", color: "#d4a72c", marginTop: "4px" }}>{notDownloaded.refusal}</div>
          )}
          {notDownloadedHint !== null && (
            <div style={{ fontSize: "12px", color: MUTED_COLOR, marginTop: "4px" }}>{notDownloadedHint}</div>
          )}
        </div>
      )}

      {showConnecting ? (
        <div
          data-testid="desktop-saves-connecting"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "12px 0",
            fontSize: "13px",
            color: MUTED_COLOR,
          }}
        >
          <SvgThrobber style={{ width: "14px", height: "14px" }} />
          <span>{connectingToRommText("Connecting to RomM", retryProgress)}</span>
        </div>
      ) : (
        <>
          {showUnslotted && <UnslottedFilesSection saveStatus={saveStatus} noSaveText={noSaveText} />}

          {!activeSlotKnown && slotState.lastKnownSlots && <LastKnownSection lastKnown={slotState.lastKnownSlots} />}

          {displaySlots.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {displaySlots.map((slot) => {
                const isSlotActive = activeSlot !== null && activeSlotKnown && slot.slot === activeSlot;
                return (
                  <DesktopSlotPanel
                    key={`panel-${slot.slot}-${isSlotActive ? "active" : "inactive"}`}
                    romId={romId}
                    slot={slot}
                    isActive={isSlotActive}
                    saveStatus={isSlotActive ? (saveStatus ?? null) : null}
                    noSaveLine={noSaveText}
                    isOffline={isOffline}
                    defaultExpanded={isSlotActive}
                    slotConfirmed={slotConfirmed}
                    saveSyncEnabled={saveSyncEnabled}
                    confirmingSlot={confirmingSlot}
                    ask={ask}
                    onConfirmSlot={onConfirmSlot}
                    onSlotActivated={onSlotActivated}
                    onSlotDeleted={onSlotListChanged}
                    onVersionRestored={onSlotListChanged}
                    onCopySave={onCopySave}
                  />
                );
              })}
            </div>
          )}
        </>
      )}
    </>
  );
};

function useDesktopSlotState(
  appId: number,
  romId: number,
  saveSyncEnabled: boolean,
  syncRefused: boolean,
  isOffline: boolean,
  mountedRef: MutableRefObject<boolean>,
) {
  const [slotState, setSlotState] = useState<SlotState>(INITIAL_SLOT_STATE);
  const slotsLoadedRef = useRef(false);
  const [rawSlotConfirmed, setRawSlotConfirmed] = useState<boolean | null>(null);

  const refreshSlots = useCallback(async () => {
    if (!saveSyncEnabled) return;
    try {
      const slotResult = await getSaveSlots(romId);
      if (!mountedRef.current) return;
      reportSlotResultReachability(slotResult);
      applyRefreshSlotResult<SlotState>(slotResult, setSlotState);
    } catch (e) {
      logWarn(`DesktopSaveManagementCard: failed to refresh save slots: ${e}`);
    }
    await readTrackingConfigured(romId, () => mountedRef.current, setRawSlotConfirmed);
  }, [romId, saveSyncEnabled, mountedRef]);

  // Initial slot load + known-offline fast path + reconnect reload.
  useEffect(() => {
    if (!saveSyncEnabled || syncRefused) {
      slotsLoadedRef.current = false;
      return;
    }
    if (slotsLoadedRef.current) return;

    let cancelled = false;
    const isCancelled = () => cancelled;
    const isCurrent = () => !cancelled;

    if (isOffline) {
      // Known-offline fast path: read cached `last_known` snapshot without the retry spinner,
      // keeping `slotsLoadedRef.current = false` so reconnect reloads live slots automatically.
      detach(readTrackingConfigured(romId, isCurrent, setRawSlotConfirmed));
      detach(loadOfflineSnapshot(romId, isCancelled, slotsLoadedRef, setSlotState));
      return () => {
        cancelled = true;
      };
    }

    slotsLoadedRef.current = true;
    const run: SlotLoadRun = { settled: false };
    detach(readTrackingConfigured(romId, isCurrent, setRawSlotConfirmed));
    detach(loadDesktopSlots(romId, run, isCancelled, slotsLoadedRef, setSlotState));

    return () => {
      cancelled = true;
      if (!run.settled) {
        slotsLoadedRef.current = false;
        setSlotState((prev) => (prev.slotsLoading ? { ...prev, slotsLoading: false } : prev));
      }
    };
  }, [romId, saveSyncEnabled, syncRefused, isOffline]);

  // Refresh slot list and status when `save_sync` fires for this ROM.
  useEffect(() => {
    if (!saveSyncEnabled || syncRefused) return;
    const onDataChanged = ({ detail: ev }: WindowEventMap["romm_data_changed"]) => {
      if (ev.type !== "save_sync") return;
      if (ev.rom_id !== undefined && ev.rom_id !== romId) return;
      detach(Promise.all([refreshSaveStatus(appId), refreshSlots()]));
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => globalThis.removeEventListener("romm_data_changed", onDataChanged);
  }, [appId, romId, saveSyncEnabled, syncRefused, refreshSlots]);

  return { slotState, setSlotState, rawSlotConfirmed, setRawSlotConfirmed, refreshSlots };
}

const SaveManagementCardContent: FC<{
  appId: number;
  romId: number;
  detail: GameDetailState;
  ask?: AskDialog | undefined;
}> = ({ appId, romId, detail, ask }) => {
  const dialogHost = useDialogHost();
  const effectiveAsk = ask ?? dialogHost.ask;
  const cardRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const connectionState = useRommConnectionState();
  const isOffline = connectionState === "offline";
  const retryProgress = useServerRetryProgress();

  const [confirmingSlot, setConfirmingSlot] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [deletingLocalSaves, setDeletingLocalSaves] = useState(false);

  const { localSavesAnswer, saveSyncAnswer } = useSaveSurfaceAnswers(romId, detail.saveSyncEnabled);
  const saveStatus = detail.saveStatus;
  const syncRefused = detail.saveSyncEnabled && refusesDownloadedGame(saveSyncAnswer);
  const strandedVersion = useStrandedCheck(appId, romId, detail.saveSyncEnabled && !syncRefused);
  const { slotState, setSlotState, rawSlotConfirmed, setRawSlotConfirmed, refreshSlots } = useDesktopSlotState(
    appId,
    romId,
    detail.saveSyncEnabled,
    syncRefused,
    isOffline,
    mountedRef,
  );
  const slotConfirmed = !detail.saveSyncEnabled || syncRefused ? true : rawSlotConfirmed;
  const syncOffRefused =
    !detail.saveSyncEnabled && localSavesAnswer !== null && !localSavesAnswer.available && localSavesAnswer.installed;
  const hidesHeaderSlotControls = !detail.saveSyncEnabled || syncRefused;

  useTabSwitchScroll(cardRef);

  const refreshStatusAndSlots = async () => {
    await Promise.all([refreshSaveStatus(appId), refreshSlots()]);
  };

  const autoConfirmIfNeeded = async (slotName: string): Promise<boolean> => {
    if (!detail.saveSyncEnabled || slotConfirmed !== false) return false;
    try {
      const confirmResult = await confirmSlotChoice(romId, slotName, false, null, false);
      if (confirmResult.success) {
        if (mountedRef.current) setRawSlotConfirmed(true);
        return true;
      }
    } catch (e) {
      logWarn(`DesktopSaveManagementCard: auto-confirm for '${slotName}' failed: ${e}`);
    }
    return false;
  };

  const applyActivatedSlot = async (slotName: string, verb: string) => {
    const confirmedNow = await autoConfirmIfNeeded(slotName);
    showToast(confirmedNow ? `${verb} and confirmed slot '${slotName}'` : `Switched to slot '${slotName}'`);
    if (mountedRef.current) {
      setSlotState((prev) => ({
        ...prev,
        activeSlot: slotName,
        activeSlotKnown: true,
        lastKnownSlots: null,
      }));
    }
  };

  const handleSyncNow = async () => {
    if (isSyncing || isOffline || deletingLocalSaves) return;
    setIsSyncing(true);
    try {
      await executeManualSaveSync(appId, romId);
      await refreshSlots();
    } finally {
      if (mountedRef.current) setIsSyncing(false);
    }
  };

  const handleDeleteLocalSaves = async () => {
    const isMounted = () => mountedRef.current;
    if (deletingLocalSaves || isSyncing) return;
    const confirmed = await desktopDeleteLocalSavesDialog(effectiveAsk)();
    if (!confirmed || !isMounted()) return;
    setDeletingLocalSaves(true);
    try {
      await executeDeleteLocalSaves(appId, romId);
    } finally {
      if (isMounted()) setDeletingLocalSaves(false);
    }
  };

  const handleConfirmSlot = async (slotName: string) => {
    if (!slotName || confirmingSlot) return;
    setConfirmingSlot(true);
    try {
      const result = await confirmSlotChoice(romId, slotName, false, null, false);
      if (result.success) {
        if (mountedRef.current) setRawSlotConfirmed(true);
        showToast(`Confirmed save slot '${slotName}'`);
        announceSaveSync(romId);
        await refreshStatusAndSlots();
      } else {
        showToast(result.message || "Failed to confirm save slot");
      }
    } catch (e) {
      logWarn(`DesktopSaveManagementCard: failed to confirm slot: ${e}`);
      showToast("An error occurred while confirming the slot");
    } finally {
      if (mountedRef.current) setConfirmingSlot(false);
    }
  };

  const handleOpenNewSlot = () => {
    detach(
      effectiveAsk<boolean>(false, (resolve) => (
        <DesktopNewSlotDialog
          onDismiss={() => resolve(false)}
          onCreate={async (name) => {
            const trimmed = name.trim();
            if (!trimmed) return null;
            const outcome = await executeSlotSwitch(romId, trimmed, "create");
            if (!outcome.ok) {
              if (outcome.partial) {
                showToast(outcome.message);
                resolve(true);
                return null;
              }
              return outcome.message;
            }
            reportServerReachable(true);
            await applyActivatedSlot(trimmed, "Created");
            resolve(true);
            announceSaveSync(romId);
            await refreshStatusAndSlots();
            return null;
          }}
        />
      )),
    );
  };

  const handleSlotActivated = async (slotName: string, newStatus: SaveStatus) => {
    await applyActivatedSlot(slotName, "Switched to");
    globalThis.dispatchEvent(
      new CustomEvent("romm_data_changed", {
        detail: { type: "save_sync", rom_id: romId, save_status: newStatus },
      }),
    );
    await refreshStatusAndSlots();
  };

  const handleSlotListChanged = () => {
    announceSaveSync(romId);
    detach(refreshStatusAndSlots());
  };

  const handleCopySave = (saveId: number, sourceSlot: string) => {
    detach(
      (async () => {
        const target = await desktopCopySlotDialog(effectiveAsk)(sourceSlot, slotState.availableSlots);
        if (!target || !mountedRef.current) return;
        await executeCopySaveToSlot(romId, saveId, target, desktopSlotConflictDialog(effectiveAsk));
      })(),
    );
  };

  const sortedSlots = sortSaveSlots(slotState.availableSlots, slotState.activeSlot, slotState.activeSlotKnown);
  const displaySlots: SaveSlotSummary[] = filterSaveSlotsForDisplay(sortedSlots, slotState.activeSlot);
  const hasSaves = (saveStatus?.files.length ?? 0) > 0 || slotState.availableSlots.some((s) => s.count > 0);
  const noSaveText = noSaveLine(saveSyncAnswer);
  const notDownloaded = detail.saveSyncEnabled && !detail.installed ? notDownloadedStatement(saveSyncAnswer) : null;
  const notDownloadedHint = notDownloaded?.refusal == null ? null : refusalHint(saveSyncAnswer?.reason);
  const showDeleteLocalSaves = detail.installed && !(detail.saveSyncEnabled ? syncRefused : syncOffRefused);

  return (
    <div
      ref={cardRef}
      data-testid="desktop-saves-card"
      className="tender-desktop-card tender-desktop-saves-card"
      style={CARD_STYLE}
    >
      <CardHeader
        subtitleText={cardSubtitle(detail.saveSyncEnabled, syncRefused, displaySlots.length)}
        hidesHeaderSlotControls={hidesHeaderSlotControls}
        showDeleteLocalSaves={showDeleteLocalSaves}
        isSyncing={isSyncing}
        isOffline={isOffline}
        deletingLocalSaves={deletingLocalSaves}
        onSyncNow={() => detach(handleSyncNow())}
        onDeleteLocalSaves={() => detach(handleDeleteLocalSaves())}
        onOpenNewSlot={handleOpenNewSlot}
      />

      {!detail.saveSyncEnabled && (
        <SyncOffSection syncOffRefused={syncOffRefused} localSavesAnswer={localSavesAnswer} />
      )}

      {detail.saveSyncEnabled && syncRefused && saveSyncAnswer && (
        <RefusalBox testId="desktop-saves-refusal" message={saveSyncAnswer.message} reason={saveSyncAnswer.reason} />
      )}

      {detail.saveSyncEnabled && !syncRefused && (
        <SaveSyncOnSection
          romId={romId}
          isOffline={isOffline}
          strandedVersion={strandedVersion}
          slotConfirmed={slotConfirmed}
          activeSlot={slotState.activeSlot}
          activeSlotKnown={slotState.activeSlotKnown}
          hasSaves={hasSaves}
          confirmingSlot={confirmingSlot}
          notDownloaded={notDownloaded}
          notDownloadedHint={notDownloadedHint}
          slotState={slotState}
          retryProgress={retryProgress}
          displaySlots={displaySlots}
          saveStatus={saveStatus}
          noSaveText={noSaveText}
          saveSyncEnabled={detail.saveSyncEnabled}
          ask={effectiveAsk}
          onConfirmSlot={(slotName) => detach(handleConfirmSlot(slotName))}
          onSlotActivated={handleSlotActivated}
          onSlotListChanged={handleSlotListChanged}
          onCopySave={handleCopySave}
        />
      )}

      {ask ? null : dialogHost.element}
    </div>
  );
};

export const SaveManagementCard: FC<SaveManagementCardProps> = ({ appId, detail, ask }) => {
  const leaseOwner = `desktop-saves-card:${appId}`;
  usePruneLeaseOwner(leaseOwner);
  const isStranded = useStrandedAnswer() !== null;

  if (detail.romId === null || isStranded) return null;

  return <SaveManagementCardContent key={detail.romId} appId={appId} romId={detail.romId} detail={detail} ask={ask} />;
};
