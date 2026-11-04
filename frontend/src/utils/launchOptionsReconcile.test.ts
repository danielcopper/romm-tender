import { describe, it, expect, beforeEach, vi } from "vitest";
import { batchConfirmLaunchOptions, NOT_RESPONDING_START, prepareStart } from "./launchOptionsReconcile";
import * as steamShortcuts from "./steamShortcuts";
import * as backend from "../api/backend";
import { capturePruneLeaseAdmission, mountPruneLeaseOwner, releasePruneLeasesByOwner } from "./pruneLease";

vi.mock("./steamShortcuts");
vi.mock("../api/backend");

function items(n: number): { app_id: number; launch_options: string }[] {
  return Array.from({ length: n }, (_, i) => ({ app_id: i + 1, launch_options: `cmd ${i + 1}` }));
}

describe("batchConfirmLaunchOptions", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockResolvedValue(true);
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
  });

  it("no-ops on an empty list (no confirm, no log)", async () => {
    await batchConfirmLaunchOptions([], "startup_reconcile");
    expect(vi.mocked(steamShortcuts.setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logError)).not.toHaveBeenCalled();
  });

  it("no-ops on a non-array input (defensive guard)", async () => {
    await batchConfirmLaunchOptions(undefined as unknown as { app_id: number; launch_options: string }[], "ctx");
    expect(vi.mocked(steamShortcuts.setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logError)).not.toHaveBeenCalled();
  });

  it("confirms every item across batches (12 items -> two batches of 10 + 2)", async () => {
    await batchConfirmLaunchOptions(items(12), "startup_reconcile");
    expect(vi.mocked(steamShortcuts.setLaunchOptionsConfirmed)).toHaveBeenCalledTimes(12);
    expect(vi.mocked(steamShortcuts.setLaunchOptionsConfirmed)).toHaveBeenNthCalledWith(
      1,
      1,
      "cmd 1",
      undefined,
      expect.any(Function),
    );
    expect(vi.mocked(steamShortcuts.setLaunchOptionsConfirmed)).toHaveBeenNthCalledWith(
      12,
      12,
      "cmd 12",
      undefined,
      expect.any(Function),
    );
    expect(vi.mocked(backend.logError)).not.toHaveBeenCalled();
  });

  describe("the batch's one reading of Steam's shortcut file", () => {
    beforeEach(() => {
      vi.mocked(backend.getShortcutLaunchOptions).mockResolvedValue({ success: true, launch_options: [] });
    });

    it("is one backend call per batch, for that batch's app IDs, however many of its confirms fail", async () => {
      vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockImplementation(
        async (_appId, _value, _timeout, readFile) => {
          await readFile!();
          return false;
        },
      );

      await batchConfirmLaunchOptions(items(12), "startup_reconcile");

      expect(vi.mocked(backend.getShortcutLaunchOptions)).toHaveBeenCalledTimes(2);
      expect(vi.mocked(backend.getShortcutLaunchOptions)).toHaveBeenNthCalledWith(1, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
      expect(vi.mocked(backend.getShortcutLaunchOptions)).toHaveBeenNthCalledWith(2, [11, 12]);
    });

    it("hands every confirm of a batch the same reader, and the next batch another", async () => {
      await batchConfirmLaunchOptions(items(12), "startup_reconcile");

      const readers = vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mock.calls.map((call) => call[3]);
      expect(readers.every((reader) => typeof reader === "function")).toBe(true);
      expect(new Set(readers.slice(0, 10)).size).toBe(1);
      expect(new Set(readers.slice(10)).size).toBe(1);
      expect(readers[10]).not.toBe(readers[0]);
    });

    it("is not taken in a batch whose every confirm is settled without it", async () => {
      await batchConfirmLaunchOptions(items(12), "startup_reconcile");

      expect(vi.mocked(steamShortcuts.setLaunchOptionsConfirmed)).toHaveBeenCalledTimes(12);
      expect(vi.mocked(backend.getShortcutLaunchOptions)).not.toHaveBeenCalled();
    });
  });

  it("logs a non-vacuous error with the appId + context on a false confirm; still processes the rest", async () => {
    vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockImplementation((appId: number) =>
      Promise.resolve(appId !== 2),
    );
    await batchConfirmLaunchOptions(items(3), "startup_reconcile");
    expect(vi.mocked(steamShortcuts.setLaunchOptionsConfirmed)).toHaveBeenCalledTimes(3);
    expect(vi.mocked(backend.logError)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.logError)).toHaveBeenCalledWith(
      "startup_reconcile: failed to confirm launch options for appId 2",
    );
  });

  it("logs a non-vacuous error with the appId + context when a confirm throws; still processes the rest", async () => {
    vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockImplementation((appId: number) =>
      appId === 2 ? Promise.reject(new Error("boom")) : Promise.resolve(true),
    );
    await batchConfirmLaunchOptions(items(3), "startup_reconcile");
    expect(vi.mocked(steamShortcuts.setLaunchOptionsConfirmed)).toHaveBeenCalledTimes(3);
    expect(vi.mocked(backend.logError)).toHaveBeenCalledTimes(1);
    const msg = vi.mocked(backend.logError).mock.calls[0]![0];
    expect(msg).toContain("startup_reconcile: failed to set launch options for appId 2");
    expect(msg).toContain("boom");
  });

  it.each([
    { reread: "cmd 1", logged: [] },
    { reread: "old cmd", logged: ["startup_reconcile: failed to confirm launch options for appId 1"] },
  ])(
    "logs a failure for a report that misses the wait only when the re-read shows another value ($reread)",
    async ({ reread, logged }) => {
      const actual = await vi.importActual<typeof steamShortcuts>("./steamShortcuts");
      vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockImplementation(actual.setLaunchOptionsConfirmed);
      vi.useFakeTimers();
      let registrations = 0;
      vi.stubGlobal("SteamClient", {
        Apps: {
          SetAppLaunchOptions: vi.fn(),
          RegisterForAppDetails: (_appId: number, callback: (details: SteamAppDetails) => void) => {
            const launchOptions = ++registrations === 1 ? "old cmd" : reread;
            queueMicrotask(() => callback({ strLaunchOptions: launchOptions }));
            return { unregister: vi.fn() };
          },
        },
      });
      try {
        const applying = batchConfirmLaunchOptions(items(1), "startup_reconcile");
        await vi.advanceTimersByTimeAsync(2000);
        await applying;
      } finally {
        vi.useRealTimers();
      }

      expect(vi.mocked(backend.logError).mock.calls.map(([message]) => message)).toEqual(logged);
      expect(registrations).toBe(2);
    },
  );

  it("does not begin a later Steam batch after cancellation", async () => {
    let settle!: () => void;
    const firstBatch = new Promise<boolean>((resolve) => {
      settle = () => resolve(true);
    });
    vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockReturnValue(firstBatch);
    const controller = new AbortController();
    const applying = batchConfirmLaunchOptions(items(12), "setSystemCore", controller.signal);
    await Promise.resolve();
    expect(steamShortcuts.setLaunchOptionsConfirmed).toHaveBeenCalledTimes(10);

    controller.abort();
    settle();
    await applying;

    expect(steamShortcuts.setLaunchOptionsConfirmed).toHaveBeenCalledTimes(10);
  });
});

describe("prepareStart", () => {
  const APP_ID = "100";
  type Answer = Awaited<ReturnType<typeof backend.prepareGameStart>>;
  const prepared = (token = "launch-lease"): Answer => ({
    success: true,
    app_id: 100,
    launch_options: APP_ID,
    prune_lease_token: token,
  });

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockResolvedValue(true);
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
  });

  it("asks the backend to prepare the start and sets the shortcut's launch options back to its appId", async () => {
    vi.mocked(backend.prepareGameStart).mockResolvedValue(prepared());

    await expect(prepareStart(42, 100, "CustomPlayButton")).resolves.toEqual({ status: "ready" });

    expect(backend.prepareGameStart).toHaveBeenCalledWith(42);
    expect(steamShortcuts.setLaunchOptionsConfirmed).toHaveBeenCalledWith(100, APP_ID);
    expect(backend.releasePruneConflictLease).toHaveBeenCalledWith("launch-lease");
    expect(backend.logError).not.toHaveBeenCalled();
  });

  it("refuses with the game page's own words where the backend names the game's start", async () => {
    vi.mocked(backend.prepareGameStart).mockResolvedValue({
      success: false,
      reason: "cannot_start",
      message: "ROM 42 cannot start with PCSX2: environment_unread",
      start: { state: "cannot_start", reason: "not_installed", entry: "PCSX2" },
    });

    await expect(prepareStart(42, 100, "Watcher")).resolves.toEqual({
      status: "refused",
      sentence: "PCSX2 is not installed, so this game cannot start.",
    });
    expect(steamShortcuts.setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });

  it("refuses with the resolver's reason where it gives the entry no complete command", async () => {
    vi.mocked(backend.prepareGameStart).mockResolvedValue({
      success: false,
      reason: "cannot_start",
      message: "ROM 42 cannot start with Dolphin: environment_unknown",
      start: { state: "cannot_start", reason: "environment_unknown", entry: "Dolphin" },
    });

    await expect(prepareStart(42, 100, "Watcher")).resolves.toEqual({
      status: "refused",
      sentence: "Tender cannot start Dolphin yet: what it needs from RetroDECK's start is not known yet.",
    });
    expect(steamShortcuts.setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });

  it("refuses a game no emulator starts in the game page's words", async () => {
    vi.mocked(backend.prepareGameStart).mockResolvedValue({
      success: false,
      reason: "cannot_start",
      message: "ROM 42 has no emulator to start with",
      start: { state: "no_entry", reason: null, entry: null },
    });

    await expect(prepareStart(42, 100, "Watcher")).resolves.toEqual({
      status: "refused",
      sentence: "No emulator starts this game.",
    });
    expect(steamShortcuts.setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });

  it("refuses with the backend's own message where it names no start", async () => {
    vi.mocked(backend.prepareGameStart).mockResolvedValue({
      success: false,
      reason: "start_unavailable",
      message: "Tender has no runtime folder to hand the start to the launcher in",
    });

    await expect(prepareStart(42, 100, "Watcher")).resolves.toEqual({
      status: "refused",
      sentence: "Tender has no runtime folder to hand the start to the launcher in",
    });
    expect(backend.logError).toHaveBeenCalledWith(expect.stringContaining("Watcher: the start was refused"));
  });

  it("refuses a call that fails, in the error's own words, and starts no Steam write", async () => {
    vi.mocked(backend.prepareGameStart).mockRejectedValue(new Error("connection lost"));

    await expect(prepareStart(42, 100, "CustomPlayButton")).resolves.toEqual({
      status: "refused",
      sentence: "Couldn't start the game: connection lost",
    });
    expect(steamShortcuts.setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(backend.logError).toHaveBeenCalledWith(
      expect.stringContaining("CustomPlayButton: preparing the start failed"),
    );
  });

  it("keeps a Steam write that fails from stopping the start, and releases its lease", async () => {
    vi.mocked(backend.prepareGameStart).mockResolvedValue(prepared("write-failure-lease"));
    vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockRejectedValue(new Error("steam unavailable"));

    await expect(prepareStart(42, 100, "Watcher")).resolves.toEqual({ status: "ready" });

    expect(backend.releasePruneConflictLease).toHaveBeenCalledWith("write-failure-lease");
    expect(backend.logError).toHaveBeenCalledWith(
      expect.stringContaining("Watcher: setting the launch options failed"),
    );
  });

  it("keeps a Steam write Steam does not confirm from stopping the start", async () => {
    vi.mocked(backend.prepareGameStart).mockResolvedValue(prepared());
    vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockResolvedValue(false);

    await expect(prepareStart(42, 100, "Watcher")).resolves.toEqual({ status: "ready" });

    expect(backend.logError).toHaveBeenCalledWith(expect.stringContaining("launch options of appId 100 not confirmed"));
  });

  it("answers cancelled for a torn-down owner's admission, and never without an admission", async () => {
    let answer!: (value: Answer) => void;
    const fetched = new Promise<Answer>((resolve) => {
      answer = resolve;
    });
    vi.mocked(backend.prepareGameStart).mockReturnValue(fetched);
    mountPruneLeaseOwner("custom-play-button:100");
    const owned = prepareStart(42, 100, "CustomPlayButton", capturePruneLeaseAdmission("custom-play-button:100"));
    const unowned = prepareStart(42, 100, "Watcher");

    await releasePruneLeasesByOwner("custom-play-button:100");
    answer(prepared());

    await expect(owned).resolves.toEqual({ status: "cancelled" });
    await expect(unowned).resolves.toEqual({ status: "ready" });
    expect(steamShortcuts.setLaunchOptionsConfirmed).toHaveBeenCalledTimes(1);
  });

  it("refuses a preparation with no answer in time as Tender not responding, without a Steam write", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(backend.prepareGameStart).mockReturnValue(new Promise<never>(() => {}));

      const pending = prepareStart(42, 100, "CustomPlayButton");
      await vi.advanceTimersByTimeAsync(3000);
      await expect(pending).resolves.toEqual({ status: "refused", sentence: NOT_RESPONDING_START });

      expect(NOT_RESPONDING_START).toBe("Tender isn't responding — couldn't start the game.");
      expect(steamShortcuts.setLaunchOptionsConfirmed).not.toHaveBeenCalled();
      expect(backend.logError).toHaveBeenCalledWith(
        expect.stringContaining("CustomPlayButton: preparing the start timed out"),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("observes a lease-bearing answer that arrives after the timeout and releases it without a Steam write", async () => {
    vi.useFakeTimers();
    try {
      let resolveFetch!: (value: Answer) => void;
      vi.mocked(backend.prepareGameStart).mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveFetch = resolve;
          }),
      );

      const pending = prepareStart(42, 100, "Watcher");
      await vi.advanceTimersByTimeAsync(3000);
      await expect(pending).resolves.toEqual({ status: "refused", sentence: NOT_RESPONDING_START });
      expect(backend.releasePruneConflictLease).not.toHaveBeenCalled();

      resolveFetch(prepared("late-timeout-lease"));
      await vi.advanceTimersByTimeAsync(0);

      expect(backend.releasePruneConflictLease).toHaveBeenCalledWith("late-timeout-lease");
      expect(steamShortcuts.setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
