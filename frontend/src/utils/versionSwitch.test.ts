import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  reportVersionListReachability,
  loadVersionList,
  fetchVersionCovers,
  executeVersionSwitch,
} from "./versionSwitch";
import * as backend from "../api/backend";
import type { VersionList, VersionInfo } from "../api/backend";
import * as connectionState from "./connectionState";
import * as vanishedBinding from "./vanishedBinding";
import * as versionApp from "./versionSwitchApplication";
import * as toast from "./toast";
import * as pruneLease from "./pruneLease";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  getVersionList: vi.fn(),
  switchVersion: vi.fn(),
  syncRomSaves: vi.fn(),
  refreshSaveStatus: vi.fn().mockResolvedValue(undefined),
  fetchCoverBase64: vi.fn(),
  logWarn: vi.fn(),
  logError: vi.fn(),
}));

vi.mock("./connectionState", () => ({
  reportServerReachable: vi.fn(),
}));

vi.mock("./vanishedBinding", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./vanishedBinding")>()),
  setBoundAvailability: vi.fn(),
}));

vi.mock("./versionSwitchApplication", () => ({
  applyCommittedVersionSwitch: vi.fn().mockResolvedValue({ confirmed: true, broadcast: true }),
}));

vi.mock("./toast", () => ({
  showToast: vi.fn(),
}));

const mockTarget: VersionInfo = {
  rom_id: 2,
  name: "Game (Europe)",
  label: "Europe",
  regions: ["Europe"],
  languages: ["En"],
  revision: "",
  tags: [],
  active: false,
  is_default: false,
  installed: true,
  synced: true,
  vanished: false,
  switchable: true,
};

describe("reportVersionListReachability", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reports server unreachable when server_query_failed is true", () => {
    reportVersionListReachability({
      multi_version: true,
      server_query_failed: true,
      bound_vanished: false,
      versions: [],
    });
    expect(connectionState.reportServerReachable).toHaveBeenCalledWith(false);
  });

  it("reports server reachable when multi-version and bound is not vanished", () => {
    reportVersionListReachability({
      multi_version: true,
      server_query_failed: false,
      bound_vanished: false,
      versions: [],
    });
    expect(connectionState.reportServerReachable).toHaveBeenCalledWith(true);
  });

  it("does not report reachable when bound is vanished", () => {
    reportVersionListReachability({
      multi_version: true,
      server_query_failed: false,
      bound_vanished: true,
      versions: [],
    });
    expect(connectionState.reportServerReachable).not.toHaveBeenCalled();
  });
});

describe("loadVersionList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches list, reports reachability, and publishes the bound version's availability", async () => {
    const mockList: VersionList = {
      multi_version: true,
      server_query_failed: false,
      bound_vanished: true,
      versions: [mockTarget],
    };
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(mockList);

    const result = await loadVersionList(100);
    expect(result).toBe(mockList);
    expect(backend.getVersionList).toHaveBeenCalledWith(100);
    // The bound version is gone and another version is not.
    expect(vanishedBinding.setBoundAvailability).toHaveBeenCalledWith(100, "version_gone");
  });
});

describe("fetchVersionCovers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches covers for unrequested versions and dedupes", async () => {
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "test-base64" });
    const requested = new Set<number>([1]);
    const onCover = vi.fn();

    const cleanup = fetchVersionCovers([mockTarget], requested, onCover);
    await Promise.resolve();
    await Promise.resolve();

    expect(requested.has(2)).toBe(true);
    expect(backend.fetchCoverBase64).toHaveBeenCalledWith(2);
    expect(onCover).toHaveBeenCalledWith(2, "test-base64");
    cleanup();
  });

  it("ignores response if cleanup function is called before resolve", async () => {
    let resolveCover!: (val: { base64: string }) => void;
    vi.mocked(backend.fetchCoverBase64).mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolveCover = res;
        }),
    );
    const requested = new Set<number>();
    const onCover = vi.fn();

    const cleanup = fetchVersionCovers([mockTarget], requested, onCover);
    cleanup();
    resolveCover({ base64: "test-base64" });
    await Promise.resolve();

    expect(onCover).not.toHaveBeenCalled();
  });
});

describe("executeVersionSwitch", () => {
  const leaseOwner = "version-picker:100";
  const askUnsyncedSaves = vi.fn();
  const setSwitching = vi.fn();
  const onCoverResolved = vi.fn();
  const onVanishedRefusal = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    pruneLease.mountPruneLeaseOwner(leaseOwner);
  });

  it("no-ops if target is active, vanished, or not switchable", async () => {
    for (const target of [
      { ...mockTarget, active: true },
      { ...mockTarget, vanished: true },
      { ...mockTarget, switchable: false },
    ]) {
      await executeVersionSwitch({ appId: 100, target, leaseOwner, askUnsyncedSaves, setSwitching });
    }
    expect(backend.switchVersion).not.toHaveBeenCalled();
    expect(setSwitching).not.toHaveBeenCalled();
  });

  it("no-ops for a version another shortcut of this game is on, installed or not", async () => {
    for (const target of [
      { ...mockTarget, bound_elsewhere: true },
      { ...mockTarget, bound_elsewhere: true, installed: false },
    ]) {
      await executeVersionSwitch({ appId: 100, target, leaseOwner, askUnsyncedSaves, setSwitching });
    }
    expect(backend.switchVersion).not.toHaveBeenCalled();
    expect(setSwitching).not.toHaveBeenCalled();
  });

  it("switches version successfully without soft-block", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: true,
      app_id: 100,
      rom_id: 2,
      target_installed: true,
      launch_options: "run",
    });

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
      onCoverResolved,
    });
    expect(setSwitching).toHaveBeenCalledWith(true);
    // setSwitching(false) must NOT be called on success
    expect(setSwitching).not.toHaveBeenCalledWith(false);
    expect(backend.switchVersion).toHaveBeenCalledWith(100, 2, false);
    expect(versionApp.applyCommittedVersionSwitch).toHaveBeenCalledWith(
      expect.objectContaining({ success: true }),
      onCoverResolved,
      expect.any(Object),
    );
  });

  const committed = () =>
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: true,
      app_id: 100,
      rom_id: 2,
      target_installed: true,
      launch_options: "run",
    });

  it("has the caller read the list again when a committed switch was stopped short of its broadcast", async () => {
    committed();
    vi.mocked(versionApp.applyCommittedVersionSwitch).mockResolvedValueOnce({ confirmed: true, broadcast: false });
    const setSwitching = vi.fn();
    const onUnbroadcastSwitch = vi.fn();

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
      onUnbroadcastSwitch,
    });

    expect(onUnbroadcastSwitch).toHaveBeenCalledTimes(1);
    expect(setSwitching.mock.calls).toEqual([[true]]);
  });

  it("asks nothing when the caller went before a committed switch could broadcast", async () => {
    committed();
    vi.mocked(versionApp.applyCommittedVersionSwitch).mockImplementationOnce(async () => {
      await pruneLease.releasePruneLeasesByOwner(leaseOwner);
      return { confirmed: true, broadcast: false };
    });
    const onUnbroadcastSwitch = vi.fn();

    await executeVersionSwitch({ appId: 100, target: mockTarget, leaseOwner, askUnsyncedSaves, onUnbroadcastSwitch });

    expect(onUnbroadcastSwitch).not.toHaveBeenCalled();
  });

  it("asks nothing when the switch broadcast", async () => {
    committed();
    const onUnbroadcastSwitch = vi.fn();

    await executeVersionSwitch({ appId: 100, target: mockTarget, leaseOwner, askUnsyncedSaves, onUnbroadcastSwitch });

    expect(onUnbroadcastSwitch).not.toHaveBeenCalled();
  });

  it("warns user if shortcut confirm fails on successful switch", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: true,
      app_id: 100,
      rom_id: 2,
      target_installed: true,
      launch_options: "run",
    });
    vi.mocked(versionApp.applyCommittedVersionSwitch).mockResolvedValueOnce({ confirmed: false, broadcast: true });

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
    });
    expect(toast.showToast).toHaveBeenCalledWith("Switched — re-switch if launch fails");
  });

  it("handles soft-block unsynced saves when user cancels", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: false,
      reason: "unsynced_saves",
      message: "Unsynced saves",
      stranding: "unsynced",
      unsynced_rom_id: 1,
      unsynced_version_name: "Game (USA)",
      server_reachable: true,
    });
    askUnsyncedSaves.mockResolvedValueOnce("cancel");

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
    });
    expect(connectionState.reportServerReachable).toHaveBeenCalledWith(true);
    expect(setSwitching).toHaveBeenCalledWith(false);
    expect(backend.switchVersion).toHaveBeenCalledTimes(1);
  });

  it("handles soft-block unsynced saves when user forces switch_anyway", async () => {
    vi.mocked(backend.switchVersion)
      .mockResolvedValueOnce({
        success: false,
        reason: "unsynced_saves",
        message: "Unsynced saves",
        stranding: "unsynced",
        unsynced_rom_id: 1,
        unsynced_version_name: "Game (USA)",
        server_reachable: true,
      })
      .mockResolvedValueOnce({
        success: true,
        app_id: 100,
        rom_id: 2,
        target_installed: true,
        launch_options: "run",
      });
    askUnsyncedSaves.mockResolvedValueOnce("switch_anyway");

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
    });
    expect(backend.switchVersion).toHaveBeenNthCalledWith(1, 100, 2, false);
    expect(backend.switchVersion).toHaveBeenNthCalledWith(2, 100, 2, true);
    expect(versionApp.applyCommittedVersionSwitch).toHaveBeenCalled();
  });

  it("handles soft-block sync_and_switch successfully", async () => {
    vi.mocked(backend.switchVersion)
      .mockResolvedValueOnce({
        success: false,
        reason: "unsynced_saves",
        message: "Unsynced saves",
        stranding: "unsynced",
        unsynced_rom_id: 1,
        unsynced_version_name: "Game (USA)",
        server_reachable: true,
      })
      .mockResolvedValueOnce({
        success: true,
        app_id: 100,
        rom_id: 2,
        target_installed: true,
        launch_options: "run",
      });
    askUnsyncedSaves.mockResolvedValueOnce("sync_and_switch");
    vi.mocked(backend.syncRomSaves).mockResolvedValueOnce({
      success: true,
      message: "ok",
      synced: 1,
      conflicts: [],
    });

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
    });
    expect(backend.syncRomSaves).toHaveBeenCalledWith(1);
    expect(backend.switchVersion).toHaveBeenNthCalledWith(2, 100, 2, false);
  });

  it("handles soft-block sync_and_switch when sync fails", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: false,
      reason: "unsynced_saves",
      message: "Unsynced saves",
      stranding: "unsynced",
      unsynced_rom_id: 1,
      unsynced_version_name: "Game (USA)",
      server_reachable: true,
    });
    askUnsyncedSaves.mockResolvedValueOnce("sync_and_switch");
    vi.mocked(backend.syncRomSaves).mockResolvedValueOnce({
      success: false,
      message: "failed",
      synced: 0,
    });

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
    });
    expect(toast.showToast).toHaveBeenCalledWith("Couldn't sync saves — try again");
    expect(backend.refreshSaveStatus).toHaveBeenCalledWith(1);
    expect(setSwitching).toHaveBeenCalledWith(false);
  });

  it("names the running game where the sync before the switch is refused because it runs", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: false,
      reason: "unsynced_saves",
      message: "Unsynced saves",
      stranding: "unsynced",
      unsynced_rom_id: 1,
      unsynced_version_name: "Game (USA)",
      server_reachable: true,
    });
    askUnsyncedSaves.mockResolvedValueOnce("sync_and_switch");
    const running = "Game (USA) is running. Quit it first.";
    vi.mocked(backend.syncRomSaves).mockResolvedValueOnce({
      success: false,
      reason: "game_running",
      message: running,
      synced: 0,
    });

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
    });
    expect(toast.showToast).toHaveBeenCalledWith(running);
    expect(backend.switchVersion).toHaveBeenCalledTimes(1);
    expect(setSwitching).toHaveBeenCalledWith(false);
  });

  it("handles soft-block sync_and_switch when conflicts exist", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: false,
      reason: "unsynced_saves",
      message: "Unsynced saves",
      stranding: "unsynced",
      unsynced_rom_id: 1,
      unsynced_version_name: "Game (USA)",
      server_reachable: true,
    });
    askUnsyncedSaves.mockResolvedValueOnce("sync_and_switch");
    vi.mocked(backend.syncRomSaves).mockResolvedValueOnce({
      success: true,
      message: "conflicts",
      synced: 0,
      conflicts: [{ filename: "save1.srm" } as never],
    });

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
    });
    expect(toast.showToast).toHaveBeenCalledWith("Resolve save conflicts first");
    expect(backend.refreshSaveStatus).toHaveBeenCalledWith(1);
    expect(setSwitching).toHaveBeenCalledWith(false);
  });

  it("handles generic switch failure and reports unreachable if server_unreachable", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: false,
      reason: "server_unreachable",
      message: "Host down",
    });

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
    });
    expect(connectionState.reportServerReachable).toHaveBeenCalledWith(false);
    expect(toast.showToast).toHaveBeenCalledWith("Could not switch version", { subtext: "Host down" });
    expect(setSwitching).toHaveBeenCalledWith(false);
  });

  it("invokes onVanishedRefusal when target version vanished", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: false,
      reason: "version_vanished",
      message: "ROM 404",
    });

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      onVanishedRefusal,
    });
    expect(onVanishedRefusal).toHaveBeenCalled();
  });

  it("handles teardown cancellation silently", async () => {
    const error = new pruneLease.PruneLeaseAdmissionCancelled("unmounted");
    vi.mocked(backend.switchVersion).mockRejectedValueOnce(error);

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      logTag: "TestSwitch",
    });
    expect(backend.logWarn).toHaveBeenCalledWith(
      expect.stringContaining("TestSwitch: version switch continuation was cancelled"),
    );
    expect(toast.showToast).not.toHaveBeenCalled();
  });

  it("handles unexpected error by toasting and clearing switching", async () => {
    vi.mocked(backend.switchVersion).mockRejectedValueOnce(new Error("Crash"));

    await executeVersionSwitch({
      appId: 100,
      target: mockTarget,
      leaseOwner,
      askUnsyncedSaves,
      setSwitching,
      logTag: "TestSwitch",
    });
    expect(backend.logError).toHaveBeenCalledWith(expect.stringContaining("TestSwitch: switchVersion failed"));
    expect(toast.showToast).toHaveBeenCalledWith("Could not switch version");
    expect(setSwitching).toHaveBeenCalledWith(false);
  });
});

describe("executeVersionSwitch — what the unsynced-saves prompt is told", () => {
  const leaseOwner = "version-picker:100";
  const askUnsyncedSaves = vi.fn();
  const block = (stranding: "unsynced" | "unchecked") => ({
    success: false as const,
    reason: "unsynced_saves" as const,
    message: "Unsynced saves",
    stranding,
    unsynced_rom_id: 1,
    unsynced_version_name: "Game (USA)",
    server_reachable: true,
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    pruneLease.mountPruneLeaseOwner(leaseOwner);
    (await vi.importActual<typeof import("./vanishedBinding")>("./vanishedBinding")).resetBoundAvailability();
    askUnsyncedSaves.mockResolvedValue("cancel");
  });

  it("tells it the version being left is gone where RomM answered a 404 for it", async () => {
    const actual = await vi.importActual<typeof import("./vanishedBinding")>("./vanishedBinding");
    actual.setBoundAvailability(100, "version_gone");
    vi.mocked(backend.switchVersion).mockResolvedValueOnce(block("unsynced"));

    await executeVersionSwitch({ appId: 100, target: mockTarget, leaseOwner, askUnsyncedSaves });

    expect(askUnsyncedSaves).toHaveBeenCalledWith({
      versionName: "Game (USA)",
      stranding: "unsynced",
      serverReachable: true,
      versionGone: true,
    });
  });

  it("keeps the version live where nothing proved it gone", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce(block("unsynced"));

    await executeVersionSwitch({ appId: 100, target: mockTarget, leaseOwner, askUnsyncedSaves });

    expect(askUnsyncedSaves).toHaveBeenCalledWith(expect.objectContaining({ versionGone: false }));
  });

  it("passes on that the check could not read", async () => {
    vi.mocked(backend.switchVersion).mockResolvedValueOnce(block("unchecked"));

    await executeVersionSwitch({ appId: 100, target: mockTarget, leaseOwner, askUnsyncedSaves });

    expect(askUnsyncedSaves).toHaveBeenCalledWith(expect.objectContaining({ stranding: "unchecked" }));
  });
});
