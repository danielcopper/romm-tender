import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { restartSteam } from "./steamRestart";
import { showToast } from "./toast";

vi.mock("./toast", () => ({ showToast: vi.fn() }));

const restartPC = vi.fn();
const startRestart = vi.fn();

function stubSteam({ running = [] as Array<{ appid: number; display_name: string }> } = {}) {
  vi.stubGlobal("SteamClient", {
    System: { RestartPC: restartPC },
    User: { StartRestart: startRestart },
  });
  vi.stubGlobal("SteamUIStore", { RunningApps: running });
}

describe("steamRestart", () => {
  beforeEach(() => {
    restartPC.mockReset();
    startRestart.mockReset();
    vi.mocked(showToast).mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("restartSteam", () => {
    it("restarts the client, not the device", () => {
      stubSteam();
      restartSteam();
      expect(startRestart).toHaveBeenCalledWith(false);
      expect(restartPC).not.toHaveBeenCalled();
    });

    it("refuses while a game is running, and says why", () => {
      stubSteam({ running: [{ appid: 42, display_name: "Some Game" }] });
      restartSteam();
      expect(startRestart).not.toHaveBeenCalled();
      expect(vi.mocked(showToast)).toHaveBeenCalledWith(expect.stringContaining("running game"));
    });
  });
});
