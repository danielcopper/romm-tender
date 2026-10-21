import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import { toaster } from "../api/host";
import * as backend from "../api/backend";
import { reportServerReachable } from "./connectionState";
import { getMigrationState } from "./migrationStore";
import { SERVER_UNREACHABLE_TOAST_BODY } from "./saveSetup";
import { LOCAL_CALL_LIMIT_MS, SERVER_CALL_LIMIT_MS } from "./launchGate";
import { ensureTrackingConfiguredBigPicture, makeLaunchGateOps, runPreLaunchSync } from "./launchGateOps";
import { TimeoutError } from "./withTimeout";
import type { SaveSetupInfo, SyncConflict } from "../types";

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
  probeReachability: vi.fn(),
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

    expect(await runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: true })).toEqual({
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

    expect(await runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: true })).toEqual({
      success: false,
      message: "c",
      conflicts: [conflict],
    });
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("an empty conflict list is not a conflict: a successful sync stays a success", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({ success: true, message: "ok", conflicts: [] });

    expect(await runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: false })).toEqual({
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

    expect(await runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: true })).toEqual({
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

    expect(await runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: true })).toEqual({
      success: false,
      message: "Device not registered",
    });
    expect(backend.debugLog).toHaveBeenCalledWith(
      `${TAG}: pre-launch sync failed: reason= errors=[] message=Device not registered`,
    );
  });

  it("a throw is a failure with an empty message, logged with the tag", async () => {
    vi.mocked(backend.preLaunchSync).mockRejectedValue(new Error("boom"));

    expect(await runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: true })).toEqual({
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

    it("a sync that never answers is let through as a TimeoutError, for the gate to answer", async () => {
      vi.mocked(backend.preLaunchSync).mockReturnValue(new Promise(() => {}));

      const outcome = expect(runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: true })).rejects.toBeInstanceOf(
        TimeoutError,
      );
      await vi.advanceTimersByTimeAsync(SERVER_CALL_LIMIT_MS);

      await outcome;
      expect(backend.logError).not.toHaveBeenCalled();
    });

    it("a sync that answers just inside the deadline is taken", async () => {
      let answer!: (v: { success: boolean; message: string }) => void;
      vi.mocked(backend.preLaunchSync).mockReturnValue(new Promise((resolve) => (answer = resolve)));

      const outcome = runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: false });
      await vi.advanceTimersByTimeAsync(SERVER_CALL_LIMIT_MS - 1);
      answer({ success: true, message: "late but in time" });

      expect(await outcome).toEqual({ success: true, message: "late but in time" });
    });
  });

  it("a successful sync that moved saves toasts what it moved where the path toasts", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({ success: true, message: "", uploaded: 1, downloaded: 0 });

    expect(await runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: true })).toEqual({
      success: true,
      message: "",
    });
    expect(toastBodies()).toEqual(["Saves uploaded to RomM"]);
  });

  it("a successful sync that moved saves toasts nothing where the path does not toast", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({ success: true, message: "", uploaded: 1, downloaded: 0 });

    expect(await runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: false })).toEqual({
      success: true,
      message: "",
    });
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("a successful sync that moved nothing toasts nothing", async () => {
    vi.mocked(backend.preLaunchSync).mockResolvedValue({ success: true, message: "", uploaded: 0, downloaded: 0 });

    await runPreLaunchSync(ROM_ID, { tag: TAG, toastSyncResult: true });
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

    expect(outcome.success).toBe(true);
    expect(backend.logError).toHaveBeenCalledWith(`${TAG}: onSyncStart threw: Error: hook boom`);
    expect(backend.preLaunchSync).toHaveBeenCalledWith(ROM_ID);
  });
});
