import { describe, it, expect, vi, beforeEach } from "vitest";

const shortcutsGoneFromSteam = vi.fn();
vi.mock("./steamShortcuts", () => ({
  shortcutsGoneFromSteam: (...args: unknown[]) => shortcutsGoneFromSteam(...args),
}));

const reportSyncRemovals = vi.fn();
const logError = vi.fn();
vi.mock("../api/backend", () => ({
  reportSyncRemovals: (...args: unknown[]) => reportSyncRemovals(...args),
  logError: (...args: unknown[]) => logError(...args),
}));

const refreshSyncStatsAfterChange = vi.fn().mockResolvedValue(undefined);
vi.mock("./syncStatsStore", () => ({
  refreshSyncStatsAfterChange: () => refreshSyncStatsAfterChange(),
}));

import { reportSyncRemovalsGone } from "./syncRemovalReport";

const STALE = { rom_id: 99, app_id: 9900 };
const CONVERGED = { rom_id: 31, app_id: 6031, kept_rom_id: 30, kept_app_id: 6030 };

describe("reportSyncRemovalsGone (#2653)", () => {
  beforeEach(() => {
    shortcutsGoneFromSteam.mockReset();
    reportSyncRemovals.mockReset().mockResolvedValue({ success: true, message: "", unbound_count: 2 });
    logError.mockClear();
    refreshSyncStatsAfterChange.mockClear();
  });

  it("asks Steam about every removal, waiting for the ones removed, and reports the gone ones stale and converged apart", async () => {
    shortcutsGoneFromSteam.mockResolvedValue([9900, 6031]);

    await reportSyncRemovalsGone([STALE, CONVERGED], [9900]);

    expect(shortcutsGoneFromSteam).toHaveBeenCalledWith([9900, 6031], [9900]);
    expect(reportSyncRemovals).toHaveBeenCalledWith([{ rom_id: 99, app_id: 9900 }], [{ rom_id: 31, app_id: 6031 }]);
  });

  it("leaves out a removal Steam still lists", async () => {
    shortcutsGoneFromSteam.mockResolvedValue([6031]);

    await reportSyncRemovalsGone([STALE, CONVERGED], [9900, 6031]);

    expect(reportSyncRemovals).toHaveBeenCalledWith([], [{ rom_id: 31, app_id: 6031 }]);
  });

  it("reports nothing when nothing is gone", async () => {
    shortcutsGoneFromSteam.mockResolvedValue([]);

    await reportSyncRemovalsGone([STALE, CONVERGED], [9900, 6031]);

    expect(reportSyncRemovals).not.toHaveBeenCalled();
    expect(refreshSyncStatsAfterChange).not.toHaveBeenCalled();
  });

  it("reads the game count again once a report unbound a row, and not when it unbound none", async () => {
    shortcutsGoneFromSteam.mockResolvedValue([9900]);

    await reportSyncRemovalsGone([STALE], [9900]);
    reportSyncRemovals.mockResolvedValueOnce({ success: true, message: "", unbound_count: 0 });
    await reportSyncRemovalsGone([STALE], [9900]);

    expect(refreshSyncStatsAfterChange).toHaveBeenCalledTimes(1);
  });

  it("logs a report the backend refused and reads nothing again", async () => {
    shortcutsGoneFromSteam.mockResolvedValue([9900]);
    reportSyncRemovals.mockResolvedValue({ success: false, reason: "prune_active", message: "A cleanup is running" });

    await reportSyncRemovalsGone([STALE], [9900]);

    expect(logError).toHaveBeenCalledWith(expect.stringContaining("A cleanup is running"));
    expect(refreshSyncStatsAfterChange).not.toHaveBeenCalled();
  });
});
