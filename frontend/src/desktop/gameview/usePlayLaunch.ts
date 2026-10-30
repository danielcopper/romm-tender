/**
 * The desktop Play button's presses: Play through the shared launch gate
 * (`utils/launchGateOps.ts`, `utils/launchVerdict.ts`) asked through the desktop
 * dialogs, Resume, Stop and Resolve Conflict. `bigpicture/CustomPlayButton`'s
 * handlers are the gamepad drawing of the same presses.
 */

import { useRef } from "react";
import { debugLog } from "../../api/backend";
import { strandedAnswer } from "../../api/host";
import { desktopLaunchPrompts, desktopResolveConflicts, desktopStopGameDialog } from "./dialogs/desktopDialogs";
import { activateRunningApp, executeStopRunningGame } from "../../utils/runningGame";
import { resolveKnownConflicts } from "../../utils/saveConflictFlow";
import { noteAppRom, readGameRunning } from "../../utils/sessionManager";
import { reconfirmLaunchOptions } from "../../utils/launchOptionsReconcile";
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
  /** Whether the page's check of whether the game can start is still running. */
  startChecking: boolean;
  /** The sentence PLAY is refused with; `null` where nothing refuses it. */
  playRefusal: string | null;
  ask: AskDialog;
  leaseOwner: string;
  setPressState: (state: PressState | null) => void;
  /** Bring the running state down when the game turns out not to be running. */
  clearSessionRunning: () => void;
  holdVerdict: (state: "play" | "conflict") => void;
  setStopPending: (pending: boolean) => void;
}

interface UsePlayLaunchResult {
  handlePlayClick: () => Promise<void>;
  handleResumeClick: () => Promise<void>;
  handleResolveConflictClick: () => Promise<void>;
  handleStopClick: () => Promise<void>;
}

export function usePlayLaunch({
  appId,
  romId,
  pressState,
  startCheckFailed,
  startChecking,
  playRefusal,
  ask,
  leaseOwner,
  setPressState,
  clearSessionRunning,
  holdVerdict,
  setStopPending,
}: UsePlayLaunchOptions): UsePlayLaunchResult {
  const stopInFlightRef = useRef(false);

  // Why a start after a check that got no answer in time skips the re-confirm:
  // docs/architecture/save-file-sync-architecture.md, "Pre-launch sync".
  const dispatchLaunch = async (
    rid: number,
    gameId: string,
    admission: PruneLeaseAdmission,
    { skipReconfirm = false }: { skipReconfirm?: boolean } = {},
  ) => {
    if (!isPruneLeaseAdmissionCurrent(admission)) {
      setPressState(null);
      return;
    }
    setPressState("launching");
    if (!skipReconfirm) {
      const reconfirm = await reconfirmLaunchOptions(rid, appId, TAG, admission);
      if (reconfirm.status === "cancelled") return;
      if (reconfirm.status === "timeout") {
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
    if (pressState !== null) return;
    await startGame();
  };

  const startGame = async () => {
    if (romId === null) return;
    const gameId = appStore.GetAppOverviewByAppID(appId)?.GetGameID?.() ?? String(appId);
    const admission = capturePruneLeaseAdmission(leaseOwner);

    // A start of a game already running goes to Steam without the gate, whose
    // sync would upload the save while the emulator holds it open.
    const running = readGameRunning(appId, romId);
    if (running.running) {
      detach(debugLog(`${TAG}: appId=${appId} already running — skipping pre-launch sync [${running.diagnostics}]`));
      await dispatchLaunch(romId, gameId, admission);
      return;
    }

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
        ensureTrackingConfigured: () => ensureTrackingConfiguredSilently(romId, TAG),
        confirmCoreChange: prompts.confirmCoreChange,
        confirmUncheckedStartingSource: prompts.confirmUncheckedSource,
        askSyncWait: prompts.askSyncWait,
      });
      // A migration block needs no message: the page shows it in place of its cards.
      await runGateLoop(appId, romId, ops, {
        prompts,
        launch: (options) => dispatchLaunch(romId, gameId, admission, options),
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

  const handleResumeClick = async () => {
    // The running state can outlive a session whose end never reached this
    // button, and so can a LAUNCHING held under it, during which PLAY takes no
    // press. With nothing running, the running state comes down and the press
    // does what PLAY's would on the page under it, unless the panel is stranded.
    if (!readGameRunning(appId, romId).running) {
      clearSessionRunning();
      if (strandedAnswer()) {
        detach(debugLog(`${TAG}: Resume on appId=${appId} from a stranded panel but nothing is running`));
        return;
      }
      if (startChecking) {
        detach(debugLog(`${TAG}: Resume on appId=${appId} but nothing is running, and the page is still checking`));
        return;
      }
      if (playRefusal !== null) {
        showToast(playRefusal);
        return;
      }
      detach(debugLog(`${TAG}: Resume on appId=${appId} but nothing is running — self-healing to launch`));
      await startGame();
      return;
    }
    activateRunningApp(appId, TAG);
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
        setPressState(null);
      },
      onSetPending: setStopPending,
      confirmModal: desktopStopGameDialog(ask),
    });
  };

  return { handlePlayClick, handleResumeClick, handleResolveConflictClick, handleStopClick };
}
