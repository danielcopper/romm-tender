/**
 * Resume and Stop for a game that is already running.
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
 * Bring an already-running game to the foreground instead of launching it
 * (#1313). Foregrounding is pure UI focus navigation the way Steam's own
 * gamescope "Resume Game" does it — `SteamUIStore.SetRunningApp(appId)` +
 * `NavigateToRunningApp()` — NOT a launch: it fires no `GameActionStart` (so the
 * launch interceptor never re-enters) and shows no "already running" dialog, so
 * the pre-launch sync funnel never runs mid-session (which would upload the save
 * while the emulator holds the file open). `RaiseWindowForGame` (the prior
 * approach) is a DESKTOP-overlay call that silently no-ops in gamescope Game Mode
 * — it reports Success but does nothing — so it is not used here.
 */
export function activateRunningApp(appId: number, tag = "runningGame"): void {
  // NOSONAR(typescript:S7741) — SteamUIStore is an ambient Steam SP global; the
  // typeof guard keeps a genuinely-absent one from throwing ReferenceError.
  if (typeof SteamUIStore !== "undefined" && SteamUIStore) {
    // A present-but-broken store is the exact failure class Resume was born
    // from (RaiseWindowForGame reporting Success while doing nothing) — a
    // throwing `SetRunningApp` / `NavigateToRunningApp` getter must NOT strand the
    // user with no foreground and no backstop. Any throw is swallowed and falls
    // through to the route nav below, mirroring how runningApps.ts wraps every
    // Steam-global access in try/catch.
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
  // succeeded it already selected this app, so the foreground lands on it (the
  // decky-rocketjump fallback path).
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
  /** Held while a stop runs; a press that finds it set does nothing. */
  stopInFlightRef: { current: boolean };
  /** Bring the running overlay down — called only once nothing of the game runs. */
  onClearOverlay: () => void;
  onSetPending?: (pending: boolean) => void;
  /**
   * The surface's Stop confirm. Required because the backend forces an
   * emulator that does not exit, so anything unsaved is gone.
   */
  confirmModal: () => Promise<boolean>;
}

/** Stop the running game *romId* once the user confirms. */
export async function executeStopRunningGame({
  appId,
  romId,
  tag = "stopGame",
  stopInFlightRef,
  onClearOverlay,
  onSetPending,
  confirmModal,
}: StopGameParams): Promise<void> {
  // A stop is already running — do not start a second one. The disabled menu
  // item makes this hard to reach; this is the guard for the paths that
  // bypass the render (a menu opened before the flag flipped, a double-fire
  // within one frame).
  if (stopInFlightRef.current) {
    detach(debugLog(`${tag}: Stop ignored for appId=${appId} — a stop is already in flight`));
    return;
  }

  // Stale-overlay self-heal, mirroring the Resume press: if nothing is
  // actually running, the overlay is stale — clear it back to Play without
  // prompting or touching the backend.
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
  // exists to fix. The detail lookup that fills `romId` normally lands long
  // before a running overlay can be pressed; if it somehow has not, say so and
  // leave the overlay up so Resume stays reachable.
  if (romId == null) {
    detach(debugLog(`${tag}: Stop on appId=${appId} but the rom id is not resolved yet — not stopping`));
    showToast("Couldn't stop the game — still loading its details");
    return;
  }

  if (!(await confirmModal())) {
    detach(debugLog(`${tag}: Stop cancelled for appId=${appId}`));
    return;
  }

  // Claimed only once the user has actually confirmed — an abandoned modal
  // must not leave Stop Game stuck reading "Stopping...".
  stopInFlightRef.current = true;
  onSetPending?.(true);
  try {
    const result = await stopRunningGame(romId);
    if (result.success || result.reason === "not_running") {
      // "not_running" is the same stale-overlay case caught one layer down:
      // the backend found nothing of RetroDECK's alive. Either way the game is
      // not running now, so the overlay must come down.
      detach(
        debugLog(
          `${tag}: stop_running_game for appId=${appId} — success=${result.success} ` +
            `reason=${result.reason ?? "none"} stopped=${result.stopped ?? 0} forced=${result.force_killed ?? 0}`,
        ),
      );
      onClearOverlay();
      return;
    }
    // Every other failure leaves the overlay UP on purpose. That includes
    // "game_not_running": RetroDECK is alive but the backend could not tie any
    // of its instances to this ROM, so it signalled nothing — the game may
    // well still be running, and Resume has to stay reachable either way.
    detach(debugLog(`${tag}: stop_running_game refused for appId=${appId} — reason=${result.reason ?? "none"}`));
    showToast(result.message || "Couldn't stop the game");
  } catch (e) {
    // The overlay deliberately stays up: the call never reached a verdict, so
    // the game may well still be running and Resume must stay reachable.
    detach(debugLog(`${tag}: stop_running_game threw for appId=${appId}: ${e}`));
    if (isStrandedPanelFailure(e)) {
      // The failure's message is the answer the backend gave this panel.
      tellStrandedStop(e.message);
    } else {
      showToast("Couldn't stop the game");
    }
  } finally {
    // Released on every path, so a failed stop can be retried deliberately
    // (the backend, not this flag, is what makes a retry safe).
    stopInFlightRef.current = false;
    onSetPending?.(false);
  }
}
