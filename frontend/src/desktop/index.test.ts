import { describe, it, expect, vi, afterEach } from "vitest";
import { startDesktopSurface, stopDesktopSurface } from "./index";
import * as desktopWin from "./desktopWindow";
import * as navigationWatcher from "./navigationWatcher";
import { GameView } from "./gameview/GameView";
import * as backend from "../api/backend";
import { launchPromptsForThisStart } from "../utils/launchPromptRouter";
import type { LaunchPrompts } from "../utils/launchVerdict";

describe("the desktop surface", () => {
  afterEach(() => {
    stopDesktopSurface();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("offers the launch watcher its dialogs while it runs and withdraws them when it stops", () => {
    const gamepad = {} as LaunchPrompts;
    vi.stubGlobal("SteamUIStore", { MainInstanceUIMode: 7, SetRunningApp: vi.fn() });
    vi.spyOn(desktopWin, "findDesktopWindow").mockReturnValue(window);
    vi.spyOn(navigationWatcher, "startDesktopNavigationWatcher").mockImplementation(() => () => {});
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);

    startDesktopSurface();
    expect(launchPromptsForThisStart(gamepad)).not.toBe(gamepad);

    stopDesktopSurface();
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
  });

  it("starts the navigation watcher, which looks for the desktop window, and stops it again", () => {
    vi.useFakeTimers();
    try {
      const find = vi.spyOn(desktopWin, "findDesktopWindow").mockReturnValue(undefined);
      startDesktopSurface();
      expect(find).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(500);
      expect(find).toHaveBeenCalledTimes(2);

      stopDesktopSurface();
      vi.advanceTimersByTime(1000);
      expect(find).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("hands the watcher the game view, and no play button, so Steam's own stays", () => {
    const start = vi.spyOn(navigationWatcher, "startDesktopNavigationWatcher").mockImplementation(() => () => {});

    startDesktopSurface();

    expect(start).toHaveBeenCalledExactlyOnceWith({ gameView: GameView });
  });

  it("logs a start that throws instead of throwing into the panel's start-up", () => {
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => {});
    vi.spyOn(desktopWin, "findDesktopWindow").mockImplementation(() => {
      throw new Error("no popup manager");
    });

    expect(() => startDesktopSurface()).not.toThrow();
    expect(logError).toHaveBeenCalledWith(expect.stringContaining("no popup manager"));
  });
});
