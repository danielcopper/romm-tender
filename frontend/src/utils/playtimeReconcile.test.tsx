import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useGamePlaytime } from "./playtimeReconcile";
import * as backend from "../api/backend";
import * as metadataPatches from "./metadataPatches";
import { stubAppStore } from "../test-utils/steamStubs";

vi.mock("../api/backend", () => ({
  reconcilePlaytime: vi.fn(),
  debugLog: vi.fn(),
}));

vi.mock("./metadataPatches", () => ({
  updatePlaytimeDisplay: vi.fn(),
}));

const overview = (fields: Omit<SteamAppOverview, "display_name" | "strDisplayName">): SteamAppOverview => ({
  display_name: "",
  strDisplayName: "",
  ...fields,
});

describe("useGamePlaytime", () => {
  const mockAppId = 12345;
  const mockRomId = 999;

  beforeEach(() => {
    vi.clearAllMocks();
    stubAppStore({});
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(
      overview({
        appid: mockAppId,
        rt_last_time_played: 0,
        minutes_playtime_forever: 0,
      }),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("initializes with overview values and does not call reconcilePlaytime if romId is null", () => {
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(
      overview({
        appid: mockAppId,
        rt_last_time_played: 1600000000,
        minutes_playtime_forever: 125,
      }),
    );

    const { result } = renderHook(() => useGamePlaytime(mockAppId, null));

    expect(result.current.playtime).toBe("2h 5m");
    expect(result.current.lastPlayed).not.toBe("Never");
    expect(backend.reconcilePlaytime).not.toHaveBeenCalled();
  });

  it("reconciles playtime when romId is provided and shows the restored last-played", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-21T12:00:00Z"));
    vi.mocked(backend.reconcilePlaytime).mockResolvedValue({
      total_seconds: 7200,
      session_count: 3,
      last_played: "2026-09-20T12:00:00Z",
      server_query_failed: false,
    });

    const { result } = renderHook(() => useGamePlaytime(mockAppId, mockRomId, "TestContext"));

    await waitFor(() => {
      expect(backend.reconcilePlaytime).toHaveBeenCalledWith(mockRomId);
      expect(metadataPatches.updatePlaytimeDisplay).toHaveBeenCalledWith(mockAppId, 7200, false);
      expect(result.current.lastPlayed).toBe("Yesterday");
    });
  });

  it("handles deferred reconcile result with debugLog", async () => {
    vi.mocked(backend.reconcilePlaytime).mockResolvedValue({
      success: false,
      reason: "prune_active",
      message: "Prune lease held",
    });

    renderHook(() => useGamePlaytime(mockAppId, mockRomId, "DeferredContext"));

    await waitFor(() => {
      expect(backend.reconcilePlaytime).toHaveBeenCalledWith(mockRomId);
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("DeferredContext: playtime reconcile deferred: Prune lease held"),
      );
      expect(metadataPatches.updatePlaytimeDisplay).not.toHaveBeenCalled();
    });
  });

  it("re-injects local total when server_query_failed is true and keeps Steam's last-played", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-11T10:00:00Z"));
    vi.mocked(backend.reconcilePlaytime).mockResolvedValue({
      total_seconds: 1800,
      session_count: 1,
      last_played: "2026-09-10T10:00:00Z",
      server_query_failed: true,
    });

    const { result } = renderHook(() => useGamePlaytime(mockAppId, mockRomId));

    await waitFor(() => {
      expect(backend.reconcilePlaytime).toHaveBeenCalledWith(mockRomId);
      expect(metadataPatches.updatePlaytimeDisplay).toHaveBeenCalledWith(mockAppId, 1800, false);
      expect(result.current.lastPlayed).toBe("Never");
    });
  });

  it("handles reconcile error with debugLog without crashing", async () => {
    vi.mocked(backend.reconcilePlaytime).mockRejectedValue(new Error("network failure"));

    renderHook(() => useGamePlaytime(mockAppId, mockRomId, "ErrorContext"));

    await waitFor(() => {
      expect(backend.reconcilePlaytime).toHaveBeenCalledWith(mockRomId);
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("ErrorContext: playtime reconcile error: Error: network failure"),
      );
      expect(metadataPatches.updatePlaytimeDisplay).not.toHaveBeenCalled();
    });
  });

  it("updates reactively when romm_playtime_changed is dispatched for the matching appId", async () => {
    vi.mocked(backend.reconcilePlaytime).mockResolvedValue({
      total_seconds: 0,
      session_count: 0,
      last_played: null,
      server_query_failed: false,
    });

    const mockOverview = overview({
      appid: mockAppId,
      rt_last_time_played: 0,
      minutes_playtime_forever: 0,
    });
    vi.mocked(appStore.GetAppOverviewByAppID).mockImplementation(() => mockOverview);

    const { result } = renderHook(() => useGamePlaytime(mockAppId, mockRomId));

    expect(result.current.playtime).toBe("None");

    // Dispatch event for a different app ID - should be ignored
    act(() => {
      globalThis.dispatchEvent(new CustomEvent("romm_playtime_changed", { detail: { appId: 99999 } }));
    });
    expect(result.current.playtime).toBe("None");

    // Update overview mock and dispatch for matching appId
    mockOverview.minutes_playtime_forever = 90;
    mockOverview.rt_last_time_played = 1700000000;

    act(() => {
      globalThis.dispatchEvent(new CustomEvent("romm_playtime_changed", { detail: { appId: mockAppId } }));
    });

    await waitFor(() => {
      expect(result.current.playtime).toBe("1h 30m");
    });
  });
});
