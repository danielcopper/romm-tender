import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { canRestartDevice, restartDevice, restartSteam } from "./steamRestart";
import { showToast } from "./toast";

vi.mock("./toast", () => ({ showToast: vi.fn() }));

const restartPC = vi.fn();
const startRestart = vi.fn();

function stubSteam({ withRestartPC = true, running = [] as Array<{ appid: number; display_name: string }> } = {}) {
  vi.stubGlobal("SteamClient", {
    System: withRestartPC ? { RestartPC: restartPC } : {},
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

  describe("canRestartDevice", () => {
    it("is true where the Steam build carries RestartPC", () => {
      stubSteam();
      expect(canRestartDevice()).toBe(true);
    });

    it("is false where it does not, so no caller offers a button that does nothing", () => {
      stubSteam({ withRestartPC: false });
      expect(canRestartDevice()).toBe(false);
    });
  });

  describe("restartDevice", () => {
    it("reboots the device, which is what gets the plugin a next start", () => {
      stubSteam();
      restartDevice();
      expect(restartPC).toHaveBeenCalledTimes(1);
      expect(startRestart).not.toHaveBeenCalled();
    });

    it("refuses while a game is running, and says why", () => {
      stubSteam({ running: [{ appid: 42, display_name: "Some Game" }] });
      restartDevice();
      expect(restartPC).not.toHaveBeenCalled();
      expect(vi.mocked(showToast)).toHaveBeenCalledWith(expect.stringContaining("running game"));
    });

    it("does nothing rather than throwing when RestartPC vanished after the render", () => {
      stubSteam({ withRestartPC: false });
      expect(() => restartDevice()).not.toThrow();
    });
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
