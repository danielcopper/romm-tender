import { describe, it, expect, beforeEach, vi } from "vitest";
import { switchedOffStartingSource } from "./startingSource";
import * as backend from "../api/backend";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { TimeoutError } from "./withTimeout";

vi.mock("../api/backend", () => ({
  checkStartSource: vi.fn(),
  logError: vi.fn(),
}));

describe("switchedOffStartingSource", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("answers the kind the backend names as the switched-off source that would start the game", async () => {
    vi.mocked(backend.checkStartSource).mockResolvedValue({ switched_off: "emudeck" });
    await expect(switchedOffStartingSource("Watcher")).resolves.toBe("emudeck");
  });

  it("answers null while the backend names none", async () => {
    vi.mocked(backend.checkStartSource).mockResolvedValue({ switched_off: null });
    await expect(switchedOffStartingSource("Watcher")).resolves.toBeNull();
  });

  it("fails open and logs when the read throws", async () => {
    vi.mocked(backend.checkStartSource).mockRejectedValue(new Error("bridge down"));

    await expect(switchedOffStartingSource("CustomPlayButton")).resolves.toBeNull();
    expect(vi.mocked(backend.logError)).toHaveBeenCalledWith(
      expect.stringContaining("CustomPlayButton start-source check threw (allowing launch)"),
    );
  });

  it("rejects with the expired limit, rather than failing open, when the read never answers", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(backend.checkStartSource).mockReturnValue(new Promise<never>(() => {}));
      const answer = switchedOffStartingSource("CustomPlayButton");
      const settled = expect(answer).rejects.toBeInstanceOf(TimeoutError);

      await vi.advanceTimersByTimeAsync(LOCAL_CALL_LIMIT_MS);

      await settled;
      expect(vi.mocked(backend.logError)).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
