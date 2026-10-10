import { describe, it, expect, beforeEach, vi } from "vitest";
import { startingSourceSwitchedOff } from "./startingSource";
import * as backend from "../api/backend";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { TimeoutError } from "./withTimeout";

vi.mock("../api/backend", () => ({
  checkStartSource: vi.fn(),
  logError: vi.fn(),
}));

describe("startingSourceSwitchedOff", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("is true while the backend names the source games start through as switched off", async () => {
    vi.mocked(backend.checkStartSource).mockResolvedValue({ switched_off: "retrodeck" });
    await expect(startingSourceSwitchedOff("Watcher")).resolves.toBe(true);
  });

  it("is false while the backend names none", async () => {
    vi.mocked(backend.checkStartSource).mockResolvedValue({ switched_off: null });
    await expect(startingSourceSwitchedOff("Watcher")).resolves.toBe(false);
  });

  it("fails open and logs when the read throws", async () => {
    vi.mocked(backend.checkStartSource).mockRejectedValue(new Error("bridge down"));

    await expect(startingSourceSwitchedOff("CustomPlayButton")).resolves.toBe(false);
    expect(vi.mocked(backend.logError)).toHaveBeenCalledWith(
      expect.stringContaining("CustomPlayButton start-source check threw (allowing launch)"),
    );
  });

  it("rejects with the expired limit, rather than failing open, when the read never answers", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(backend.checkStartSource).mockReturnValue(new Promise<never>(() => {}));
      const answer = startingSourceSwitchedOff("CustomPlayButton");
      const settled = expect(answer).rejects.toBeInstanceOf(TimeoutError);

      await vi.advanceTimersByTimeAsync(LOCAL_CALL_LIMIT_MS);

      await settled;
      expect(vi.mocked(backend.logError)).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
