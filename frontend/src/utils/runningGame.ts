/**
 * Shared running game operations:
 * 1. Resuming / activating an already-running Steam game without triggering re-entrant launch gates.
 * 2. Stopping a running game with in-flight protection, liveness verification, and metrics logging.
 */

import { Navigation } from "@decky/ui";
import { debugLog, stopRunningGame } from "../api/backend";
import { isStrandedPanelFailure, strandedAnswer } from "../api/host";
import { detach } from "./detach";
import { readGameRunning } from "./sessionManager";
import { tellStrandedPress } from "./strandedPanelStore";
import { strandedPanelSentence } from "./strandedPanelWording";
import { showToast } from "./toast";

/**
 * Foreground / activate an already-running app via SteamUIStore and Navigation.
 * This is NOT a launch: it does not call RunGame or fire GameActionStart, avoiding
 * re-entrant launch gates and mid-session save sync.
 */
export function activateRunningApp(appId: number, tag = "runningGame"): void {
  // NOSONAR(typescript:S7741) — SteamUIStore is an ambient Steam SP global; the
  // typeof guard keeps a genuinely-absent one from throwing ReferenceError.
  if (typeof SteamUIStore !== "undefined" && SteamUIStore) {
    try {
      SteamUIStore.SetRunningApp(appId);
      if (typeof SteamUIStore.NavigateToRunningApp === "function") {
        SteamUIStore.NavigateToRunningApp();
        detach(debugLog(`${tag}: resumed appId=${appId} via SteamUIStore.NavigateToRunningApp`));
        return;
      }
    } catch (e) {
      detach(debugLog(`${tag}: resume — SteamUIStore threw, falling back to Navigate: ${e}`));
    }
  }

  // Older SteamUI without `NavigateToRunningApp` (API drift), an absent store, or a
  // store whose `SetRunningApp` / `NavigateToRunningApp` threw — navigate to the
  // running-app route directly. When the store was present and `SetRunningApp`
  // succeeded it already selected this app, so the foreground lands on it.
  Navigation.Navigate("/apprunning");
  detach(debugLog(`${tag}: resumed appId=${appId} via Navigation.Navigate`));
}

/** Say that Stop cannot reach the backend of a stranded panel, *sentence* being the answer that backend gave it. */
function tellStrandedStop(sentence: string): void {
  tellStrandedPress("Couldn't stop the game", sentence);
}

export interface StopGameParams {
  appId: number;
  romId: number | null;
  tag?: string;
  stopInFlightRef: { current: boolean };
  onClearOverlay: () => void;
  onSetPending?: (pending: boolean) => void;
  /**
   * The surface's Stop confirm. Required because the backend forces an
   * emulator that does not exit, so anything unsaved is gone.
   */
  confirmModal: () => Promise<boolean>;
}

/**
 * Execute game stop with in-flight guard, liveness verification, modal confirmation,
 * backend RPC call, error toast, and diagnostics logging.
 */
export async function executeStopRunningGame({
  appId,
  romId,
  tag = "stopGame",
  stopInFlightRef,
  onClearOverlay,
  onSetPending,
  confirmModal,
}: StopGameParams): Promise<void> {
  // A stop is already running — do not start a second one.
  if (stopInFlightRef.current) {
    detach(debugLog(`${tag}: Stop ignored for appId=${appId} — a stop is already in flight`));
    return;
  }

  // Stale-overlay self-heal: if nothing is actually running, clear it back to Play
  // without prompting or touching the backend.
  if (!readGameRunning(appId, romId).running) {
    detach(debugLog(`${tag}: Stop on appId=${appId} but nothing is running — clearing stale overlay`));
    onClearOverlay();
    return;
  }

  // A menu opened before the strand still offers Stop Game, and a panel the
  // backend already refused cannot stop anything, so it says so before asking
  // to confirm a stop that cannot happen.
  const stranded = strandedAnswer();
  if (stranded) {
    detach(debugLog(`${tag}: Stop on appId=${appId} from a stranded panel — not stopping`));
    tellStrandedStop(strandedPanelSentence(stranded));
    return;
  }

  // Without the rom id the backend cannot tell this game's instance from any
  // other live one, and stopping "whichever" is exactly the bug this argument
  // exists to fix.
  if (romId == null) {
    detach(debugLog(`${tag}: Stop on appId=${appId} but the rom id is not resolved yet — not stopping`));
    showToast("Couldn't stop the game — still loading its details");
    return;
  }

  if (!(await confirmModal())) {
    detach(debugLog(`${tag}: Stop cancelled for appId=${appId}`));
    return;
  }

  stopInFlightRef.current = true;
  onSetPending?.(true);
  try {
    const result = await stopRunningGame(romId);
    if (result.success || result.reason === "not_running") {
      detach(
        debugLog(
          `${tag}: stop_running_game for appId=${appId} — success=${result.success} ` +
            `reason=${result.reason ?? "none"} stopped=${result.stopped ?? 0} forced=${result.force_killed ?? 0}`,
        ),
      );
      onClearOverlay();
      return;
    }
    detach(debugLog(`${tag}: stop_running_game refused for appId=${appId} — reason=${result.reason ?? "none"}`));
    showToast(result.message || "Couldn't stop the game");
  } catch (e) {
    detach(debugLog(`${tag}: stop_running_game threw for appId=${appId}: ${e}`));
    if (isStrandedPanelFailure(e)) {
      // The failure's message is the answer the backend gave this panel.
      tellStrandedStop(e.message);
    } else {
      showToast("Couldn't stop the game");
    }
  } finally {
    stopInFlightRef.current = false;
    onSetPending?.(false);
  }
}
