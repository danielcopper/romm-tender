/**
 * Point every RomM-owned shortcut at the launcher's home.
 *
 * Why the launcher lives where it does, and which shortcuts count as ours, is
 * `docs/architecture/steam-non-steam-shortcuts.md` → "Where the exe points".
 *
 * The frontend does not decide WHICH shortcuts, and does not record that it is
 * over: the backend reads every one of them out of `shortcuts.vdf` in a single
 * parse and hands back the app IDs, so nothing here costs a
 * `RegisterForAppDetails` and its fat details object. This module only writes,
 * and only what it was handed — the completion is stamped by the backend's own
 * next reading of that file, which is the evidence a write actually landed.
 *
 * `SetShortcutExe` / `SetShortcutStartDir` are appId-safe, so a shortcut keeps
 * its identity and with it its playtime, artwork, collections and Steam Input
 * profile. Measured across a 826-shortcut library: the appId set was identical
 * to a `shortcuts.vdf` backup taken before the rewrite, and the calls cost 12 ms
 * of renderer time. Nothing is paced between them — the 50 ms the apply loop
 * uses belongs to a NEWLY ADDED shortcut waiting for its overview, and these
 * shortcuts already exist.
 */

import { getShortcutRelocation, logError, logInfo } from "../api/backend";

/**
 * Whether the library now points at the launcher's home.
 *
 * `relocated` is the only answer that says no shortcut of ours still names a
 * launcher inside a program install directory. `blocked` is every other outcome —
 * the launcher is not at its home yet, Steam's shortcut file could not be read,
 * or a write threw — and it is deliberately not a failure to report at the
 * user: the next start asks again, and until then the old paths keep working.
 */
export type LauncherRelocation = { status: "relocated" } | { status: "blocked" };

/**
 * Carry out whatever the backend says is left of the relocation.
 *
 * Runs once at panel load — not when the Quick Access panel mounts, because a
 * user can launch a game without ever opening the QAM.
 */
export async function relocateShortcutsToLauncher(): Promise<LauncherRelocation> {
  const plan = await getShortcutRelocation();
  if (plan.status === "done") return { status: "relocated" };
  if (plan.status === "blocked") {
    logInfo(`launcher relocation: nothing rewritten — ${plan.message}`);
    return { status: "blocked" };
  }

  try {
    for (const appId of plan.app_ids) {
      SteamClient.Apps.SetShortcutExe(appId, plan.exe);
      SteamClient.Apps.SetShortcutStartDir(appId, plan.start_dir);
    }
  } catch (e) {
    // A partial pass is not a relocation: the shortcuts it never reached still
    // name the old path, so this answers `blocked` rather than claiming
    // a move it did not finish. The next start's reading finds them and hands
    // them over again.
    logError(`launcher relocation: stopped after a failed write, leaving the rest for the next start: ${e}`);
    return { status: "blocked" };
  }

  logInfo(`launcher relocation: pointed ${plan.app_ids.length} shortcut(s) at ${plan.exe}`);
  return { status: "relocated" };
}
