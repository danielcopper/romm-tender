import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePlayLaunch } from "./usePlayLaunch";
import * as backend from "../../api/backend";
import * as toast from "../../utils/toast";
import * as sessionManager from "../../utils/sessionManager";
import * as pruneLease from "../../utils/pruneLease";
import { reconfirmLaunchOptions } from "../../utils/launchOptionsReconcile";
import { consumeLaunchSkip } from "../../utils/launchGate";
import { getMigrationState } from "../../utils/migrationStore";
import * as gameDetailStore from "../../utils/gameDetailStore";

vi.mock("../../api/backend", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/backend")>();
  return {
    ...actual,
    preLaunchSync: vi.fn().mockResolvedValue({ success: true }),
    checkStartSource: vi.fn().mockResolvedValue({ switched_off: null }),
    stopRunningGame: vi.fn().mockResolvedValue({ success: true, stopped: 1, force_killed: 0 }),
    debugLog: vi.fn(),
    logError: vi.fn(),
    getSaveSetupInfo: vi.fn(),
    probeReachability: vi.fn().mockResolvedValue({ online: true }),
    checkLocalDrift: vi.fn().mockResolvedValue({ drifted: false, only_here: false, stranding: "none", rom_id: 100 }),
    isSaveTrackingConfigured: vi.fn().mockResolvedValue({ configured: true }),
    confirmSlotChoice: vi.fn(),
    checkCoreChange: vi.fn().mockResolvedValue({ changed: false }),
    getInstalledRom: vi.fn().mockResolvedValue({ launchable: true }),
    getSaveStatus: vi.fn(),
  };
});

vi.mock("../../utils/toast", () => ({
  showToast: vi.fn(),
}));

vi.mock("../../utils/pruneLease", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../utils/pruneLease")>();
  return {
    ...actual,
    capturePruneLeaseAdmission: vi.fn((owner: string) => ({ owner, ownerGeneration: 1 })),
    isPruneLeaseAdmissionCurrent: vi.fn(() => true),
  };
});

/** Whether Steam lists the app as running, as the `readGameRunning` stand-in below reads it. */
const steamListsApp = vi.hoisted(() => vi.fn((_appId: number) => false));

vi.mock("../../utils/sessionManager", () => {
  const isSessionActive = vi.fn((_romId: number) => false);
  return {
    isSessionActive,
    noteAppRom: vi.fn(),
    readGameRunning: vi.fn((appId: number, romId: number | null | undefined) => {
      const running = (romId != null && isSessionActive(romId)) || steamListsApp(appId);
      return { running, decidedBy: running ? "store" : "none", diagnostics: "" };
    }),
  };
});

vi.mock("../../utils/launchOptionsReconcile", () => ({
  reconfirmLaunchOptions: vi.fn().mockResolvedValue({ status: "ready" }),
}));

vi.mock("../../utils/gameDetailStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../utils/gameDetailStore")>();
  return { ...actual, recheckStart: vi.fn() };
});

vi.mock("../../utils/migrationStore", () => ({
  getMigrationState: vi.fn(() => ({ pending: false })),
}));

describe("usePlayLaunch", () => {
  const ask = vi.fn();
  const setPressState = vi.fn();
  const holdVerdict = vi.fn();
  const clearSessionRunning = vi.fn();
  const setStopPending = vi.fn();
  const runGame = vi.fn();

  const renderLaunch = (overrides: Partial<Parameters<typeof usePlayLaunch>[0]> = {}) =>
    renderHook(() =>
      usePlayLaunch({
        appId: 12345,
        romId: 100,
        pressState: null,
        startCheckFailed: false,
        ask,
        leaseOwner: "desktop-play-button:12345",
        setPressState,
        clearSessionRunning,
        holdVerdict,
        setStopPending,
        ...overrides,
      }),
    ).result;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("SteamClient", { Apps: { RunGame: runGame } });
    // The skip set is module state, and a start in an earlier test leaves its mark.
    consumeLaunchSkip(12345);
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(() => ({ GetGameID: () => "12345" })) });
    vi.mocked(sessionManager.isSessionActive).mockReturnValue(false);
    steamListsApp.mockReturnValue(false);
  });

  /** The Stop press's update of the press state takes down a held LAUNCHING and nothing else. */
  const expectOnlyLaunchingTakenDown = () => {
    const update = setPressState.mock.lastCall?.[0] as (prev: string | null) => string | null;
    expect(update("launching")).toBeNull();
    expect(update("checking")).toBe("checking");
    expect(update("syncing")).toBe("syncing");
  };

  describe("Stop", () => {
    it("stops the running game once the Stop confirm is accepted", async () => {
      vi.mocked(sessionManager.isSessionActive).mockReturnValue(true);
      ask.mockResolvedValueOnce(true);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handleStopClick();
      });

      expect(ask).toHaveBeenCalledExactlyOnceWith(false, expect.any(Function));
      expect(backend.stopRunningGame).toHaveBeenCalledWith(100);
      expect(setStopPending.mock.calls).toEqual([[true], [false]]);
      expect(clearSessionRunning).toHaveBeenCalled();
      expectOnlyLaunchingTakenDown();
    });

    it("does not stop the game when the Stop confirm is declined", async () => {
      vi.mocked(sessionManager.isSessionActive).mockReturnValue(true);
      ask.mockResolvedValueOnce(false);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handleStopClick();
      });

      expect(ask).toHaveBeenCalledTimes(1);
      expect(backend.stopRunningGame).not.toHaveBeenCalled();
      expect(setPressState).not.toHaveBeenCalled();
    });

    it("brings a stale running state down without asking when nothing is running", async () => {
      const result = renderLaunch();

      await act(async () => {
        await result.current.handleStopClick();
      });

      expect(ask).not.toHaveBeenCalled();
      expect(backend.stopRunningGame).not.toHaveBeenCalled();
      expect(clearSessionRunning).toHaveBeenCalled();
      expectOnlyLaunchingTakenDown();
    });
  });

  describe("before the page's detail names a ROM", () => {
    it("Play does nothing", async () => {
      const result = renderLaunch({ romId: null });

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(setPressState).not.toHaveBeenCalled();
      expect(runGame).not.toHaveBeenCalled();
    });
  });

  describe("Resolve Conflict", () => {
    it("does nothing before the page's detail names a ROM", async () => {
      const result = renderLaunch({ romId: null });

      await act(async () => {
        await result.current.handleResolveConflictClick();
      });

      expect(backend.getSaveStatus).not.toHaveBeenCalled();
      expect(setPressState).not.toHaveBeenCalled();
      expect(holdVerdict).not.toHaveBeenCalled();
    });
  });

  describe("Play", () => {
    it("starts a game that is already running without its pre-launch sync", async () => {
      vi.mocked(sessionManager.readGameRunning).mockReturnValueOnce({
        running: true,
        decidedBy: "session",
        diagnostics: "session=1",
      } as ReturnType<typeof sessionManager.readGameRunning>);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(backend.preLaunchSync).not.toHaveBeenCalled();
      expect(runGame).toHaveBeenCalledWith("12345", "", -1, 100);
      expect(backend.debugLog).toHaveBeenCalledWith(
        "DesktopPlayButton: appId=12345 already running — skipping pre-launch sync [session=1]",
      );
    });

    it("starts nothing when the re-confirm was cancelled", async () => {
      vi.mocked(reconfirmLaunchOptions).mockResolvedValueOnce({ status: "cancelled" });
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(runGame).not.toHaveBeenCalled();
      expect(sessionManager.noteAppRom).not.toHaveBeenCalled();
      expect(consumeLaunchSkip(12345)).toBe(false);
    });

    it("takes no press while one is under way", async () => {
      const result = renderLaunch({ pressState: "checking" });

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(setPressState).not.toHaveBeenCalled();
      expect(runGame).not.toHaveBeenCalled();
    });

    it("marks the start before Steam is asked to start it, so the launch watcher does not gate it again", async () => {
      let markedAtStart: boolean | null = null;
      runGame.mockImplementationOnce(() => {
        markedAtStart = consumeLaunchSkip(12345);
      });
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(runGame).toHaveBeenCalledTimes(1);
      expect(markedAtStart).toBe(true);
    });

    it("logs that it runs the launch gate for a game that is not running", async () => {
      vi.mocked(sessionManager.readGameRunning).mockReturnValueOnce({
        running: false,
        decidedBy: "none",
        diagnostics: "session=none",
      } as ReturnType<typeof sessionManager.readGameRunning>);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(backend.debugLog).toHaveBeenCalledWith(
        "DesktopPlayButton: appId=12345 not running — running the launch gate [session=none]",
      );
    });

    it("re-confirms the shortcut's launch options before the start", async () => {
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(reconfirmLaunchOptions).toHaveBeenCalledWith(100, 12345, "DesktopPlayButton", expect.anything());
      expect(runGame).toHaveBeenCalled();
    });

    it("starts without the re-confirm after a check that got no answer in time", async () => {
      vi.mocked(backend.checkCoreChange).mockReturnValueOnce(new Promise(() => {}));
      vi.useFakeTimers();
      try {
        ask.mockResolvedValueOnce("start");
        const result = renderLaunch();

        await act(async () => {
          const press = result.current.handlePlayClick();
          await vi.advanceTimersByTimeAsync(10_000);
          await press;
        });
      } finally {
        vi.useRealTimers();
      }

      expect(runGame).toHaveBeenCalled();
      expect(reconfirmLaunchOptions).not.toHaveBeenCalled();
    });

    it("returns to PLAY and starts nothing when the re-confirm times out", async () => {
      vi.mocked(reconfirmLaunchOptions).mockResolvedValueOnce({ status: "timeout" });
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(setPressState).toHaveBeenLastCalledWith(null);
      expect(runGame).not.toHaveBeenCalled();
      expect(consumeLaunchSkip(12345)).toBe(false);
    });

    it("returns to PLAY and starts nothing once the page no longer answers for the start", async () => {
      vi.mocked(pruneLease.isPruneLeaseAdmissionCurrent).mockReturnValueOnce(false);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(setPressState).toHaveBeenLastCalledWith(null);
      expect(setPressState).not.toHaveBeenCalledWith("launching");
      expect(runGame).not.toHaveBeenCalled();
    });

    it("asks about a changed core and starts nothing when it is cancelled", async () => {
      vi.mocked(backend.checkCoreChange).mockResolvedValueOnce({
        changed: true,
        old_label: "PCSX ReARMed",
        new_label: "DuckStation",
      });
      ask.mockResolvedValueOnce(false);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(ask).toHaveBeenCalledTimes(1);
      expect(setPressState).toHaveBeenLastCalledWith(null);
      expect(backend.preLaunchSync).not.toHaveBeenCalled();
    });

    it("asks about a changed core and starts the game when it is confirmed", async () => {
      vi.mocked(backend.checkCoreChange).mockResolvedValueOnce({
        changed: true,
        old_label: "PCSX ReARMed",
        new_label: "DuckStation",
      });
      ask.mockResolvedValueOnce(true);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(backend.preLaunchSync).toHaveBeenCalledWith(100);
      expect(runGame).toHaveBeenCalled();
    });

    it("a pending migration starts nothing and returns the button to PLAY without a toast", async () => {
      vi.mocked(getMigrationState).mockReturnValueOnce({ pending: true } as ReturnType<typeof getMigrationState>);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(setPressState.mock.calls).toEqual([["checking"], [null]]);
      expect(backend.preLaunchSync).not.toHaveBeenCalled();
      expect(toast.showToast).not.toHaveBeenCalled();
    });

    it("a prompt that throws resets the button instead of leaving it stuck", async () => {
      vi.mocked(backend.probeReachability).mockResolvedValueOnce({ online: false });
      vi.mocked(backend.checkLocalDrift).mockResolvedValueOnce({
        drifted: true,
        only_here: false,
        stranding: "unsynced",
        rom_id: 100,
      });
      ask.mockRejectedValueOnce(new Error("dialog host gone"));
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(backend.debugLog).toHaveBeenCalledWith(
        "DesktopPlayButton: Play's launch check threw — resetting to play: Error: dialog host gone",
      );
      expect(setPressState).toHaveBeenLastCalledWith(null);
      expect(gameDetailStore.recheckStart).not.toHaveBeenCalled();
      expect(toast.showToast).not.toHaveBeenCalled();
    });
  });

  describe("after the page's check of whether the game can start went wrong", () => {
    const REFUSED = "smw.zip is not the file Super Nintendo starts from; it starts from .sfc.";

    it("asks again before the launch check, and starts nothing where the answer refuses, saying why", async () => {
      vi.mocked(gameDetailStore.recheckStart).mockResolvedValueOnce(REFUSED);
      const result = renderLaunch({ startCheckFailed: true });

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(gameDetailStore.recheckStart).toHaveBeenCalledWith(12345, 100, false);
      expect(toast.showToast).toHaveBeenCalledWith(REFUSED);
      expect(backend.probeReachability).not.toHaveBeenCalled();
      expect(runGame).not.toHaveBeenCalled();
      expect(setPressState).toHaveBeenLastCalledWith(null);
    });

    it("asks again and starts the game where nothing stands in its way", async () => {
      vi.mocked(gameDetailStore.recheckStart).mockResolvedValueOnce(null);
      const result = renderLaunch({ startCheckFailed: true });

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(gameDetailStore.recheckStart).toHaveBeenCalledWith(12345, 100, false);
      expect(runGame).toHaveBeenCalledWith("12345", "", -1, 100);
      expect(toast.showToast).not.toHaveBeenCalled();
    });

    it("says in a notification that a press whose launch check then throws could not start the game", async () => {
      vi.mocked(gameDetailStore.recheckStart).mockResolvedValueOnce(null);
      vi.mocked(backend.probeReachability).mockResolvedValueOnce({ online: false });
      vi.mocked(backend.checkLocalDrift).mockResolvedValueOnce({
        drifted: true,
        only_here: false,
        stranding: "unsynced",
        rom_id: 100,
      });
      ask.mockRejectedValueOnce(new Error("dialog host gone"));
      const result = renderLaunch({ startCheckFailed: true });

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(toast.showToast).toHaveBeenCalledWith("Tender could not start this game: dialog host gone");
      expect(setPressState).toHaveBeenLastCalledWith(null);
    });
  });
});
