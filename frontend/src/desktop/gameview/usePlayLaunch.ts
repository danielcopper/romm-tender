/**
 * The desktop Play button's presses: Play through the shared launch gate
 * (`utils/launchGateOps.ts`, `utils/launchVerdict.ts`) asked through the desktop
 * dialogs, Stop and Resolve Conflict. `bigpicture/CustomPlayButton`'s handlers
 * are the gamepad drawing of the same presses.
 */

import { useRef, type Dispatch, type SetStateAction } from "react";
import { debugLog } from "../../api/backend";
import { desktopLaunchPrompts, desktopResolveConflicts, desktopStopGameDialog } from "./dialogs/desktopDialogs";
import { executeStopRunningGame } from "../../utils/runningGame";
import { resolveKnownConflicts } from "../../utils/saveConflictFlow";
import { noteAppRom, readGameRunning } from "../../utils/sessionManager";
import { prepareStart } from "../../utils/launchOptionsReconcile";
import {
  capturePruneLeaseAdmission,
  isPruneLeaseAdmissionCurrent,
  type PruneLeaseAdmission,
} from "../../utils/pruneLease";
import { detach } from "../../utils/detach";
import { markLaunchSkipped } from "../../utils/launchGate";
import { ensureTrackingConfiguredSilently, makeLaunchGateOps } from "../../utils/launchGateOps";
import { runGateLoop } from "../../utils/launchVerdict";
import { recheckStart, refreshSaveStatus } from "../../utils/gameDetailStore";
import { startFailedSentence } from "../../utils/gameLaunch";
import { showToast } from "../../utils/toast";
import type { AskDialog } from "./dialogs/askDialog";

const TAG = "DesktopPlayButton";

/** What a press puts the button on until it settles. */
export type PressState = "checking" | "syncing" | "launching";

interface UsePlayLaunchOptions {
  appId: number;
  romId: number | null;
  pressState: PressState | null;
  /** Whether the page's check of whether the game can start went wrong. */
  startCheckFailed: boolean;
  ask: AskDialog;
  leaseOwner: string;
  setPressState: Dispatch<SetStateAction<PressState | null>>;
  /** Bring the running state down when the game turns out not to be running. */
  clearSessionRunning: () => void;
  holdVerdict: (state: "play" | "conflict") => void;
  setStopPending: (pending: boolean) => void;
}

interface UsePlayLaunchResult {
  handlePlayClick: () => Promise<void>;
  handleResolveConflictClick: () => Promise<void>;
  handleStopClick: () => Promise<void>;
}

export function usePlayLaunch({
  appId,
  romId,
  pressState,
  startCheckFailed,
  ask,
  leaseOwner,
  setPressState,
  clearSessionRunning,
  holdVerdict,
  setStopPending,
}: UsePlayLaunchOptions): UsePlayLaunchResult {
  const stopInFlightRef = useRef(false);

  // Every start is prepared first; without the hand-over nothing starts, so a
  // preparation that does not come about refuses the start with its sentence
  // (#2221 D15, D19). Bringing a running game to the front prepares nothing.
  const dispatchLaunch = async (
    rid: number,
    gameId: string,
    admission: PruneLeaseAdmission,
    { prepare = true }: { prepare?: boolean } = {},
  ) => {
    if (!isPruneLeaseAdmissionCurrent(admission)) {
      setPressState(null);
      return;
    }
    setPressState("launching");
    if (prepare) {
      const prepared = await prepareStart(rid, appId, TAG, admission);
      if (prepared.status === "cancelled") return;
      if (prepared.status === "refused") {
        showToast(prepared.sentence);
        setPressState(null);
        return;
      }
    }
    noteAppRom(appId, rid);
    // So this start does not reach the launch watcher, which would gate it again.
    markLaunchSkipped(appId);
    SteamClient.Apps.RunGame(gameId, "", -1, 100);
  };

  const handlePlayClick = async () => {
    if (pressState !== null || romId === null) return;
    const gameId = appStore.GetAppOverviewByAppID(appId)?.GetGameID?.() ?? String(appId);
    const admission = capturePruneLeaseAdmission(leaseOwner);

    // A start of a game already running goes to Steam without the gate, whose
    // sync would upload the save while the emulator holds it open.
    const running = readGameRunning(appId, romId);
    if (running.running) {
      detach(debugLog(`${TAG}: appId=${appId} already running — skipping pre-launch sync [${running.diagnostics}]`));
      await dispatchLaunch(romId, gameId, admission, { prepare: false });
      return;
    }
    detach(debugLog(`${TAG}: appId=${appId} not running — running the launch gate [${running.diagnostics}]`));

    setPressState("checking");
    try {
      // What asking again answers: docs/architecture/backend-architecture.md,
      // "Launch-target validation".
      if (startCheckFailed) {
        const refusal = await recheckStart(appId, romId, false);
        if (refusal !== null) {
          showToast(refusal);
          setPressState(null);
          return;
        }
      }
      const prompts = desktopLaunchPrompts(ask);
      const ops = makeLaunchGateOps(romId, {
        tag: TAG,
        toastSyncResult: true,
        onSyncStart: () => setPressState("syncing"),
        ensureTrackingConfigured: async () => {
          await ensureTrackingConfiguredSilently(romId, TAG);
          return "proceed";
        },
        confirmCoreChange: prompts.confirmCoreChange,
        confirmUncheckedStartingSource: prompts.confirmUncheckedSource,
        askSyncWait: prompts.askSyncWait,
      });
      // A migration block needs no message: the page shows it in place of its cards.
      await runGateLoop(appId, romId, ops, {
        prompts,
        launch: () => dispatchLaunch(romId, gameId, admission),
        onDeclined: () => setPressState(null),
        onMigrationBlocked: () => setPressState(null),
        onConflictCancelled: () => {
          setPressState(null);
          holdVerdict("conflict");
          detach(refreshSaveStatus(appId));
        },
        onRetry: () => setPressState("checking"),
      });
    } catch (e) {
      detach(debugLog(`${TAG}: Play's launch check threw — resetting to play: ${e}`));
      if (startCheckFailed) showToast(startFailedSentence(e));
      setPressState(null);
    }
  };

  const handleResolveConflictClick = async () => {
    if (romId === null) return;
    setPressState("syncing");
    const outcome = await resolveKnownConflicts(romId, desktopResolveConflicts(ask), TAG);
    setPressState(null);
    holdVerdict(outcome === "resolved" ? "play" : "conflict");
  };

  const handleStopClick = async () => {
    await executeStopRunningGame({
      appId,
      romId,
      tag: TAG,
      stopInFlightRef,
      onClearOverlay: () => {
        clearSessionRunning();
        setPressState((prev) => (prev === "launching" ? null : prev));
      },
      onSetPending: setStopPending,
      confirmModal: desktopStopGameDialog(ask),
    });
  };

  return { handlePlayClick, handleResolveConflictClick, handleStopClick };
}
