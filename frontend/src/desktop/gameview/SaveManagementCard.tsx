/**
 * SaveManagementCard — save slot browser and manager for the Desktop game view.
 *
 * Parity with the game-detail save sync flows:
 * - Returns `null` and issues zero reads while `detail.romId === null` or the
 *   backend reports a stranded panel (`useStrandedAnswer`).
 * - Save sync off (`!detail.saveSyncEnabled`): reads `getLocalSaveFiles(romId)`
 *   to list this device's save files or explain refusal / not-downloaded state.
 * - Save sync on: checks `getSaveSyncAvailability(romId)` (`refusesDownloadedGame`,
 *   `notDownloadedStatement`, `noSaveLine`), shows live retry progress while loading
 *   (`#1345`) with a known-offline fast path + auto-reload on reconnect, renders
 *   unslotted local files (`#1747`) and stale last-known snapshots (`#1755`) apart
 *   from live slot panels, inline slot-tracking confirmation, stranded-version
 *   warning, manual sync, delete local saves, and copy/restore/new-slot dialogs.
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
import type { LastKnownSlots, LocalSaveFiles, SaveSlotSummary, SaveStatus, SaveSyncAvailability } from "../../types";
import {
  beginServerLoad,
  reportServerReachable,
  setServerRetryProgress,
  settleServerLoad,
  useRommConnectionState,
  useServerRetryProgress,
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

export interface SaveManagementCardProps {
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

function renderLastKnownSection(lastKnown: LastKnownSlots) {
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
                        fontSize: "10px",
                        fontWeight: 700,
                        backgroundColor: "#5ba32b",
                        color: "#ffffff",
                        padding: "1px 6px",
                        borderRadius: "2px",
                        letterSpacing: "0.04em",
                        textTransform: "uppercase",
                      }}
                    >
                      ACTIVE
                    </span>
                  )}
                  <span
                    style={{
                      fontSize: "10px",
                      fontWeight: 600,
                      backgroundColor: "rgba(255, 255, 255, 0.08)",
                      color: MUTED_COLOR,
                      padding: "1px 6px",
                      borderRadius: "2px",
                      letterSpacing: "0.04em",
                      textTransform: "uppercase",
                    }}
                  >
                    {slot.source === "local" ? "LOCAL" : "SERVER"}
                  </span>
                  <span
                    style={{
                      fontSize: "10px",
                      fontWeight: 600,
                      backgroundColor: "rgba(255, 255, 255, 0.06)",
                      color: MUTED_COLOR,
                      padding: "1px 6px",
                      borderRadius: "2px",
                      letterSpacing: "0.04em",
                      textTransform: "uppercase",
                    }}
                  >
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
    if (result.success) {
      reportServerReachable(true);
    } else if (result.reason === "server_unreachable") {
      reportServerReachable(false);
    }
    applyLoadSlotsResult<SlotState>(result, setSlotState, slotsLoadedRef, (msg) => detach(debugLog(msg)));
  } catch (e) {
    detach(debugLog(`SaveManagementCard: failed to load save slots: ${e}`));
    if (!isCancelled()) {
      run.settled = true;
      slotsLoadedRef.current = false;
      setSlotState((prev) => ({ ...prev, slotsLoading: false }));
    }
  } finally {
    settleServerLoad(load);
  }
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

  const [slotState, setSlotState] = useState<SlotState>(INITIAL_SLOT_STATE);
  const slotsLoadedRef = useRef(false);
  const [rawSlotConfirmed, setRawSlotConfirmed] = useState<boolean | null>(null);
  const [confirmingSlot, setConfirmingSlot] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [deletingLocalSaves, setDeletingLocalSaves] = useState(false);
  const [rawStrandedVersion, setRawStrandedVersion] = useState<StrandedVersion | null>(null);

  const [rawLocalSavesAnswer, setRawLocalSavesAnswer] = useState<LocalSaveFiles | null>(null);
  const [rawSaveSyncAnswer, setRawSaveSyncAnswer] = useState<SaveSyncAvailability | null>(null);

  const localSavesAnswer = detail.saveSyncEnabled ? null : rawLocalSavesAnswer;
  const saveSyncAnswer = detail.saveSyncEnabled ? rawSaveSyncAnswer : null;
  const saveStatus = detail.saveStatus;
  const syncRefused = detail.saveSyncEnabled && refusesDownloadedGame(saveSyncAnswer);
  const slotConfirmed = !detail.saveSyncEnabled || syncRefused ? true : rawSlotConfirmed;
  const strandedVersion = detail.saveSyncEnabled && !syncRefused ? rawStrandedVersion : null;
  const syncOffRefused =
    !detail.saveSyncEnabled && localSavesAnswer !== null && !localSavesAnswer.available && localSavesAnswer.installed;
  const hidesHeaderSlotControls = !detail.saveSyncEnabled || syncRefused;

  // Scroll into view when PLAY/badges or launch gate routes the user to Saves.
  useEffect(() => {
    const onTabSwitch = (e: Event) => {
      if ((e as CustomEvent<{ tab?: string } | null>).detail?.tab === "saves") {
        cardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    };
    globalThis.addEventListener("romm_tab_switch", onTabSwitch);
    return () => globalThis.removeEventListener("romm_tab_switch", onTabSwitch);
  }, []);

  // Save sync off: read local save files without touching RomM.
  useEffect(() => {
    if (detail.saveSyncEnabled) return;
    let cancelled = false;
    const isCancelled = () => cancelled;
    let latestSeq = 0;
    const read = async () => {
      const seq = ++latestSeq;
      try {
        const answer = await getLocalSaveFiles(romId);
        if (!isCancelled() && seq === latestSeq) setRawLocalSavesAnswer(answer);
      } catch (e) {
        logWarn(`DesktopSaveManagementCard: local saves read for romId=${romId} failed: ${e}`);
        if (!isCancelled() && seq === latestSeq) setRawLocalSavesAnswer(null);
      }
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
  }, [romId, detail.saveSyncEnabled]);

  // Save sync on: read availability so unsupported/non-downloaded states render honestly.
  useEffect(() => {
    if (!detail.saveSyncEnabled) return;
    let cancelled = false;
    const isCancelled = () => cancelled;
    let latestSeq = 0;
    const read = async () => {
      const seq = ++latestSeq;
      try {
        const answer = await getSaveSyncAvailability(romId);
        if (!isCancelled() && seq === latestSeq) setRawSaveSyncAnswer(answer);
      } catch (e) {
        logWarn(`DesktopSaveManagementCard: save sync availability read for romId=${romId} failed: ${e}`);
        if (!isCancelled() && seq === latestSeq) setRawSaveSyncAnswer(null);
      }
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
  }, [romId, detail.saveSyncEnabled]);

  const refreshSlots = useCallback(async () => {
    if (!detail.saveSyncEnabled) return;
    try {
      const slotResult = await getSaveSlots(romId);
      if (!mountedRef.current) return;
      if (slotResult.success) {
        reportServerReachable(true);
      } else if (slotResult.reason === "server_unreachable") {
        reportServerReachable(false);
      }
      applyRefreshSlotResult<SlotState>(slotResult, setSlotState);
    } catch (e) {
      detach(debugLog(`SaveManagementCard: failed to refresh save slots: ${e}`));
    }
    try {
      const tracking = await isSaveTrackingConfigured(romId);
      if (mountedRef.current) setRawSlotConfirmed(tracking.configured);
    } catch (e) {
      detach(debugLog(`SaveManagementCard: failed to check tracking: ${e}`));
      if (mountedRef.current) setRawSlotConfirmed(true);
    }
  }, [romId, detail.saveSyncEnabled]);

  // Initial slot load + known-offline fast path + reconnect reload (#1345).
  useEffect(() => {
    if (!detail.saveSyncEnabled || syncRefused) {
      slotsLoadedRef.current = false;
      return;
    }
    let cancelled = false;
    const isCancelled = () => cancelled;

    const readTracking = async () => {
      try {
        const tracking = await isSaveTrackingConfigured(romId);
        if (!isCancelled()) setRawSlotConfirmed(tracking.configured);
      } catch (e) {
        detach(debugLog(`SaveManagementCard: failed to check tracking: ${e}`));
        if (!isCancelled()) setRawSlotConfirmed(true);
      }
    };

    if (slotsLoadedRef.current) return;

    if (isOffline) {
      // Known-offline fast path (#1345): never block on the connecting spinner
      // or claim the shared retry frame while offline; still read any cached
      // `last_known` snapshot and leave `slotsLoadedRef.current = false` so
      // reconnect reloads live data automatically.
      detach(readTracking());
      detach(
        (async () => {
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
            detach(debugLog(`SaveManagementCard: offline slot snapshot read failed: ${e}`));
          }
        })(),
      );
      return () => {
        cancelled = true;
      };
    }

    slotsLoadedRef.current = true;
    const run: SlotLoadRun = { settled: false };
    detach(readTracking());
    detach(loadDesktopSlots(romId, run, isCancelled, slotsLoadedRef, setSlotState));

    return () => {
      cancelled = true;
      if (!run.settled) {
        slotsLoadedRef.current = false;
        setSlotState((prev) => (prev.slotsLoading ? { ...prev, slotsLoading: false } : prev));
      }
    };
  }, [romId, detail.saveSyncEnabled, syncRefused, isOffline]);

  // Stranded sibling version check (#1298).
  useEffect(() => {
    if (!detail.saveSyncEnabled || syncRefused) return;
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
  }, [appId, romId, detail.saveSyncEnabled, syncRefused]);

  // Refresh slot list and status when `save_sync` fires for this ROM.
  useEffect(() => {
    if (!detail.saveSyncEnabled || syncRefused) return;
    const onDataChanged = ({ detail: ev }: WindowEventMap["romm_data_changed"]) => {
      if (ev.type !== "save_sync") return;
      if (ev.rom_id !== undefined && ev.rom_id !== romId) return;
      detach(refreshSaveStatus(appId));
      detach(refreshSlots());
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => globalThis.removeEventListener("romm_data_changed", onDataChanged);
  }, [appId, romId, detail.saveSyncEnabled, syncRefused, refreshSlots]);

  const autoConfirmIfNeeded = async (slotName: string): Promise<boolean> => {
    if (!detail.saveSyncEnabled || slotConfirmed !== false) return false;
    try {
      const confirmResult = await confirmSlotChoice(romId, slotName, false, null, false);
      if (confirmResult.success) {
        if (mountedRef.current) setRawSlotConfirmed(true);
        return true;
      }
    } catch (e) {
      detach(debugLog(`SaveManagementCard: auto-confirm for '${slotName}' failed: ${e}`));
    }
    return false;
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
        await refreshSaveStatus(appId);
        await refreshSlots();
      } else {
        showToast(result.message || "Failed to confirm save slot");
      }
    } catch (e) {
      detach(debugLog(`SaveManagementCard: failed to confirm slot: ${e}`));
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
            const confirmedNow = await autoConfirmIfNeeded(trimmed);
            showToast(confirmedNow ? `Created and confirmed slot '${trimmed}'` : `Switched to slot '${trimmed}'`);
            if (mountedRef.current) {
              setSlotState((prev) => ({
                ...prev,
                activeSlot: trimmed,
                activeSlotKnown: true,
                lastKnownSlots: null,
              }));
            }
            resolve(true);
            announceSaveSync(romId);
            await refreshSaveStatus(appId);
            await refreshSlots();
            return null;
          }}
        />
      )),
    );
  };

  const handleSlotActivated = async (slotName: string, newStatus: SaveStatus) => {
    const confirmedNow = await autoConfirmIfNeeded(slotName);
    showToast(confirmedNow ? `Switched to and confirmed slot '${slotName}'` : `Switched to slot '${slotName}'`);
    if (mountedRef.current) {
      setSlotState((prev) => ({
        ...prev,
        activeSlot: slotName,
        activeSlotKnown: true,
        lastKnownSlots: null,
      }));
    }
    globalThis.dispatchEvent(
      new CustomEvent("romm_data_changed", {
        detail: { type: "save_sync", rom_id: romId, save_status: newStatus },
      }),
    );
    await refreshSaveStatus(appId);
    await refreshSlots();
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
  const activeSlot = slotState.activeSlot;
  const activeSlotKnown = slotState.activeSlotKnown;
  const hasSaves = (saveStatus?.files.length ?? 0) > 0 || slotState.availableSlots.some((s) => s.count > 0);
  const noSaveText = noSaveLine(saveSyncAnswer);
  const notDownloaded = detail.saveSyncEnabled && !detail.installed ? notDownloadedStatement(saveSyncAnswer) : null;
  const notDownloadedHint = notDownloaded?.refusal == null ? null : refusalHint(saveSyncAnswer?.reason);

  const showDeleteLocalSaves = detail.installed && !(detail.saveSyncEnabled ? syncRefused : syncOffRefused);

  let subtitleText = "save sync off";
  if (detail.saveSyncEnabled) {
    subtitleText = syncRefused
      ? "save sync unavailable"
      : `${displaySlots.length} slot${displaySlots.length === 1 ? "" : "s"} · save sync on`;
  }

  return (
    <div
      ref={cardRef}
      data-testid="desktop-saves-card"
      className="tender-desktop-card tender-desktop-saves-card"
      style={CARD_STYLE}
    >
      {/* Card Header */}
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
            <button
              type="button"
              style={{
                ...HEADER_BUTTON_STYLE,
                opacity: isSyncing || isOffline || deletingLocalSaves ? 0.5 : 1,
                cursor: isSyncing || isOffline || deletingLocalSaves ? "not-allowed" : "pointer",
              }}
              disabled={isSyncing || isOffline || deletingLocalSaves}
              onClick={() => detach(handleSyncNow())}
            >
              {isSyncing && <SvgThrobber style={{ width: "12px", height: "12px" }} />}
              <span>{isSyncing ? "Syncing…" : "Sync now"}</span>
            </button>
          )}
          {showDeleteLocalSaves && (
            <button
              type="button"
              style={{
                ...HEADER_BUTTON_STYLE,
                borderColor: "rgba(217, 65, 38, 0.4)",
                color: "#ff6b6b",
                opacity: deletingLocalSaves || isSyncing ? 0.5 : 1,
                cursor: deletingLocalSaves || isSyncing ? "not-allowed" : "pointer",
              }}
              disabled={deletingLocalSaves || isSyncing}
              onClick={() => detach(handleDeleteLocalSaves())}
            >
              {deletingLocalSaves && <SvgThrobber style={{ width: "12px", height: "12px" }} />}
              <span>{deletingLocalSaves ? "Deleting…" : "Delete Local Saves"}</span>
            </button>
          )}
          {!hidesHeaderSlotControls && (
            <button
              type="button"
              style={{
                ...HEADER_BUTTON_STYLE,
                opacity: isOffline || isSyncing ? 0.5 : 1,
                cursor: isOffline || isSyncing ? "not-allowed" : "pointer",
              }}
              disabled={isOffline || isSyncing}
              onClick={handleOpenNewSlot}
            >
              + New Slot
            </button>
          )}
        </div>
      </div>

      {/* Save Sync Off View */}
      {!detail.saveSyncEnabled && (
        <div data-testid="desktop-saves-sync-off">
          <div style={{ fontSize: "12px", color: MUTED_COLOR, marginBottom: "8px" }}>{SAVE_SYNC_OFF}</div>
          {syncOffRefused ? (
            <div style={REFUSAL_BOX_STYLE}>
              <div style={{ fontSize: "12px", color: "#d4a72c" }}>{localSavesAnswer.message ?? ""}</div>
              {refusalHint(localSavesAnswer.reason) && (
                <div style={{ fontSize: "12px", color: "#dfe3e6", marginTop: "6px" }}>
                  {refusalHint(localSavesAnswer.reason)}
                </div>
              )}
            </div>
          ) : localSavesAnswer ? (
            localSavesAnswer.files.length > 0 ? (
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
                    <div style={{ fontSize: "12px", color: MUTED_COLOR }}>
                      {`Last changed: ${formatTimestamp(f.modified_at)}`}
                    </div>
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
            ) : (
              <div style={{ fontSize: "12px", color: MUTED_COLOR }}>
                {localSavesAnswer.installed ? NO_SAVE_ON_DEVICE : NOT_DOWNLOADED_NO_SAVE}
              </div>
            )
          ) : null}
        </div>
      )}

      {/* Save Sync On: Refusal View */}
      {detail.saveSyncEnabled && syncRefused && saveSyncAnswer && (
        <div data-testid="desktop-saves-refusal" style={REFUSAL_BOX_STYLE}>
          <div style={{ fontSize: "12px", color: "#d4a72c" }}>{saveSyncAnswer.message ?? ""}</div>
          {refusalHint(saveSyncAnswer.reason) && (
            <div style={{ fontSize: "12px", color: "#dfe3e6", marginTop: "6px" }}>
              {refusalHint(saveSyncAnswer.reason)}
            </div>
          )}
        </div>
      )}

      {/* Save Sync On: Normal View */}
      {detail.saveSyncEnabled && !syncRefused && (
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
              RomM is offline — slot switching is disabled until the server is reachable. This prevents save sync
              conflicts.
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
                  opacity: isOffline || confirmingSlot ? 0.5 : 1,
                  cursor: isOffline || confirmingSlot ? "not-allowed" : "pointer",
                }}
                disabled={isOffline || confirmingSlot}
                onClick={() => detach(handleConfirmSlot(activeSlot))}
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

          {/* Connecting indicator (#1345) */}
          {slotState.slotsLoading && slotState.availableSlots.length === 0 ? (
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
              {/* Unslotted files (#1747) */}
              {(!activeSlotKnown || activeSlot === null) && (
                <div data-testid="desktop-unslotted-files">
                  {saveStatus && saveStatus.files.length > 0 ? (
                    <div style={{ marginBottom: "12px" }}>
                      {saveStatus.files.map((file) => {
                        const conflict = (saveStatus.conflicts ?? []).find((c) => c.filename === file.filename);
                        return renderDesktopSaveFileRow(file, conflict, saveStatus.last_sync_check_at);
                      })}
                    </div>
                  ) : (
                    (() => {
                      const line = emptySlotLine(saveStatus ?? null, noSaveText);
                      return line === null ? null : (
                        <div
                          style={{ fontSize: "13px", color: MUTED_COLOR, fontStyle: "italic", marginBottom: "12px" }}
                        >
                          {line}
                        </div>
                      );
                    })()
                  )}
                </div>
              )}

              {/* Read-only stale snapshot when no live answer landed (#1755) */}
              {!activeSlotKnown && slotState.lastKnownSlots && renderLastKnownSection(slotState.lastKnownSlots)}

              {/* Live Slot Panels */}
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
                        saveSyncEnabled={detail.saveSyncEnabled}
                        confirmingSlot={confirmingSlot}
                        ask={effectiveAsk}
                        onConfirmSlot={(slotName) => detach(handleConfirmSlot(slotName))}
                        onSlotActivated={handleSlotActivated}
                        onSlotDeleted={() => {
                          announceSaveSync(romId);
                          detach(refreshSaveStatus(appId));
                          detach(refreshSlots());
                        }}
                        onVersionRestored={() => {
                          announceSaveSync(romId);
                          detach(refreshSaveStatus(appId));
                          detach(refreshSlots());
                        }}
                        onCopySave={handleCopySave}
                      />
                    );
                  })}
                </div>
              )}
            </>
          )}
        </>
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
