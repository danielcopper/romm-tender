/**
 * Exercises the sync-start stale-shortcut reconcile (#1046): the frontend reads
 * Steam's live RomM-shortcut appIds and asks the backend to unbind any dead
 * binding before the work queue is built, so the next sync recreates a shortcut
 * the user deleted via Steam's own UI.
 *
 * Lives in its own file (separate module instance) so the reconcile path never
 * shares syncManager's module-level per-unit state (_isUnitRunning / _scanCache)
 * with the event-handler tests in syncManager.test.ts.
 *
 * steamShortcuts is mocked so the ownership scan is controllable; the backend
 * reconcileShortcuts callable uses the global test-setup mock.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";

const scanShortcutOwnership = vi.fn();
vi.mock("./steamShortcuts", () => ({
  scanShortcutOwnership: (...args: unknown[]) => scanShortcutOwnership(...args),
  getLiveRomMShortcutAppIds: vi.fn(),
  // The other steamShortcuts exports are unused by reconcileStaleShortcuts but
  // must exist so syncManager's imports resolve.
  setLaunchOptionsConfirmed: vi.fn(),
  addShortcut: vi.fn(),
  getExistingRomMShortcuts: vi.fn(),
}));

import { reconcileStaleShortcuts } from "./syncManager";

describe("reconcileStaleShortcuts (#1046)", () => {
  beforeEach(() => {
    scanShortcutOwnership.mockReset();
    vi.mocked(backend.reconcileShortcuts).mockReset();
    vi.mocked(backend.reconcileShortcuts).mockResolvedValue({ success: true, message: "", unbound_count: 0 });
  });

  it("passes the live appId set to the backend reconcile callable", async () => {
    scanShortcutOwnership.mockResolvedValue({ owned: [100, 200], unresolved: [] });

    await reconcileStaleShortcuts();

    expect(vi.mocked(backend.reconcileShortcuts)).toHaveBeenCalledWith([100, 200]);
  });

  it("keeps a shortcut Steam did not answer for in the live set, so it is not reported stale (#1979)", async () => {
    // 300's details timed out: nothing was established about it, so it must not
    // be read as a shortcut the user deleted. The backend unbinds exactly the
    // bindings absent from the set it is sent.
    scanShortcutOwnership.mockResolvedValue({ owned: [100], unresolved: [300] });

    await reconcileStaleShortcuts();

    expect(vi.mocked(backend.reconcileShortcuts)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.reconcileShortcuts)).toHaveBeenCalledWith([100, 300]);
  });

  it("passes an empty live set through (scan ran, found none)", async () => {
    scanShortcutOwnership.mockResolvedValue({ owned: [], unresolved: [] });

    await reconcileStaleShortcuts();

    // [] is a real "no RomM shortcuts in Steam" signal the backend acts on.
    expect(vi.mocked(backend.reconcileShortcuts)).toHaveBeenCalledWith([]);
  });

  it("does NOT reconcile when the live scan returns null (store unreadable)", async () => {
    scanShortcutOwnership.mockResolvedValue(null);

    await reconcileStaleShortcuts();

    // null = could-not-scan: reconciling against it would unbind every binding.
    expect(vi.mocked(backend.reconcileShortcuts)).not.toHaveBeenCalled();
  });

  it("swallows a scan rejection without calling reconcile", async () => {
    scanShortcutOwnership.mockRejectedValue(new Error("scan boom"));
    const logErrorSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
    try {
      await expect(reconcileStaleShortcuts()).resolves.toBeUndefined();

      expect(vi.mocked(backend.reconcileShortcuts)).not.toHaveBeenCalled();
      // Non-vacuous: the catch surfaces the failure rather than throwing.
      expect(logErrorSpy).toHaveBeenCalledWith(expect.stringContaining("scan boom"));
    } finally {
      logErrorSpy.mockRestore();
    }
  });

  it("swallows a backend reconcile rejection (best-effort, never blocks sync)", async () => {
    scanShortcutOwnership.mockResolvedValue({ owned: [100], unresolved: [] });
    vi.mocked(backend.reconcileShortcuts).mockRejectedValue(new Error("reconcile boom"));
    const logErrorSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
    try {
      await expect(reconcileStaleShortcuts()).resolves.toBeUndefined();

      // Non-vacuous: the catch surfaces the failure rather than throwing.
      expect(logErrorSpy).toHaveBeenCalledWith(expect.stringContaining("reconcile boom"));
    } finally {
      logErrorSpy.mockRestore();
    }
  });

  it("logs the unbound count when the backend reports stale bindings cleared", async () => {
    scanShortcutOwnership.mockResolvedValue({ owned: [100], unresolved: [] });
    vi.mocked(backend.reconcileShortcuts).mockResolvedValue({ success: true, message: "", unbound_count: 3 });
    const logInfoSpy = vi.spyOn(backend, "logInfo").mockImplementation(() => {});
    try {
      await reconcileStaleShortcuts();

      // Non-vacuous: the success branch surfaces how many bindings were cleared.
      expect(logInfoSpy).toHaveBeenCalledWith(expect.stringContaining("3"));
    } finally {
      logInfoSpy.mockRestore();
    }
  });
});
