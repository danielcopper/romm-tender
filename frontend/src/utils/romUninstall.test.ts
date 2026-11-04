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

/** Record the rom ids every "romm_rom_uninstalled" announces until the returned stop is called. */
function listenForUninstalls(): { romIds: number[]; stop: () => void } {
  const romIds: number[] = [];
  const listener = (event: WindowEventMap["romm_rom_uninstalled"]) => romIds.push(event.detail.rom_id);
  globalThis.addEventListener("romm_rom_uninstalled", listener);
  return { romIds, stop: () => globalThis.removeEventListener("romm_rom_uninstalled", listener) };
}

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

    const uninstalls = listenForUninstalls();

    const removed = await executeRomUninstall({
      romId: 42,
      romName: "Super Mario 64",
      leaseOwner: "test-owner",
      tag: "testTag",
    });

    expect(removed).toBe(true);
    expect(backend.removeRom).toHaveBeenCalledWith(42);
    // The kept shortcut carries its appId, installed or not: nothing to reset.
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(uninstalls.romIds).toEqual([42]);
    expect(toast.showToast).toHaveBeenCalledWith("Super Mario 64 uninstalled");
    uninstalls.stop();
  });

  it("uses default 'ROM' in toast when romName is not provided", async () => {
    vi.mocked(backend.removeRom).mockResolvedValueOnce({
      success: true,
      message: "",
      prune_lease_token: "test-token",
    });

    const removed = await executeRomUninstall({
      romId: 42,
      leaseOwner: "test-owner",
    });

    expect(removed).toBe(true);
    expect(toast.showToast).toHaveBeenCalledWith("ROM uninstalled");
  });

  it("handles backend refusal with error message toast", async () => {
    vi.mocked(backend.removeRom).mockResolvedValueOnce({
      success: false,
      message: "Server disk is read-only",
    });
    const uninstalls = listenForUninstalls();

    const removed = await executeRomUninstall({
      romId: 42,
      romName: "Super Mario 64",
      leaseOwner: "test-owner",
    });

    expect(removed).toBe(false);
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(uninstalls.romIds).toEqual([]);
    expect(toast.showToast).toHaveBeenCalledWith("Server disk is read-only");
    uninstalls.stop();
  });

  it("logs a cancelled continuation and says nothing failed when the page is left before the removal's lease is taken", async () => {
    vi.mocked(backend.removeRom).mockImplementationOnce(async () => {
      await pruneLease.releasePruneLeasesByOwner("test-owner");
      return { success: true, message: "", prune_lease_token: "test-token" };
    });

    const removed = await executeRomUninstall({
      romId: 42,
      romName: "Super Mario 64",
      leaseOwner: "test-owner",
      tag: "handleUninstall",
    });

    expect(removed).toBe(false);
    expect(backend.debugLog).toHaveBeenCalledWith(
      expect.stringContaining("handleUninstall: continuation was cancelled"),
    );
    expect(toast.showToast).not.toHaveBeenCalled();
  });

  it("says the uninstall failed when the removal itself fails after the page was left", async () => {
    vi.mocked(backend.removeRom).mockImplementationOnce(async () => {
      await pruneLease.releasePruneLeasesByOwner("test-owner");
      throw new Error("connection_lost");
    });
    const uninstalls = listenForUninstalls();

    const removed = await executeRomUninstall({
      romId: 42,
      romName: "Super Mario 64",
      leaseOwner: "test-owner",
    });

    expect(removed).toBe(false);
    expect(toast.showToast).toHaveBeenCalledWith("Uninstall failed");
    expect(backend.debugLog).not.toHaveBeenCalled();
    expect(uninstalls.romIds).toEqual([]);
    uninstalls.stop();
  });

  it("handles unexpected exceptions by displaying a toast", async () => {
    vi.mocked(backend.removeRom).mockRejectedValueOnce(new Error("Network failure"));

    const removed = await executeRomUninstall({
      romId: 42,
      romName: "Super Mario 64",
      leaseOwner: "test-owner",
    });

    expect(removed).toBe(false);
    expect(toast.showToast).toHaveBeenCalledWith("Uninstall failed");
  });
});
