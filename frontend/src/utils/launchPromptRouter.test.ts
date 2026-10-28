import { describe, it, expect, vi, afterEach } from "vitest";
import * as backend from "../api/backend";
import { launchPromptsForThisStart, setDesktopLaunchPrompts, steamUIModeIsDesktop } from "./launchPromptRouter";
import type { LaunchPrompts } from "./launchVerdict";

vi.mock("../api/backend", () => ({ debugLog: vi.fn(() => Promise.resolve()) }));

const DESKTOP_MODE = 7;
const BIG_PICTURE_MODE = 4;

const makePrompts = (): LaunchPrompts => ({
  confirmCoreChange: vi.fn(),
  resolveConflicts: vi.fn(),
  askOfflineDrift: vi.fn(),
  confirmFallbackLaunch: vi.fn(),
  confirmUncheckedSource: vi.fn(),
  askSyncWait: vi.fn(),
});

const gamepad = makePrompts();
const desktop = makePrompts();

const inMode = (mode: number | undefined): void => {
  vi.stubGlobal("SteamUIStore", { MainInstanceUIMode: mode, SetRunningApp: vi.fn() });
};

const lastDebugLine = (): string => String(vi.mocked(backend.debugLog).mock.lastCall?.[0]);

afterEach(() => {
  setDesktopLaunchPrompts(null);
  vi.mocked(backend.debugLog).mockClear();
});

describe("steamUIModeIsDesktop", () => {
  it("answers yes only for the desktop client's mode", () => {
    inMode(DESKTOP_MODE);
    expect(steamUIModeIsDesktop()).toBe(true);
    inMode(BIG_PICTURE_MODE);
    expect(steamUIModeIsDesktop()).toBe(false);
    inMode(undefined);
    expect(steamUIModeIsDesktop()).toBe(false);
  });

  it("answers no while the store is absent", () => {
    vi.stubGlobal("SteamUIStore", undefined);
    expect(steamUIModeIsDesktop()).toBe(false);
    vi.stubGlobal("SteamUIStore", null);
    expect(steamUIModeIsDesktop()).toBe(false);
  });

  it("answers no when reading the mode throws", () => {
    vi.stubGlobal("SteamUIStore", {
      get MainInstanceUIMode(): number {
        throw new Error("not ready");
      },
    });
    expect(steamUIModeIsDesktop()).toBe(false);
  });
});

describe("launchPromptsForThisStart", () => {
  it("asks through the desktop surface while the desktop client is the main UI and it can draw", () => {
    inMode(DESKTOP_MODE);
    setDesktopLaunchPrompts(() => desktop);
    expect(launchPromptsForThisStart(gamepad)).toBe(desktop);
    expect(lastDebugLine()).toBe("Launch prompts: desktop — the desktop client is Steam's main UI");
  });

  it("asks through the gamepad modals while Big Picture is the main UI, even with a desktop surface", () => {
    inMode(BIG_PICTURE_MODE);
    const provider = vi.fn(() => desktop);
    setDesktopLaunchPrompts(provider);
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
    expect(provider).not.toHaveBeenCalled();
    expect(lastDebugLine()).toBe("Launch prompts: gamepad — the desktop client is not Steam's main UI");
  });

  it("asks through the gamepad modals when no desktop surface is registered, as in a shipped bundle", () => {
    inMode(DESKTOP_MODE);
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
    expect(lastDebugLine()).toBe("Launch prompts: gamepad — no desktop surface is registered");
  });

  it("asks through the gamepad modals when the desktop surface has no window", () => {
    inMode(DESKTOP_MODE);
    setDesktopLaunchPrompts(() => null);
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
    expect(lastDebugLine()).toBe("Launch prompts: gamepad — the desktop surface has no window to draw into");
  });

  it("asks through the gamepad modals when the desktop surface throws", () => {
    inMode(DESKTOP_MODE);
    setDesktopLaunchPrompts(() => {
      throw new Error("boom");
    });
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
    expect(lastDebugLine()).toBe("Launch prompts: gamepad — the desktop surface threw: Error: boom");
  });

  it("asks through the gamepad modals when the store is absent", () => {
    vi.stubGlobal("SteamUIStore", undefined);
    setDesktopLaunchPrompts(() => desktop);
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
  });

  it("asks through the gamepad modals once the desktop surface is withdrawn", () => {
    inMode(DESKTOP_MODE);
    setDesktopLaunchPrompts(() => desktop);
    expect(launchPromptsForThisStart(gamepad)).toBe(desktop);
    setDesktopLaunchPrompts(null);
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
  });
});
