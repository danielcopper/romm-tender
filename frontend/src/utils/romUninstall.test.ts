import { describe, it, expect, vi, beforeEach } from "vitest";
import { executeRomUninstall } from "./romUninstall";
import * as backend from "../api/backend";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";
import * as toast from "./toast";
import * as pruneLease from "./pruneLease";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  removeRom: vi.fn(),
  debugLog: vi.fn(),
}));

vi.mock("./steamShortcuts", () => ({
  setLaunchOptionsConfirmed: vi.fn().mockResolvedValue(true),
}));

vi.mock("./toast", () => ({
  showToast: vi.fn(),
}));

describe("executeRomUninstall", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pruneLease.mountPruneLeaseOwner("test-owner");
  });

  it("handles a successful uninstall end-to-end", async () => {
    vi.mocked(backend.removeRom).mockResolvedValueOnce({
      success: true,
      message: "",
      prune_lease_token: "test-token",
    });

    const dispatchEventSpy = vi.spyOn(globalThis, "dispatchEvent");

    await executeRomUninstall({
      romId: 42,
      appId: 100,
      romName: "Super Mario 64",
      leaseOwner: "test-owner",
      tag: "testTag",
      context: "testContext",
    });
    expect(backend.removeRom).toHaveBeenCalledWith(42);
    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(100, "");
    expect(dispatchEventSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "romm_rom_uninstalled",
        detail: { rom_id: 42 },
      }),
    );
    expect(toast.showToast).toHaveBeenCalledWith("Super Mario 64 uninstalled");
  });

  it("uses default 'ROM' in toast when romName is not provided", async () => {
    vi.mocked(backend.removeRom).mockResolvedValueOnce({
      success: true,
      message: "",
      prune_lease_token: "test-token",
    });

    await executeRomUninstall({
      romId: 42,
      appId: 100,
      leaseOwner: "test-owner",
    });
    expect(toast.showToast).toHaveBeenCalledWith("ROM uninstalled");
  });

  it("handles backend refusal with error message toast", async () => {
    vi.mocked(backend.removeRom).mockResolvedValueOnce({
      success: false,
      message: "Server disk is read-only",
    });

    await executeRomUninstall({
      romId: 42,
      appId: 100,
      romName: "Super Mario 64",
      leaseOwner: "test-owner",
    });
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(toast.showToast).toHaveBeenCalledWith("Server disk is read-only");
  });

  it("handles teardown cancellation by logging to debugLog and suppressing toast", async () => {
    const error = new pruneLease.PruneLeaseAdmissionCancelled("unmounted");
    vi.mocked(backend.removeRom).mockRejectedValueOnce(error);

    await executeRomUninstall({
      romId: 42,
      appId: 100,
      romName: "Super Mario 64",
      leaseOwner: "test-owner",
      tag: "handleUninstall",
    });
    expect(backend.debugLog).toHaveBeenCalledWith(
      expect.stringContaining("handleUninstall: continuation was cancelled"),
    );
    expect(toast.showToast).not.toHaveBeenCalled();
  });

  it("handles unexpected exceptions by displaying a toast", async () => {
    vi.mocked(backend.removeRom).mockRejectedValueOnce(new Error("Network failure"));

    await executeRomUninstall({
      romId: 42,
      appId: 100,
      romName: "Super Mario 64",
      leaseOwner: "test-owner",
    });
    expect(toast.showToast).toHaveBeenCalledWith("Uninstall failed");
  });

  it("skips launch options reset and event dispatch if continuation signal is aborted", async () => {
    vi.mocked(backend.removeRom).mockResolvedValueOnce({
      success: true,
      message: "",
      prune_lease_token: "test-token",
    });

    const dispatchEventSpy = vi.spyOn(globalThis, "dispatchEvent");

    // Spy on withPruneLease and pass an aborted signal
    const withPruneLeaseSpy = vi
      .spyOn(pruneLease, "withPruneLease")
      .mockImplementationOnce(async (_token, _ctx, op) => {
        const controller = new AbortController();
        controller.abort();
        return op(controller.signal);
      });

    await executeRomUninstall({
      romId: 42,
      appId: 100,
      leaseOwner: "test-owner",
    });
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(dispatchEventSpy).not.toHaveBeenCalled();
    withPruneLeaseSpy.mockRestore();
  });
});
