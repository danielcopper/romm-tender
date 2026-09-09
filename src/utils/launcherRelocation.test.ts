/**
 * Exercises the plugin-load rewrite that points every RomM-owned shortcut at the
 * launcher's home outside the plugin folder (ADR-0032).
 *
 * The live scan is NOT mocked: which shortcuts are ours is decided by the exe
 * suffix inside `scanRomMShortcutExes`, so a mocked scan would make "touches
 * only ours" assert nothing. Steam's own globals are stubbed instead, the way
 * `steamShortcuts.test.ts` stubs them.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import { relocateShortcutsToLauncher } from "./launcherRelocation";

// The backend module is auto-mocked so `logInfo` / `logError` are observable;
// `./steamShortcuts` deliberately is NOT — see the file docstring.
vi.mock("../api/backend");

const OLD_LAUNCHER = "/home/deck/homebrew/plugins/decky-romm-sync/bin/rom-launcher";
const RENAMED_LAUNCHER = "/home/deck/homebrew/plugins/romm-tender/bin/rom-launcher";
const NEW_LAUNCHER = "/home/deck/.local/share/romm-tender/bin/rom-launcher";
const NEW_START_DIR = "/home/deck/.local/share/romm-tender/bin";

function stubShortcuts(exeByAppId: Record<number, string>): {
  setExe: ReturnType<typeof vi.fn>;
  setStartDir: ReturnType<typeof vi.fn>;
} {
  const setExe = vi.fn();
  const setStartDir = vi.fn();
  vi.stubGlobal("SteamClient", {
    Apps: {
      SetShortcutExe: setExe,
      SetShortcutStartDir: setStartDir,
      RegisterForAppDetails: vi.fn((appId: number, callback: (d: SteamAppDetails | undefined) => void) => {
        queueMicrotask(() => callback({ strShortcutExe: exeByAppId[appId] ?? "" }));
        return { unregister: vi.fn() };
      }),
    },
  });
  vi.stubGlobal("collectionStore", {
    deckDesktopApps: {
      apps: new Map(Object.keys(exeByAppId).map((appId) => [Number(appId), {}])),
    },
  });
  return { setExe, setStartDir };
}

function launcherIs(installed: boolean): void {
  vi.mocked(backend.getShortcutLauncher).mockResolvedValue({
    exe: NEW_LAUNCHER,
    start_dir: NEW_START_DIR,
    installed,
  });
}

describe("relocateShortcutsToLauncher", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("writes both the exe and the start dir of a shortcut still pointing into a plugin folder", async () => {
    launcherIs(true);
    const { setExe, setStartDir } = stubShortcuts({ 10: OLD_LAUNCHER });

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "relocated", rewritten: 1 });

    expect(setExe).toHaveBeenCalledWith(10, NEW_LAUNCHER);
    expect(setStartDir).toHaveBeenCalledWith(10, NEW_START_DIR);
  });

  it("rewrites a shortcut written by either plugin folder name", async () => {
    launcherIs(true);
    const { setExe } = stubShortcuts({ 10: OLD_LAUNCHER, 20: RENAMED_LAUNCHER });

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "relocated", rewritten: 2 });

    expect(setExe.mock.calls.map(([appId]) => appId)).toEqual([10, 20]);
  });

  it("skips a shortcut that already carries the launcher's home", async () => {
    launcherIs(true);
    const { setExe, setStartDir } = stubShortcuts({ 10: NEW_LAUNCHER, 20: OLD_LAUNCHER });

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "relocated", rewritten: 1 });

    expect(setExe).toHaveBeenCalledTimes(1);
    expect(setExe).toHaveBeenCalledWith(20, NEW_LAUNCHER);
    expect(setStartDir).toHaveBeenCalledTimes(1);
  });

  it("reports a library that is already relocated, having written nothing", async () => {
    launcherIs(true);
    const { setExe } = stubShortcuts({ 10: NEW_LAUNCHER, 20: NEW_LAUNCHER });

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "relocated", rewritten: 0 });

    expect(setExe).not.toHaveBeenCalled();
  });

  it("never touches a shortcut that is not ours", async () => {
    launcherIs(true);
    const { setExe, setStartDir } = stubShortcuts({ 10: OLD_LAUNCHER, 30: "/usr/bin/some-other-game" });

    await relocateShortcutsToLauncher();

    expect(setExe.mock.calls.map(([appId]) => appId)).toEqual([10]);
    expect(setStartDir.mock.calls.map(([appId]) => appId)).toEqual([10]);
  });

  it("rewrites nothing when this start could not place the launcher", async () => {
    launcherIs(false);
    const { setExe, setStartDir } = stubShortcuts({ 10: OLD_LAUNCHER });

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "not_installed" });

    expect(setExe).not.toHaveBeenCalled();
    expect(setStartDir).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logError)).toHaveBeenCalledWith(expect.stringContaining(NEW_LAUNCHER));
  });

  it("rewrites nothing when Steam's shortcut store cannot be read", async () => {
    launcherIs(true);
    const { setExe } = stubShortcuts({ 10: OLD_LAUNCHER });
    vi.stubGlobal("collectionStore", { deckDesktopApps: undefined });

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "unreadable" });

    expect(setExe).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logInfo)).toHaveBeenCalledWith(expect.stringContaining("could not be read"));
  });
});
