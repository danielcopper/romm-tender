import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import { toaster } from "../api/host";
import * as backend from "../api/backend";
import { reportServerReachable } from "./connectionState";
import { getMigrationState } from "./migrationStore";
import { SERVER_UNREACHABLE_TOAST_BODY } from "./saveSetup";
import { LOCAL_CALL_LIMIT_MS, SERVER_CALL_LIMIT_MS, SYNC_WAIT_MORE_MS } from "./launchGate";
import { ensureTrackingConfiguredBigPicture, makeLaunchGateOps, runPreLaunchSync } from "./launchGateOps";
import { TimeoutError } from "./withTimeout";
import type { LaunchPrompts } from "./launchVerdict";
import type { SaveSetupInfo, SyncConflict } from "../types";

type AskSyncWait = LaunchPrompts["askSyncWait"];

vi.mock("../api/backend", () => ({
  checkCoreChange: vi.fn(),
  checkLocalDrift: vi.fn(),
  checkStartSource: vi.fn(),
  confirmSlotChoice: vi.fn(),
  debugLog: vi.fn(() => Promise.resolve()),
  getInstalledRom: vi.fn(),
  getSaveSetupInfo: vi.fn(),
  isSaveTrackingConfigured: vi.fn(),
  logError: vi.fn(),
  preLaunchSync: vi.fn(),
  preLaunchSyncState: vi.fn(),
  probeReachability: vi.fn(),
  stopPreLaunchSync: vi.fn(),
}));

vi.mock("./connectionState", () => ({
  reportServerReachable: vi.fn(),
}));

vi.mock("./migrationStore", () => ({
  getMigrationState: vi.fn(() => ({ pending: false })),
}));

const ROM_ID = 42;
const TAG = "TestPath";

const conflict: SyncConflict = {
  type: "sync_conflict",
  rom_id: ROM_ID,
  filename: "save.srm",
  server_save_id: 7,
  server_updated_at: "2026-01-01T00:00:00Z",
  server_size: 1024,
  local_path: "/local/save.srm",
  local_hash: "abc",
  local_mtime: "2026-01-01T00:00:00Z",
  local_size: 1024,
  created_at: "2026-01-01T00:00:00Z",
};

const setupInfo = (overrides: Partial<SaveSetupInfo> = {}): SaveSetupInfo => ({
  has_local_saves: false,
  local_files: [],
  server_slots: [],
  default_slot: "slot1",
  slot_confirmed: false,
  active_slot: null,
  recommended_action: "auto_confirm_default",
  ...overrides,
});

/** The options of a pre-launch sync on a path that toasts (or not); `askSyncWait` is asked past the limit. */
const syncOptions = (toastSyncResult: boolean, askSyncWait: AskSyncWait = vi.fn()) => ({
  tag: TAG,
  toastSyncResult,
  askSyncWait,
});

const toastBodies = (): string[] => vi.mocked(toaster.toast).mock.calls.map(([arg]) => (arg as { body: string }).body);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("runPreLaunchSync", () => {
  it("a benign skip is a success, without a toast even where the path toasts", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({
      success: false,
      message: "skip",
      reason: "savefiles_in_content_dir",
      uploaded: 1,
    });

    expect(await runPreLaunchSync(ROM_ID, syncOptions(true))).toEqual({
      success: true,
      message: "skip",
    });
    expect(toaster.toast).not.toHaveBeenCalled();
    expect(backend.debugLog).toHaveBeenCalledWith(
      `${TAG}: pre-launch sync skipped (savefiles_in_content_dir) — launching`,
    );
  });

  it("conflicts pass through with the sync's own success and message", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({
      success: false,
      message: "c",
      conflicts: [conflict],
      uploaded: 1,
    });

    expect(await runPreLaunchSync(ROM_ID, syncOptions(true))).toEqual({
      success: false,
      message: "c",
      conflicts: [conflict],
    });
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("an empty conflict list is not a conflict: a successful sync stays a success", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({ success: true, message: "ok", conflicts: [] });

    expect(await runPreLaunchSync(ROM_ID, syncOptions(false))).toEqual({
      success: true,
      message: "ok",
    });
  });

  it("a failure outside the benign set fails with the sync's message, and is logged with the tag", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({
      success: false,
      message: "Server offline",
      reason: "server_unreachable",
      errors: ["a", "b"],
      uploaded: 1,
    });

    expect(await runPreLaunchSync(ROM_ID, syncOptions(true))).toEqual({
      success: false,
      message: "Server offline",
    });
    expect(backend.debugLog).toHaveBeenCalledWith(
      `${TAG}: pre-launch sync failed: reason=server_unreachable errors=[a, b] message=Server offline`,
    );
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("a failure with no reason and no errors still fails", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({ success: false, message: "Device not registered" });

    expect(await runPreLaunchSync(ROM_ID, syncOptions(true))).toEqual({
      success: false,
      message: "Device not registered",
    });
    expect(backend.debugLog).toHaveBeenCalledWith(
      `${TAG}: pre-launch sync failed: reason= errors=[] message=Device not registered`,
    );
  });

  it("a throw is a failure with an empty message, logged with the tag", async () => {
    vi.mocked(backend.preLaunchSync).mockRejectedValue(new Error("boom"));

    expect(await runPreLaunchSync(ROM_ID, syncOptions(true))).toEqual({
      success: false,
      message: "",
    });
    expect(backend.logError).toHaveBeenCalledWith(
      `${TAG}: pre-launch sync failed (surfacing fallback confirm): Error: boom`,
    );
  });

  describe("past the deadline", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it("a sync that never answers is not given up: at the limit the user is asked why it waits", async () => {
      vi.mocked(backend.preLaunchSync).mockReturnValue(new Promise(() => {}));
      vi.mocked(backend.preLaunchSyncState).mockResolvedValue({ state: "waiting_for_rom" });
      const askSyncWait = vi.fn<AskSyncWait>(() => new Promise(() => {}));
      let outcome: unknown = "pending";
      runPreLaunchSync(ROM_ID, syncOptions(true, askSyncWait)).then(
        (value) => (outcome = value),
        (e: unknown) => (outcome = e),
      );

      await vi.advanceTimersByTimeAsync(SERVER_CALL_LIMIT_MS - 1);
      expect(askSyncWait).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);

      expect(backend.preLaunchSyncState).toHaveBeenCalledWith(ROM_ID);
      expect(askSyncWait).toHaveBeenCalledWith({ kind: "this_game_busy" }, expect.any(Promise));
      await vi.advanceTimersByTimeAsync(60000);
      expect(outcome).toBe("pending");
      expect(backend.logError).not.toHaveBeenCalled();
    });

    it("a sync that answers just inside the deadline is taken", async () => {
      let answer!: (v: { success: boolean; message: string }) => void;
      vi.mocked(backend.preLaunchSync).mockReturnValue(new Promise((resolve) => (answer = resolve)));

      const outcome = runPreLaunchSync(ROM_ID, syncOptions(false));
      await vi.advanceTimersByTimeAsync(SERVER_CALL_LIMIT_MS - 1);
      answer({ success: true, message: "late but in time" });

      expect(await outcome).toEqual({ success: true, message: "late but in time" });
    });
  });

  describe("past the limit", () => {
    type SyncResult = Awaited<ReturnType<typeof backend.preLaunchSync>>;

    function held<T>(): { promise: Promise<T>; settle: (value: T) => void; fail: (e: unknown) => void } {
      let settle!: (value: T) => void;
      let fail!: (e: unknown) => void;
      const promise = new Promise<T>((resolve, reject) => {
        settle = resolve;
        fail = reject;
      });
      return { promise, settle, fail };
    }

    /** Start the sync, let it run past the limit, and answer the question with `choices` in turn. */
    async function pastTheLimit(choices: Awaited<ReturnType<AskSyncWait>>[], toastSyncResult = true) {
      const sync = held<SyncResult>();
      vi.mocked(backend.preLaunchSync).mockReturnValue(sync.promise);
      const finishes: Promise<string>[] = [];
      const askSyncWait = vi.fn<AskSyncWait>((_reason, finished) => {
        finishes.push(finished);
        const choice = choices.shift();
        return choice === undefined ? new Promise(() => {}) : Promise.resolve(choice);
      });
      let outcome: unknown = "pending";
      void runPreLaunchSync(ROM_ID, syncOptions(toastSyncResult, askSyncWait)).then((value) => (outcome = value));
      await vi.advanceTimersByTimeAsync(SERVER_CALL_LIMIT_MS);
      return { sync, askSyncWait, finishes, outcome: () => outcome };
    }

    beforeEach(() => {
      vi.useFakeTimers();
      vi.mocked(backend.preLaunchSyncState).mockResolvedValue({ state: "waiting_for_sync", holder: "post_exit" });
      vi.mocked(backend.stopPreLaunchSync).mockResolvedValue({ success: true, stopped: true });
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it.each([
      [{ state: "waiting_for_sync", holder: "sync_all" } as const, { kind: "another_sync", holder: "sync_all" }],
      [{ state: "waiting_for_rom" } as const, { kind: "this_game_busy" }],
      [{ state: "syncing" } as const, { kind: "syncing" }],
      [{ state: "idle" } as const, { kind: "syncing" }],
    ])("names the reason the backend answers (%o)", async (state, reason) => {
      vi.mocked(backend.preLaunchSyncState).mockResolvedValue(state);

      const { askSyncWait } = await pastTheLimit([]);

      expect(askSyncWait).toHaveBeenCalledWith(reason, expect.any(Promise));
    });

    it("a state read that fails is Tender not answering, logged with the tag", async () => {
      vi.mocked(backend.preLaunchSyncState).mockRejectedValue(new Error("gone"));

      const { askSyncWait } = await pastTheLimit([]);

      expect(askSyncWait).toHaveBeenCalledWith({ kind: "not_answering" }, expect.any(Promise));
      expect(backend.logError).toHaveBeenCalledWith(
        `${TAG}: what the pre-launch sync is doing could not be read: Error: gone`,
      );
    });

    it("a state read with no answer within the local limit is Tender not answering", async () => {
      vi.mocked(backend.preLaunchSyncState).mockReturnValue(new Promise(() => {}));

      const { askSyncWait } = await pastTheLimit([]);
      await vi.advanceTimersByTimeAsync(LOCAL_CALL_LIMIT_MS - 1);
      expect(askSyncWait).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);

      expect(askSyncWait).toHaveBeenCalledWith({ kind: "not_answering" }, expect.any(Promise));
    });

    it("Wait takes an answer that comes within 15 s more as it would any answer", async () => {
      const { sync, outcome } = await pastTheLimit(["wait"]);
      await vi.advanceTimersByTimeAsync(SYNC_WAIT_MORE_MS - 1);
      sync.settle({ success: false, message: "c", conflicts: [conflict] });
      await vi.advanceTimersByTimeAsync(0);

      expect(outcome()).toEqual({ success: false, message: "c", conflicts: [conflict] });
    });

    it("Wait asks again when 15 s more bring no answer", async () => {
      const { askSyncWait } = await pastTheLimit(["wait"]);
      await vi.advanceTimersByTimeAsync(SYNC_WAIT_MORE_MS - 1);
      expect(askSyncWait).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);

      expect(askSyncWait).toHaveBeenCalledTimes(2);
      expect(backend.preLaunchSyncState).toHaveBeenCalledTimes(2);
    });

    it("Play Anyway stops the sync, waits for the stop, and answers play_anyway", async () => {
      const stop = held<{ success: true; stopped: boolean }>();
      vi.mocked(backend.stopPreLaunchSync).mockReturnValue(stop.promise);

      const { outcome } = await pastTheLimit(["play_anyway"]);
      expect(backend.stopPreLaunchSync).toHaveBeenCalledWith(ROM_ID);
      expect(outcome()).toBe("pending");
      stop.settle({ success: true, stopped: true });
      await vi.advanceTimersByTimeAsync(0);

      expect(outcome()).toEqual({ chose: "play_anyway", noAnswer: false });
    });

    it("Play Anyway after Tender did not answer sends the stop without waiting for it", async () => {
      vi.mocked(backend.preLaunchSyncState).mockRejectedValue(new Error("gone"));
      vi.mocked(backend.stopPreLaunchSync).mockReturnValue(new Promise(() => {}));

      const { outcome } = await pastTheLimit(["play_anyway"]);

      expect(backend.stopPreLaunchSync).toHaveBeenCalledWith(ROM_ID);
      expect(outcome()).toEqual({ chose: "play_anyway", noAnswer: true });
    });

    it("a stop that fails is logged, and the start still goes ahead", async () => {
      vi.mocked(backend.stopPreLaunchSync).mockRejectedValue(new Error("refused"));

      const { outcome } = await pastTheLimit(["play_anyway"]);

      expect(outcome()).toEqual({ chose: "play_anyway", noAnswer: false });
      expect(backend.logError).toHaveBeenCalledWith(`${TAG}: the pre-launch sync could not be stopped: Error: refused`);
    });

    it("Cancel answers cancel and stops nothing; the late answer is toasted and re-read", async () => {
      const announced = vi.fn();
      globalThis.addEventListener("romm_data_changed", announced);
      try {
        const { sync, outcome } = await pastTheLimit(["cancel"]);
        expect(outcome()).toEqual({ chose: "cancel" });

        sync.settle({ success: true, message: "", uploaded: 0, downloaded: 1 });
        await vi.advanceTimersByTimeAsync(0);

        expect(backend.stopPreLaunchSync).not.toHaveBeenCalled();
        expect(toastBodies()).toEqual(["Saves downloaded from RomM"]);
        expect(announced).toHaveBeenCalledTimes(1);
      } finally {
        globalThis.removeEventListener("romm_data_changed", announced);
      }
    });

    it("a late answer on a path that does not toast is only re-read", async () => {
      const { sync } = await pastTheLimit(["cancel"], false);
      sync.settle({ success: true, message: "", uploaded: 0, downloaded: 1 });
      await vi.advanceTimersByTimeAsync(0);

      expect(toaster.toast).not.toHaveBeenCalled();
    });

    it("a late failure after Cancel is logged", async () => {
      const { sync } = await pastTheLimit(["cancel"]);
      sync.fail(new Error("boom"));
      await vi.advanceTimersByTimeAsync(0);

      expect(backend.logError).toHaveBeenCalledWith(
        `${TAG}: the pre-launch sync the start no longer waits for failed: Error: boom`,
      );
      expect(toaster.toast).not.toHaveBeenCalled();
    });

    it.each([
      ["a success", { success: true, message: "" }, "ready"],
      ["a benign skip", { success: false, message: "", reason: "save_shape_unsupported" as const }, "ready"],
      ["a conflict", { success: true, message: "", conflicts: [conflict] }, "answered"],
      ["a failure", { success: false, message: "Server offline" }, "answered"],
    ])("the question learns that %s is %s", async (_what, result, finish) => {
      const { sync, finishes } = await pastTheLimit([]);
      sync.settle(result);

      expect(await finishes[0]).toBe(finish);
    });

    it("the question learns that a sync that threw needs acting on", async () => {
      const { sync, finishes } = await pastTheLimit([]);
      sync.fail(new Error("boom"));

      expect(await finishes[0]).toBe("answered");
    });

    it("answered goes on with the sync's own answer", async () => {
      const { sync, outcome } = await pastTheLimit(["answered"]);
      sync.settle({ success: false, message: "Server offline" });
      await vi.advanceTimersByTimeAsync(0);

      expect(outcome()).toEqual({ success: false, message: "Server offline" });
    });
  });

  it("a successful sync that moved saves toasts what it moved where the path toasts", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({ success: true, message: "", uploaded: 1, downloaded: 0 });

    expect(await runPreLaunchSync(ROM_ID, syncOptions(true))).toEqual({
      success: true,
      message: "",
    });
    expect(toastBodies()).toEqual(["Saves uploaded to RomM"]);
  });

  it("a successful sync that moved saves toasts nothing where the path does not toast", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({ success: true, message: "", uploaded: 1, downloaded: 0 });

    expect(await runPreLaunchSync(ROM_ID, syncOptions(false))).toEqual({
      success: true,
      message: "",
    });
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("a successful sync that moved nothing toasts nothing", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({ success: true, message: "", uploaded: 0, downloaded: 0 });

    await runPreLaunchSync(ROM_ID, syncOptions(true));
    expect(toaster.toast).not.toHaveBeenCalled();
  });
});

describe("ensureTrackingConfiguredBigPicture", () => {
  let tabSwitch: Mock<(e: Event) => void>;

  beforeEach(() => {
    tabSwitch = vi.fn();
    globalThis.addEventListener("romm_tab_switch", tabSwitch);
    return () => globalThis.removeEventListener("romm_tab_switch", tabSwitch);
  });

  it("a configured ROM proceeds without asking for setup", async () => {
    vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: true, active_slot: "slot1" });

    expect(await ensureTrackingConfiguredBigPicture(ROM_ID)).toBe("proceed");
    expect(backend.getSaveSetupInfo).not.toHaveBeenCalled();
  });

  it("a failed tracking check reads as configured", async () => {
    vi.mocked(backend.isSaveTrackingConfigured).mockRejectedValue(new Error("net"));

    expect(await ensureTrackingConfiguredBigPicture(ROM_ID)).toBe("proceed");
    expect(backend.getSaveSetupInfo).not.toHaveBeenCalled();
  });

  it("a failed setup read proceeds, switching nothing", async () => {
    vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: false, active_slot: null });
    vi.mocked(backend.getSaveSetupInfo).mockRejectedValue(new Error("net"));

    expect(await ensureTrackingConfiguredBigPicture(ROM_ID)).toBe("proceed");
    expect(tabSwitch).not.toHaveBeenCalled();
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("an adoptable default is adopted and the start proceeds", async () => {
    vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: false, active_slot: null });
    vi.mocked(backend.getSaveSetupInfo).mockResolvedValue(setupInfo());
    vi.mocked(backend.confirmSlotChoice).mockResolvedValue({
      success: true,
      needs_conflict_resolution: false,
      message: "",
    });

    expect(await ensureTrackingConfiguredBigPicture(ROM_ID)).toBe("proceed");
    expect(backend.confirmSlotChoice).toHaveBeenCalledWith(ROM_ID, "slot1", false, null, false);
    expect(tabSwitch).not.toHaveBeenCalled();
  });

  it("a setup that cannot be settled here aborts with a toast and switches the page to its saves tab", async () => {
    vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: false, active_slot: null });
    vi.mocked(backend.getSaveSetupInfo).mockResolvedValue(setupInfo({ recommended_action: "server_unreachable" }));

    expect(await ensureTrackingConfiguredBigPicture(ROM_ID)).toBe("abort");
    expect(toastBodies()).toEqual([SERVER_UNREACHABLE_TOAST_BODY]);
    expect(backend.confirmSlotChoice).not.toHaveBeenCalled();
    expect((tabSwitch.mock.calls[0]?.[0] as CustomEvent | undefined)?.detail).toEqual({ tab: "saves" });
  });

  it("a game whose saves no sync carries starts with no setup step and no toast, though RomM holds saves (#2153 D1)", async () => {
    vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: false, active_slot: null });
    vi.mocked(backend.getSaveSetupInfo).mockResolvedValue(
      setupInfo({
        recommended_action: "sync_unavailable",
        reason: "save_shape_unsupported",
        message: "Save sync is unavailable: this emulator keeps one save card that all games share.",
        server_slots: [{ slot: "slot1", saves: [], count: 1, latest_updated_at: "2026-01-01T00:00:00Z" }],
      }),
    );

    expect(await ensureTrackingConfiguredBigPicture(ROM_ID)).toBe("proceed");
    expect(backend.confirmSlotChoice).not.toHaveBeenCalled();
    expect(toaster.toast).not.toHaveBeenCalled();
    expect(tabSwitch).not.toHaveBeenCalled();
  });

  it("a throw from a side effect after the read is not swallowed into proceed", async () => {
    vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: false, active_slot: null });
    vi.mocked(backend.getSaveSetupInfo).mockResolvedValue(setupInfo());
    vi.mocked(backend.confirmSlotChoice).mockRejectedValue(new Error("confirm blew up"));

    await expect(ensureTrackingConfiguredBigPicture(ROM_ID)).rejects.toThrow("confirm blew up");
  });

  describe("past the deadline", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it("a setup read that never answers is let through as a TimeoutError, not read as proceed", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: false, active_slot: null });
      vi.mocked(backend.getSaveSetupInfo).mockReturnValue(new Promise(() => {}));

      const outcome = expect(ensureTrackingConfiguredBigPicture(ROM_ID)).rejects.toBeInstanceOf(TimeoutError);
      await vi.advanceTimersByTimeAsync(SERVER_CALL_LIMIT_MS);

      await outcome;
      expect(tabSwitch).not.toHaveBeenCalled();
    });
  });
});

describe("makeLaunchGateOps", () => {
  type LaunchGateOpsOptions = Parameters<typeof makeLaunchGateOps>[1];
  const options = (overrides: Partial<LaunchGateOpsOptions> = {}): LaunchGateOpsOptions => ({
    tag: TAG,
    toastSyncResult: false,
    ensureTrackingConfigured: vi.fn(() => Promise.resolve("proceed" as const)),
    confirmCoreChange: vi.fn(() => Promise.resolve(true)),
    confirmUncheckedStartingSource: vi.fn(() => Promise.resolve(true)),
    askSyncWait: vi.fn(),
    ...overrides,
  });

  it("migrationPending reads the migration store", () => {
    vi.mocked(getMigrationState).mockReturnValueOnce({ pending: true } as ReturnType<typeof getMigrationState>);
    expect(makeLaunchGateOps(ROM_ID, options()).migrationPending()).toBe(true);
  });

  it("hasLaunchTarget asks for this ROM, and logs a failed check with the tag", async () => {
    vi.mocked(backend.getInstalledRom).mockRejectedValue(new Error("net"));

    expect(await makeLaunchGateOps(ROM_ID, options()).hasLaunchTarget()).toBe(true);
    expect(backend.getInstalledRom).toHaveBeenCalledWith(ROM_ID);
    expect(backend.logError).toHaveBeenCalledWith(expect.stringContaining(`${TAG} launch-target check threw`));
  });

  it("readStartingSource asks with the tag, and the unchecked-source question is the path's own", async () => {
    vi.mocked(backend.checkStartSource).mockRejectedValue(new Error("net"));
    const confirmUncheckedStartingSource = vi.fn(() => Promise.resolve(false));
    const ops = makeLaunchGateOps(ROM_ID, options({ confirmUncheckedStartingSource }));

    expect(await ops.readStartingSource()).toEqual({ checked: false });
    expect(backend.logError).toHaveBeenCalledWith(expect.stringContaining(`${TAG} start-source check got no answer`));
    expect(await ops.confirmUncheckedStartingSource()).toBe(false);
    expect(confirmUncheckedStartingSource).toHaveBeenCalledTimes(1);
  });

  it("the tracking step is the path's own", async () => {
    const opts = options({
      ensureTrackingConfigured: vi.fn(() => Promise.resolve("abort" as const)),
    });
    const ops = makeLaunchGateOps(ROM_ID, opts);

    expect(await ops.ensureTrackingConfigured()).toBe("abort");
  });

  it("checkCoreChange asks through confirmCoreChange when core changed", async () => {
    vi.mocked(backend.checkCoreChange).mockResolvedValue({
      changed: true,
      old_label: "Genesis Plus GX",
      new_label: "Picodrive",
    });
    const confirmCoreChange = vi.fn(() => Promise.resolve(false));
    const ops = makeLaunchGateOps(ROM_ID, options({ confirmCoreChange }));

    expect(await ops.checkCoreChange()).toBe(false);
    expect(confirmCoreChange).toHaveBeenCalledWith("Genesis Plus GX", "Picodrive");
  });

  it("checkCoreChange logs and assumes unchanged when check throws", async () => {
    vi.mocked(backend.checkCoreChange).mockRejectedValue(new Error("socket down"));
    const confirmCoreChange = vi.fn();
    const ops = makeLaunchGateOps(ROM_ID, options({ confirmCoreChange }));

    expect(await ops.checkCoreChange()).toBe(true);
    expect(backend.logError).toHaveBeenCalledWith(
      `${TAG}: core-change check failed (assuming unchanged): Error: socket down`,
    );
    expect(confirmCoreChange).not.toHaveBeenCalled();
  });

  it("checkReachability reports an answered probe to the connection store", async () => {
    vi.mocked(backend.probeReachability).mockResolvedValue({ online: false });

    expect(await makeLaunchGateOps(ROM_ID, options()).checkReachability()).toBe(false);
    expect(reportServerReachable).toHaveBeenCalledWith(false);
  });

  it("checkReachability treats a thrown probe as offline without telling the store", async () => {
    vi.mocked(backend.probeReachability).mockRejectedValue(new Error("bridge"));

    expect(await makeLaunchGateOps(ROM_ID, options()).checkReachability()).toBe(false);
    expect(reportServerReachable).not.toHaveBeenCalled();
    expect(backend.logError).toHaveBeenCalledWith(
      `${TAG}: reachability probe failed (treating as offline): Error: bridge`,
    );
  });

  it("checkReachability lets a probe that never answers through as a TimeoutError, without telling the store", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(backend.probeReachability).mockReturnValue(new Promise(() => {}));

      const outcome = expect(makeLaunchGateOps(ROM_ID, options()).checkReachability()).rejects.toBeInstanceOf(
        TimeoutError,
      );
      await vi.advanceTimersByTimeAsync(LOCAL_CALL_LIMIT_MS);

      await outcome;
      expect(reportServerReachable).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("checkLocalDrift passes a drift through, and reads a thrown check as not drifted", async () => {
    const ops = makeLaunchGateOps(ROM_ID, options());

    vi.mocked(backend.checkLocalDrift).mockResolvedValueOnce({
      drifted: true,
      only_here: false,
      stranding: "unsynced",
      rom_id: ROM_ID,
    });
    expect(await ops.checkLocalDrift()).toBe(true);
    expect(backend.checkLocalDrift).toHaveBeenCalledWith(ROM_ID);

    vi.mocked(backend.checkLocalDrift).mockRejectedValueOnce(new Error("net"));
    expect(await ops.checkLocalDrift()).toBe(false);
    expect(backend.logError).toHaveBeenCalledWith(
      `${TAG}: local-drift check failed (treating as not-drifted): Error: net`,
    );
  });

  it("preLaunchSync tells the path the sync is starting before it runs, then answers with the shaped outcome", async () => {
    const order: string[] = [];
    const onSyncStart = vi.fn(() => {
      order.push("start");
    });
    vi.mocked(backend.preLaunchSync).mockImplementation(() => {
      order.push("sync");
      return Promise.resolve({ success: true, message: "", uploaded: 0, downloaded: 2 });
    });

    const outcome = await makeLaunchGateOps(ROM_ID, options({ onSyncStart, toastSyncResult: true })).preLaunchSync();

    expect(order).toEqual(["start", "sync"]);
    expect(backend.preLaunchSync).toHaveBeenCalledWith(ROM_ID);
    expect(outcome).toEqual({ success: true, message: "" });
    expect(toastBodies()).toEqual(["Saves downloaded from RomM"]);
  });

  it("preLaunchSync runs without a start hook", async () => {
    vi.mocked(backend.preLaunchSync).mockRejectedValue(new Error("boom"));

    expect(await makeLaunchGateOps(ROM_ID, options()).preLaunchSync()).toEqual({ success: false, message: "" });
  });

  it("preLaunchSync: a throwing onSyncStart hook is caught and logged, and sync still runs", async () => {
    const onSyncStart = vi.fn(() => {
      throw new Error("hook boom");
    });
    const ops = makeLaunchGateOps(ROM_ID, options({ onSyncStart }));
    vi.mocked(backend.preLaunchSync).mockResolvedValue({
      success: true,
      synced: 1,
      uploaded: 0,
      downloaded: 0,
      message: "ok",
    });

    const outcome = await ops.preLaunchSync();

    expect("success" in outcome && outcome.success).toBe(true);
    expect(backend.logError).toHaveBeenCalledWith(`${TAG}: onSyncStart threw: Error: hook boom`);
    expect(backend.preLaunchSync).toHaveBeenCalledWith(ROM_ID);
  });
});
