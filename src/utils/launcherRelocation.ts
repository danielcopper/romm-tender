/**
 * Point every RomM-owned shortcut at the launcher's home outside the plugin
 * folder.
 *
 * Decky deletes a plugin's folder whole before it unpacks an update, and until
 * this runs every shortcut's `exe` names a file inside it — so an update that
 * fails to unpack leaves the whole library unable to start, with nothing able to
 * repair it. The backend installs the launcher under the user's data root on
 * every start; this rewrites the shortcuts that still point at the old place
 * (ADR-0032).
 *
 * `SetShortcutExe` / `SetShortcutStartDir` are appId-safe, so the shortcut keeps
 * its identity and with it its playtime, artwork, collections and Steam Input
 * profile. Measured across a 826-shortcut library: the appId set was identical
 * to a `shortcuts.vdf` backup taken before the rewrite, and the calls cost 12 ms
 * of renderer time. Nothing is paced between them — the 50 ms the apply loop
 * uses belongs to a NEWLY ADDED shortcut waiting for its overview, and these
 * shortcuts already exist.
 */

import { getShortcutLauncher, logError, logInfo } from "../api/backend";
import { scanRomMShortcutExes } from "./steamShortcuts";

/**
 * What one pass over the library did, and — for the panel — whether the
 * pre-rename install is still load-bearing for anything.
 *
 * `relocated` is the only outcome that says no shortcut of ours points into a
 * plugin folder any more. The other two are deliberately not failures to report
 * at the user: `not_installed` means this start could not place the launcher, so
 * rewriting would have pointed every game at a file that is not there;
 * `unreadable` means Steam's shortcut store could not be scanned, which is the
 * same "could not look" answer the reconcile path is careful never to act on.
 */
export type LauncherRelocation =
  { status: "relocated"; rewritten: number } | { status: "not_installed" } | { status: "unreadable" };

/**
 * Rewrite `exe` and `startDir` on every RomM-owned shortcut that does not
 * already carry the launcher's current home.
 *
 * Runs once at plugin load — not on panel mount, because a user can launch a
 * game without ever opening the QAM.
 */
export async function relocateShortcutsToLauncher(): Promise<LauncherRelocation> {
  const launcher = await getShortcutLauncher();
  if (!launcher.installed) {
    logError(`launcher relocation: no launcher at ${launcher.exe}; leaving every shortcut where it points`);
    return { status: "not_installed" };
  }

  const ours = await scanRomMShortcutExes();
  if (!ours) {
    logInfo("launcher relocation: Steam's shortcut store could not be read; nothing rewritten");
    return { status: "unreadable" };
  }

  let rewritten = 0;
  for (const [appId, exe] of ours) {
    if (exe === launcher.exe) continue;
    SteamClient.Apps.SetShortcutExe(appId, launcher.exe);
    SteamClient.Apps.SetShortcutStartDir(appId, launcher.start_dir);
    rewritten += 1;
  }
  if (rewritten > 0) logInfo(`launcher relocation: pointed ${rewritten} of ${ours.size} shortcuts at ${launcher.exe}`);
  return { status: "relocated", rewritten };
}
