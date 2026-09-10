/**
 * Exercises the plugin-load pass that points every RomM-owned shortcut at the
 * launcher's home outside the plugin folder (ADR-0032).
 *
 * Which shortcuts need the write is the backend's answer, read out of
 * `shortcuts.vdf`; what this module owns is the writing, and the two directions
 * that must never be confused — a blocked answer writes nothing at all, and a
 * pass that did not finish must not be stamped complete.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import { relocateShortcutsToLauncher } from "./launcherRelocation";

vi.mock("../api/backend");

const NEW_LAUNCHER = "/home/deck/.local/share/romm-tender/bin/rom-launcher";
const NEW_START_DIR = "/home/deck/.local/share/romm-tender/bin";

function stubSteam(): { setExe: ReturnType<typeof vi.fn>; setStartDir: ReturnType<typeof vi.fn> } {
  const setExe = vi.fn();
  const setStartDir = vi.fn();
  vi.stubGlobal("SteamClient", { Apps: { SetShortcutExe: setExe, SetShortcutStartDir: setStartDir } });
  return { setExe, setStartDir };
}

function planIs(plan: backend.ShortcutRelocation): void {
  vi.mocked(backend.getShortcutRelocation).mockResolvedValue(plan);
}

const outstanding = (appIds: number[]): backend.ShortcutRelocation => ({
  status: "outstanding",
  exe: NEW_LAUNCHER,
  start_dir: NEW_START_DIR,
  app_ids: appIds,
});

describe("relocateShortcutsToLauncher", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(backend.completeShortcutRelocation).mockResolvedValue({ success: true });
  });

  it("writes both the exe and the start dir of every shortcut the backend named", async () => {
    planIs(outstanding([10, 20]));
    const { setExe, setStartDir } = stubSteam();

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "relocated" });

    expect(setExe.mock.calls).toEqual([
      [10, NEW_LAUNCHER],
      [20, NEW_LAUNCHER],
    ]);
    expect(setStartDir.mock.calls).toEqual([
      [10, NEW_START_DIR],
      [20, NEW_START_DIR],
    ]);
  });

  it("stamps the transition complete once every write was issued", async () => {
    planIs(outstanding([10]));
    stubSteam();

    await relocateShortcutsToLauncher();

    expect(vi.mocked(backend.completeShortcutRelocation)).toHaveBeenCalledTimes(1);
  });

  it("writes and stamps nothing when the backend says the transition is over", async () => {
    planIs({ status: "done" });
    const { setExe, setStartDir } = stubSteam();

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "relocated" });

    expect(setExe).not.toHaveBeenCalled();
    expect(setStartDir).not.toHaveBeenCalled();
    expect(vi.mocked(backend.completeShortcutRelocation)).not.toHaveBeenCalled();
  });

  it("writes nothing when the backend blocks the rewrite", async () => {
    // The direction that matters: the launcher is not at its home, so pointing
    // a shortcut at it would stop its game from starting.
    planIs({ status: "blocked", message: "The launcher is not at its home yet." });
    const { setExe, setStartDir } = stubSteam();

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "blocked" });

    expect(setExe).not.toHaveBeenCalled();
    expect(setStartDir).not.toHaveBeenCalled();
    expect(vi.mocked(backend.completeShortcutRelocation)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logInfo)).toHaveBeenCalledWith(expect.stringContaining("not at its home"));
  });

  it("leaves the transition unstamped when a write throws part-way through", async () => {
    planIs(outstanding([10, 20, 30]));
    const setExe = vi.fn((appId: number) => {
      if (appId === 20) throw new Error("Steam said no");
    });
    const setStartDir = vi.fn();
    vi.stubGlobal("SteamClient", { Apps: { SetShortcutExe: setExe, SetShortcutStartDir: setStartDir } });

    await expect(relocateShortcutsToLauncher()).resolves.toEqual({ status: "blocked" });

    // The shortcuts it never reached must stay in the plan, so the next start
    // is handed them again rather than finding the question closed.
    expect(vi.mocked(backend.completeShortcutRelocation)).not.toHaveBeenCalled();
    expect(setExe.mock.calls.map(([appId]) => appId)).toEqual([10, 20]);
    expect(vi.mocked(backend.logError)).toHaveBeenCalledWith(expect.stringContaining("Steam said no"));
  });
});
