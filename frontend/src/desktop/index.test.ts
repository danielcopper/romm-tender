import { describe, it, expect, vi, afterEach } from "vitest";
import { startDesktopSurface, stopDesktopSurface } from "./index";
import * as desktopWin from "./desktopWindow";
import * as backend from "../api/backend";

describe("the desktop surface", () => {
  afterEach(() => {
    stopDesktopSurface();
    vi.restoreAllMocks();
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

  it("logs a start that throws instead of throwing into the panel's start-up", () => {
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => {});
    vi.spyOn(desktopWin, "findDesktopWindow").mockImplementation(() => {
      throw new Error("no popup manager");
    });

    expect(() => startDesktopSurface()).not.toThrow();
    expect(logError).toHaveBeenCalledWith(expect.stringContaining("no popup manager"));
  });
});
