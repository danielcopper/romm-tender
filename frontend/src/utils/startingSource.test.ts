import { describe, it, expect, beforeEach, vi } from "vitest";
import { readStartingSource } from "./startingSource";
import * as backend from "../api/backend";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";

vi.mock("../api/backend", () => ({
  checkStartSource: vi.fn(),
  logError: vi.fn(),
}));

describe("readStartingSource", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("answers the kind the backend names as the switched-off source that would start the game", async () => {
    vi.mocked(backend.checkStartSource).mockResolvedValue({ switched_off: "emudeck" });
    await expect(readStartingSource("Watcher")).resolves.toEqual({ checked: true, switchedOff: "emudeck" });
  });

  it("answers checked with no kind while the backend names none", async () => {
    vi.mocked(backend.checkStartSource).mockResolvedValue({ switched_off: null });
    await expect(readStartingSource("Watcher")).resolves.toEqual({ checked: true, switchedOff: null });
  });

  it("answers unchecked, and logs, when the read throws", async () => {
    vi.mocked(backend.checkStartSource).mockRejectedValue(new Error("bridge down"));

    await expect(readStartingSource("CustomPlayButton")).resolves.toEqual({ checked: false });
    expect(vi.mocked(backend.logError)).toHaveBeenCalledWith(
      expect.stringContaining("CustomPlayButton start-source check got no answer"),
    );
  });

  it("answers unchecked when the backend could not detect the sources", async () => {
    vi.mocked(backend.checkStartSource).mockResolvedValue({
      success: false,
      reason: "detection_failed",
      message: "Detecting the emulator sources failed.",
    });

    await expect(readStartingSource("Watcher")).resolves.toEqual({ checked: false });
    expect(vi.mocked(backend.logError)).toHaveBeenCalledWith(expect.stringContaining("detection_failed"));
  });

  it("answers unchecked, never rejects, when the read gets no answer within its limit", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(backend.checkStartSource).mockReturnValue(new Promise<never>(() => {}));
      let outcome: unknown = "pending";
      void readStartingSource("CustomPlayButton").then(
        (value) => (outcome = value),
        (e: unknown) => (outcome = e),
      );

      await vi.advanceTimersByTimeAsync(LOCAL_CALL_LIMIT_MS - 1);
      expect(outcome).toBe("pending");
      await vi.advanceTimersByTimeAsync(1);
      expect(outcome).toEqual({ checked: false });
    } finally {
      vi.useRealTimers();
    }
  });
});
