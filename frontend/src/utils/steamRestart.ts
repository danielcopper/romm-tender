/**
 * {@link restartSteam} restarts the Steam **client**. The frontend reloads;
 * Tender's backend keeps running (`services/library/_state.py` states the same
 * fact from the backend side). That is exactly right for freeing the renderer's
 * per-session heap budget.
 */

import { showToast } from "./toast";
import { isAnyAppRunning } from "./runningApps";

/**
 * Restart the Steam client — the deterministic "free memory" action. A full
 * client restart resets the renderer's per-session heap budget.
 *
 * Fire-and-forget: `StartRestart` tears the client down and back up. Hard-guarded
 * on a running game so a click can NEVER kill one mid-session; callers also
 * disable their button while a game runs, and this guard covers the race where
 * a game started between render and click.
 */
export function restartSteam(): void {
  if (isAnyAppRunning()) {
    showToast("Close your running game before restarting Steam.");
    return;
  }
  SteamClient.User.StartRestart(false);
}
