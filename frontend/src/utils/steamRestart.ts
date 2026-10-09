/**
 * {@link restartSteam} restarts the Steam **client**. Steam rebuilds its JS
 * context, so the frontend starts over; Tender's backend keeps running
 * (`services/library/_state.py` states the same fact from the backend side).
 */

import { showToast } from "./toast";
import { isAnyAppHolding } from "./runningApps";

/**
 * Restart the Steam client — the deterministic "free memory" action. A full
 * client restart resets the renderer's per-session heap budget.
 *
 * Fire-and-forget: `StartRestart` tears the client down and back up. Refused
 * while any listed app is starting, running or exiting, or reads a status that
 * cannot be read, so a click does not close a game Steam reports; callers also
 * disable their button then, and this check covers the race where a game
 * started between render and click.
 */
export function restartSteam(): void {
  if (isAnyAppHolding()) {
    showToast("Close your running game before restarting Steam.");
    return;
  }
  SteamClient.User.StartRestart(false);
}
