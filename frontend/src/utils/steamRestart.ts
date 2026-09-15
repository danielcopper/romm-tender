/**
 * The two restarts the panel can ask for, and what each one actually restarts.
 *
 * They are not interchangeable, and the difference is the whole reason both
 * live here rather than beside the one notice that first needed each:
 *
 * - {@link restartSteam} restarts the Steam **client**. The frontend reloads;
 *   the plugin's Python backend does not start again
 *   (`services/library/_state.py` states the same fact from the backend side).
 *   That is exactly right for freeing the renderer's per-session heap budget.
 * - {@link restartDevice} reboots the **device**, which is what it takes to
 *   reach the plugin's next start — the only moment the data-location migration
 *   can run, because it has to happen before the database is opened.
 *
 * Both refuse while a game is running: either one would close it, and neither
 * is urgent enough to be worth that.
 */

import { showToast } from "./toast";
import { isAnyAppRunning } from "./runningApps";

/**
 * Whether this Steam build can reboot the device on request.
 *
 * Checked at render rather than assumed: `SteamClient.System.RestartPC` is
 * present in the build this was measured on and is what Steam's own UI calls,
 * but a caller that offered the button unconditionally would show a control
 * that silently does nothing if a later build drops it.
 *
 * `SteamClient` and `System` are reached bare, as `restartSteam` reaches `User`
 * below — the plugin runs in SharedJSContext, where the global is always there.
 * Only the METHOD is treated as optional, because it is the one part a Steam
 * update can take away.
 */
export function canRestartDevice(): boolean {
  return typeof SteamClient.System.RestartPC === "function";
}

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

/**
 * Reboot the device, which is how the plugin gets its next start.
 *
 * Same hard guard on a running game as {@link restartSteam}. A no-op when the
 * running Steam build has no `RestartPC` — callers ask {@link canRestartDevice}
 * first and render no button at all, so reaching this branch means the method
 * disappeared between render and click.
 */
export function restartDevice(): void {
  if (isAnyAppRunning()) {
    showToast("Close your running game before restarting.");
    return;
  }
  SteamClient.System.RestartPC?.();
}
