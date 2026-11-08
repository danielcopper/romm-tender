import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePlayLaunch } from "./usePlayLaunch";
import * as backend from "../../api/backend";
import * as toast from "../../utils/toast";
import * as sessionManager from "../../utils/sessionManager";
import * as pruneLease from "../../utils/pruneLease";
import { prepareStart } from "../../utils/launchOptionsReconcile";
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
    removeRom: vi.fn().mockResolvedValue({ success: true, prune_lease_token: "tok" }),
    invalidateCachedGameDetail: vi.fn(),
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
    withPruneLease: vi.fn(async (_tok: string, _ctx: string, fn: (signal: { aborted: boolean }) => Promise<void>) =>
      fn({ aborted: false }),
    ),
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

vi.mock("../../utils/steamShortcuts", () => ({
  setLaunchOptionsConfirmed: vi.fn().mockResolvedValue(true),
}));

vi.mock("../../utils/launchOptionsReconcile", () => ({
  prepareStart: vi.fn().mockResolvedValue({ status: "ready" }),
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
  const startAction = vi.fn();
  const endAction = vi.fn();
  const runGame = vi.fn();

  type LaunchOptions = Partial<Parameters<typeof usePlayLaunch>[0]>;

  /** The hook rendered with *initialProps*, whose `rerender` takes the options a version switch changes. */
  const renderSwitchable = (initialProps: LaunchOptions = {}) =>
    renderHook(
      (props: LaunchOptions) =>
        usePlayLaunch({
          appId: 12345,
          romId: 100,
          romName: "Test Game",
          pressState: null,
          startCheckFailed: false,
          ask,
          leaseOwner: "desktop-play-button:12345",
          setPressState,
          startAction,
          endAction,
          clearSessionRunning,
          holdVerdict,
          setStopPending,
          ...props,
        }),
      { initialProps },
    );

  const renderLaunch = (overrides: LaunchOptions = {}) => renderSwitchable(overrides).result;

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
    it("holds its verdict for the ROM it was pressed for", async () => {
      vi.mocked(backend.getSaveStatus).mockResolvedValueOnce({ rom_id: 100, files: [], conflicts: [] } as never);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handleResolveConflictClick();
      });

      expect(holdVerdict).toHaveBeenCalledExactlyOnceWith(expect.any(String), 100);
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
      expect(prepareStart).not.toHaveBeenCalled();
      expect(runGame).toHaveBeenCalledWith("12345", "", -1, 100);
      expect(backend.debugLog).toHaveBeenCalledWith(
        "DesktopPlayButton: appId=12345 already running — skipping pre-launch sync [session=1]",
      );
    });

    it("starts nothing when preparing the start was cancelled", async () => {
      vi.mocked(prepareStart).mockResolvedValueOnce({ status: "cancelled" });
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

    it("prepares the start before Steam starts it", async () => {
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(prepareStart).toHaveBeenCalledWith(100, 12345, "DesktopPlayButton", expect.anything());
      expect(runGame).toHaveBeenCalled();
    });

    it("still prepares the start after a check that got no answer in time", async () => {
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

      expect(prepareStart).toHaveBeenCalledWith(100, 12345, "DesktopPlayButton", expect.anything());
      expect(runGame).toHaveBeenCalled();
    });

    it("returns to PLAY, starts nothing and says why when the start is refused", async () => {
      vi.mocked(prepareStart).mockResolvedValueOnce({ status: "refused", sentence: "No emulator starts this game." });
      const result = renderLaunch();

      await act(async () => {
        await result.current.handlePlayClick();
      });

      expect(setPressState).toHaveBeenLastCalledWith(null);
      expect(runGame).not.toHaveBeenCalled();
      expect(consumeLaunchSkip(12345)).toBe(false);
      expect(toast.showToast).toHaveBeenCalledWith("No emulator starts this game.");
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

  describe("Uninstall", () => {
    it("reads UNINSTALLING..., then holds DOWNLOAD once the game is removed", async () => {
      const result = renderLaunch();

      await act(async () => {
        await result.current.handleUninstallClick();
      });

      expect(startAction).toHaveBeenCalledWith("uninstalling", 100);
      expect(backend.removeRom).toHaveBeenCalledWith(100);
      expect(backend.debugLog).toHaveBeenCalledWith("DesktopPlayButton: uninstalling romId=100");
      expect(toast.showToast).toHaveBeenCalledWith("Test Game uninstalled");
      expect(holdVerdict).toHaveBeenCalledWith("download", 100);
      expect(endAction).toHaveBeenCalledWith("uninstalling", 100);
    });

    it("returns the button to its own state when the removal fails, saying why", async () => {
      vi.mocked(backend.removeRom).mockResolvedValueOnce({ success: false, message: "Failed" } as never);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handleUninstallClick();
      });

      expect(startAction).toHaveBeenCalledWith("uninstalling", 100);
      expect(toast.showToast).toHaveBeenCalledWith("Failed");
      expect(holdVerdict).not.toHaveBeenCalled();
      expect(endAction).toHaveBeenCalledWith("uninstalling", 100);
    });

    it("takes the next press once a refused removal has come back", async () => {
      vi.mocked(backend.removeRom).mockResolvedValueOnce({ success: false, message: "Failed" } as never);
      const result = renderLaunch();

      await act(async () => {
        await result.current.handleUninstallClick();
      });
      await act(async () => {
        await result.current.handleUninstallClick();
      });

      expect(backend.removeRom).toHaveBeenCalledTimes(2);
      expect(holdVerdict).toHaveBeenCalledWith("download", 100);
    });

    it("starts no second removal while one is under way", async () => {
      let finish!: (value: unknown) => void;
      vi.mocked(backend.removeRom).mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }) as never,
      );
      const result = renderLaunch();

      let first!: Promise<void>;
      await act(async () => {
        first = result.current.handleUninstallClick();
        await result.current.handleUninstallClick();
      });
      expect(backend.removeRom).toHaveBeenCalledTimes(1);

      await act(async () => {
        finish({ success: true, prune_lease_token: "tok" });
        await first;
      });
      expect(holdVerdict).toHaveBeenCalledWith("download", 100);
    });

    it("says an uninstall is under way from the press until the removal answers", async () => {
      let finish!: (value: unknown) => void;
      vi.mocked(backend.removeRom).mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }) as never,
      );
      const result = renderLaunch();
      expect(result.current.uninstallUnderWay).toBe(false);

      let removal!: Promise<void>;
      act(() => {
        removal = result.current.handleUninstallClick();
      });
      expect(result.current.uninstallUnderWay).toBe(true);

      await act(async () => {
        finish({ success: false, message: "In use" });
        await removal;
      });
      expect(result.current.uninstallUnderWay).toBe(false);
    });

    it("does nothing before the page's detail names a ROM", async () => {
      const result = renderLaunch({ romId: null });

      await act(async () => {
        await result.current.handleUninstallClick();
      });

      expect(startAction).not.toHaveBeenCalled();
      expect(backend.removeRom).not.toHaveBeenCalled();
    });
  });
});
