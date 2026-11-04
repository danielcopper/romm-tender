import { describe, it, expect, beforeEach, vi } from "vitest";
import { toaster } from "../api/host";
import * as backend from "../api/backend";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";
import { mountPruneLeaseOwner } from "./pruneLease";
import { executeGameCoreChange } from "./coreOverride";

vi.mock("../api/backend", () => ({
  setGameCore: vi.fn(),
  clearGameCore: vi.fn(),
  debugLog: vi.fn().mockResolvedValue(undefined),
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

    await executeGameCoreChange({
      romId: 42,
      coreLabel: "Snes9x",
      leaseOwner,
      onSuccess,
    });
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Core binary missing" }));
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("toasts 'Failed to set core' on a refused clear without message", async () => {
    vi.mocked(backend.clearGameCore).mockResolvedValue({
      success: false,
      reason: "not_found",
      message: "",
    });
    const onSuccess = vi.fn();

    await executeGameCoreChange({
      romId: 42,
      coreLabel: null,
      leaseOwner,
      onSuccess,
    });
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Failed to set core" }));
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("toasts 'Failed to reset core' on a clear that throws", async () => {
    vi.mocked(backend.clearGameCore).mockRejectedValue(new Error("RPC failed"));

    await executeGameCoreChange({ romId: 42, coreLabel: null, leaseOwner });

    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Failed to reset core" }));
  });

  it("logs the request and the answer under the caller's tag", async () => {
    vi.mocked(backend.setGameCore).mockResolvedValue({ success: true });
    vi.mocked(backend.clearGameCore).mockResolvedValue({ success: false, reason: "not_found", message: "" });

    await executeGameCoreChange({ romId: 42, coreLabel: "Snes9x", leaseOwner, logTag: "handleChangeGameCore" });
    await executeGameCoreChange({ romId: 42, coreLabel: null, leaseOwner, logTag: "handleResetGameCore" });

    expect(vi.mocked(backend.debugLog).mock.calls.map(([line]) => line)).toEqual([
      "handleChangeGameCore: romId=42 coreLabel=Snes9x",
      "handleChangeGameCore: result success=true",
      "handleResetGameCore: romId=42",
      "handleResetGameCore: result success=false",
    ]);
  });

  it("suppresses toast on prune lease cancellation", async () => {
    const { PruneLeaseAdmissionCancelled } = await import("./pruneLease");
    vi.mocked(backend.setGameCore).mockRejectedValue(new PruneLeaseAdmissionCancelled("unmounted"));

    await executeGameCoreChange({
      romId: 42,
      coreLabel: "Snes9x",
      leaseOwner,
    });
    expect(toaster.toast).not.toHaveBeenCalled();
    expect(backend.debugLog).toHaveBeenCalledWith(expect.stringContaining("continuation was cancelled"));
  });

  it("toasts generic failure on unexpected exception", async () => {
    vi.mocked(backend.setGameCore).mockRejectedValue(new Error("RPC failed"));

    await executeGameCoreChange({
      romId: 42,
      coreLabel: "Snes9x",
      leaseOwner,
    });
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: "Failed to set core" }));
  });
});
