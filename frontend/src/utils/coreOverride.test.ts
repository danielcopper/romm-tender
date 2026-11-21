import { describe, it, expect, beforeEach, vi } from "vitest";
import { toaster } from "../api/host";
import * as backend from "../api/backend";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";
import { mountPruneLeaseOwner, releasePruneLeasesByOwner } from "./pruneLease";
import { executeGameCoreChange } from "./coreOverride";

vi.mock("../api/backend", () => ({
  setGameCore: vi.fn(),
  clearGameCore: vi.fn(),
  debugLog: vi.fn().mockResolvedValue(undefined),
  logWarn: vi.fn(),
}));

vi.mock("./steamShortcuts", () => ({
  setLaunchOptionsConfirmed: vi.fn().mockResolvedValue(true),
}));

describe("coreOverride — executeGameCoreChange", () => {
  const leaseOwner = "test-core-owner";

  beforeEach(() => {
    mountPruneLeaseOwner(leaseOwner);
    vi.mocked(backend.setGameCore).mockReset();
    vi.mocked(backend.clearGameCore).mockReset();
    vi.mocked(backend.debugLog).mockReset();
    vi.mocked(setLaunchOptionsConfirmed).mockReset().mockResolvedValue(true);
    vi.mocked(toaster.toast).mockReset();
  });

  it("names the pinned emulator in its toast by the name it is given", async () => {
    vi.mocked(backend.setGameCore).mockResolvedValue({ success: true });

    await executeGameCoreChange({ romId: 42, coreLabel: "LRPS2", coreName: "LRPS2 / PCSX2", leaseOwner });

    expect(backend.setGameCore).toHaveBeenCalledWith(42, "LRPS2");
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Core set to LRPS2 / PCSX2" }));
  });

  it("pins a core override and writes nothing to the shortcut on success", async () => {
    vi.mocked(backend.setGameCore).mockResolvedValue({ success: true });
    const onSuccess = vi.fn();

    await executeGameCoreChange({
      romId: 42,
      coreLabel: "Snes9x",
      leaseOwner,
      onSuccess,
    });
    expect(backend.setGameCore).toHaveBeenCalledWith(42, "Snes9x");
    // The shortcut carries its appId; the next start asks with the pin.
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Core set to Snes9x" }));
    expect(onSuccess).toHaveBeenCalled();
  });

  it("pins a core override without launch options (uninstalled ROM)", async () => {
    vi.mocked(backend.setGameCore).mockResolvedValue({
      success: true,
    });
    const onSuccess = vi.fn();

    await executeGameCoreChange({
      romId: 42,
      coreLabel: "Genesis Plus GX",
      leaseOwner,
      onSuccess,
    });
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Core set to Genesis Plus GX" }));
    expect(onSuccess).toHaveBeenCalled();
  });

  it("clears a core override and reverts to following system core", async () => {
    vi.mocked(backend.clearGameCore).mockResolvedValue({ success: true });
    const onSuccess = vi.fn();

    await executeGameCoreChange({
      romId: 42,
      coreLabel: null,
      leaseOwner,
      onSuccess,
    });
    expect(backend.clearGameCore).toHaveBeenCalledWith(42);
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Now following the system core" }));
    expect(onSuccess).toHaveBeenCalled();
  });

  it("toasts failure message on set failure", async () => {
    vi.mocked(backend.setGameCore).mockResolvedValue({
      success: false,
      reason: "core_unavailable",
      message: "Core binary missing",
    });
    const onSuccess = vi.fn();

    const result = await executeGameCoreChange({
      romId: 42,
      coreLabel: "Snes9x",
      leaseOwner,
      onSuccess,
    });
    expect(result).toEqual({ status: "failed", message: "Core binary missing" });
    expect(backend.logWarn).toHaveBeenCalledWith(
      "Core selection: refused for romId=42 (coreLabel=Snes9x): reason=core_unavailable message=Core binary missing",
    );
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Core binary missing" }));
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("toasts 'Failed to reset core' on a refused clear without message", async () => {
    vi.mocked(backend.clearGameCore).mockResolvedValue({
      success: false,
      reason: "not_found",
      message: "",
    });
    const onSuccess = vi.fn();

    const result = await executeGameCoreChange({
      romId: 42,
      coreLabel: null,
      leaseOwner,
      onSuccess,
    });
    expect(result).toEqual({ status: "failed", message: "Failed to reset core" });
    expect(backend.logWarn).toHaveBeenCalledWith(
      "Core selection: refused for romId=42 (clear): reason=not_found message=Failed to reset core",
    );
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Failed to reset core" }));
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("toasts 'Failed to reset core' on a clear that throws", async () => {
    vi.mocked(backend.clearGameCore).mockRejectedValue(new Error("RPC failed"));

    const result = await executeGameCoreChange({ romId: 42, coreLabel: null, leaseOwner });

    expect(result).toEqual({ status: "failed", message: "Failed to reset core" });
    expect(backend.logWarn).toHaveBeenCalledWith("Core selection: unexpected error: Error: RPC failed");
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Failed to reset core" }));
  });

  it("logs the request and the answer under the caller's tag", async () => {
    vi.mocked(backend.setGameCore).mockResolvedValue({ success: true });
    vi.mocked(backend.clearGameCore).mockResolvedValue({ success: false, reason: "not_found", message: "" });

    const setResult = await executeGameCoreChange({
      romId: 42,
      coreLabel: "Snes9x",
      leaseOwner,
      logTag: "handleChangeGameCore",
    });
    const clearResult = await executeGameCoreChange({
      romId: 42,
      coreLabel: null,
      leaseOwner,
      logTag: "handleResetGameCore",
    });

    expect(setResult).toEqual({ status: "applied" });
    expect(clearResult).toEqual({ status: "failed", message: "Failed to reset core" });
    expect(backend.logWarn).toHaveBeenCalledWith(
      "handleResetGameCore: refused for romId=42 (clear): reason=not_found message=Failed to reset core",
    );
    expect(vi.mocked(backend.debugLog).mock.calls.map(([line]) => line)).toEqual([
      "handleChangeGameCore: romId=42 coreLabel=Snes9x",
      "handleChangeGameCore: result success=true",
      "handleResetGameCore: romId=42",
      "handleResetGameCore: result success=false",
    ]);
  });

  it("logs, without a toast, a set that fails after the page was left, naming the game and the pick", async () => {
    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.setGameCore).mockImplementationOnce(async () => {
      await releasePruneLeasesByOwner(leaseOwner);
      throw new Error("RPC failed");
    });

    const result = await executeGameCoreChange({ romId: 42, coreLabel: "Snes9x", leaseOwner });

    expect(result).toEqual({ status: "cancelled" });
    expect(toaster.toast).not.toHaveBeenCalled();
    expect(backend.logWarn).toHaveBeenCalledWith(
      "Core selection: failed after the page was left for romId=42 (coreLabel=Snes9x): Error: RPC failed",
    );
  });

  it("logs, without a toast, a clear that fails after the page was left, naming the game", async () => {
    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.clearGameCore).mockImplementationOnce(async () => {
      await releasePruneLeasesByOwner(leaseOwner);
      throw new Error("RPC dropped after teardown");
    });

    const result = await executeGameCoreChange({ romId: 42, coreLabel: null, leaseOwner, logTag: "clearTag" });

    expect(result).toEqual({ status: "cancelled" });
    expect(toaster.toast).not.toHaveBeenCalled();
    expect(backend.logWarn).toHaveBeenCalledWith(
      "clearTag: failed after the page was left for romId=42 (clear): Error: RPC dropped after teardown",
    );
  });

  it("toasts generic failure on unexpected exception", async () => {
    vi.mocked(backend.setGameCore).mockRejectedValue(new Error("RPC failed"));

    const result = await executeGameCoreChange({
      romId: 42,
      coreLabel: "Snes9x",
      leaseOwner,
      logTag: "customCoreTag",
    });
    expect(result).toEqual({ status: "failed", message: "Failed to set core" });
    expect(backend.logWarn).toHaveBeenCalledWith("customCoreTag: unexpected error: Error: RPC failed");
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Failed to set core" }));
  });

  const runSnes9xPick = (logTag: string, onSuccess: () => Promise<void>) =>
    executeGameCoreChange({
      romId: 42,
      coreLabel: "Snes9x",
      leaseOwner,
      logTag,
      onSuccess,
    });

  it("keeps status applied without a false failure toast when post-commit onSuccess throws", async () => {
    vi.mocked(backend.setGameCore).mockResolvedValue({ success: true });
    vi.mocked(backend.logWarn).mockClear();

    const result = await runSnes9xPick("customCoreTag", () => Promise.reject(new Error("refresh boom")));

    expect(result).toEqual({ status: "applied" });
    expect(toaster.toast).toHaveBeenCalledTimes(1);
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Core set to Snes9x" }));
    expect(backend.logWarn).toHaveBeenCalledWith("customCoreTag: post-change refresh error: Error: refresh boom");
  });

  it("returns cancelled quietly when post-commit onSuccess hits prune lease cancellation", async () => {
    const { PruneLeaseAdmissionCancelled } = await import("./pruneLease");
    vi.mocked(backend.setGameCore).mockResolvedValue({ success: true });
    vi.mocked(backend.logWarn).mockClear();

    const result = await runSnes9xPick("handleChangeGameCore", () =>
      Promise.reject(new PruneLeaseAdmissionCancelled("unmounted")),
    );

    expect(result).toEqual({ status: "cancelled" });
    expect(backend.logWarn).not.toHaveBeenCalled();
    expect(backend.debugLog).toHaveBeenCalledWith("handleChangeGameCore: continuation was cancelled: Error: unmounted");
  });
});
