import { describe, it, expect, vi, beforeEach } from "vitest";
import { activateRunningApp, executeStopRunningGame } from "./runningGame";
import * as backend from "../api/backend";
import * as toast from "./toast";
import * as sessionManager from "./sessionManager";
import { Navigation } from "@decky/ui";

vi.mock("@decky/ui", () => ({
  Navigation: {
    Navigate: vi.fn(),
  },
}));

vi.mock("../api/backend", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/backend")>();
  return {
    ...actual,
    stopRunningGame: vi.fn(),
    debugLog: vi.fn(),
  };
});

vi.mock("./toast", () => ({
  showToast: vi.fn(),
}));

vi.mock("./sessionManager", () => ({
  readGameRunning: vi.fn(),
}));

function setRunning(running: boolean): void {
  vi.mocked(sessionManager.readGameRunning).mockReturnValue({
    running,
    decidedBy: running ? "store" : "none",
    diagnostics: "",
  });
}

describe("runningGame", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("SteamUIStore", undefined);
  });

  describe("activateRunningApp", () => {
    it("uses SteamUIStore.SetRunningApp and NavigateToRunningApp when available", () => {
      const setRunningApp = vi.fn();
      const navigateToRunningApp = vi.fn();
      vi.stubGlobal("SteamUIStore", {
        SetRunningApp: setRunningApp,
        NavigateToRunningApp: navigateToRunningApp,
      });

      activateRunningApp(42, "testTag");

      expect(setRunningApp).toHaveBeenCalledWith(42);
      expect(navigateToRunningApp).toHaveBeenCalledTimes(1);
      expect(Navigation.Navigate).not.toHaveBeenCalled();
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("testTag: resumed appId=42 via SteamUIStore.NavigateToRunningApp"),
      );
    });

    it("falls back to Navigation.Navigate('/apprunning') when NavigateToRunningApp is missing", () => {
      const setRunningApp = vi.fn();
      vi.stubGlobal("SteamUIStore", {
        SetRunningApp: setRunningApp,
      });

      activateRunningApp(42, "testTag");

      expect(setRunningApp).toHaveBeenCalledWith(42);
      expect(Navigation.Navigate).toHaveBeenCalledWith("/apprunning");
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("testTag: resumed appId=42 via Navigation.Navigate"),
      );
    });

    it("falls back to Navigation.Navigate('/apprunning') when SteamUIStore.SetRunningApp throws", () => {
      const setRunningApp = vi.fn(() => {
        throw new Error("Store crashed");
      });
      vi.stubGlobal("SteamUIStore", {
        SetRunningApp: setRunningApp,
      });

      activateRunningApp(42, "testTag");

      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("testTag: resume — SteamUIStore threw, falling back to Navigate"),
      );
      expect(Navigation.Navigate).toHaveBeenCalledWith("/apprunning");
    });

    it("falls back to Navigation.Navigate('/apprunning') when SteamUIStore is absent", () => {
      activateRunningApp(42, "testTag");

      expect(Navigation.Navigate).toHaveBeenCalledWith("/apprunning");
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("testTag: resumed appId=42 via Navigation.Navigate"),
      );
    });

    it("lets a throwing Navigation.Navigate reach its caller", () => {
      vi.mocked(Navigation.Navigate).mockImplementationOnce(() => {
        throw new Error("Nav failure");
      });

      expect(() => activateRunningApp(42, "testTag")).toThrow("Nav failure");
    });
  });

  describe("executeStopRunningGame", () => {
    const confirmed = () => Promise.resolve(true);

    it("ignores call when a stop is already in flight", async () => {
      const stopInFlightRef = { current: true };
      const onClearOverlay = vi.fn();

      await executeStopRunningGame({
        appId: 10,
        romId: 100,
        tag: "testTag",
        stopInFlightRef,
        onClearOverlay,
        confirmModal: confirmed,
      });
      expect(backend.stopRunningGame).not.toHaveBeenCalled();
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("testTag: Stop ignored for appId=10 — a stop is already in flight"),
      );
      expect(stopInFlightRef.current).toBe(true);
    });

    it("self-heals stale overlay when nothing reads as running", async () => {
      const stopInFlightRef = { current: false };
      const onClearOverlay = vi.fn();
      setRunning(false);

      await executeStopRunningGame({
        appId: 10,
        romId: 100,
        tag: "testTag",
        stopInFlightRef,
        onClearOverlay,
        confirmModal: confirmed,
      });
      expect(onClearOverlay).toHaveBeenCalledTimes(1);
      expect(backend.stopRunningGame).not.toHaveBeenCalled();
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("testTag: Stop on appId=10 but nothing is running — clearing stale overlay"),
      );
      expect(stopInFlightRef.current).toBe(false);
    });

    it("warns and aborts if romId is null", async () => {
      const stopInFlightRef = { current: false };
      const onClearOverlay = vi.fn();
      setRunning(true);

      await executeStopRunningGame({
        appId: 10,
        romId: null,
        tag: "testTag",
        stopInFlightRef,
        onClearOverlay,
        confirmModal: confirmed,
      });
      expect(toast.showToast).toHaveBeenCalledWith("Couldn't stop the game — still loading its details");
      expect(backend.stopRunningGame).not.toHaveBeenCalled();
      expect(stopInFlightRef.current).toBe(false);
    });

    it("aborts when confirmModal returns false", async () => {
      const stopInFlightRef = { current: false };
      const onClearOverlay = vi.fn();
      const confirmModal = vi.fn().mockResolvedValue(false);
      setRunning(true);

      await executeStopRunningGame({
        appId: 10,
        romId: 100,
        tag: "testTag",
        stopInFlightRef,
        onClearOverlay,
        confirmModal,
      });
      expect(confirmModal).toHaveBeenCalledTimes(1);
      expect(backend.stopRunningGame).not.toHaveBeenCalled();
      expect(backend.debugLog).toHaveBeenCalledWith(expect.stringContaining("testTag: Stop cancelled for appId=10"));
      expect(stopInFlightRef.current).toBe(false);
    });

    it("successfully stops game and clears overlay", async () => {
      const stopInFlightRef = { current: false };
      const onClearOverlay = vi.fn();
      const onSetPending = vi.fn();
      setRunning(true);
      vi.mocked(backend.stopRunningGame).mockResolvedValueOnce({
        success: true,
        stopped: 1,
        force_killed: 0,
      });

      await executeStopRunningGame({
        appId: 10,
        romId: 100,
        tag: "testTag",
        stopInFlightRef,
        onClearOverlay,
        onSetPending,
        confirmModal: confirmed,
      });
      expect(backend.stopRunningGame).toHaveBeenCalledWith(100);
      expect(onSetPending).toHaveBeenCalledWith(true);
      expect(onSetPending).toHaveBeenCalledWith(false);
      expect(onClearOverlay).toHaveBeenCalledTimes(1);
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("testTag: stop_running_game for appId=10 — success=true"),
      );
      expect(stopInFlightRef.current).toBe(false);
    });

    it("treats reason=not_running as success and clears overlay", async () => {
      const stopInFlightRef = { current: false };
      const onClearOverlay = vi.fn();
      setRunning(true);
      vi.mocked(backend.stopRunningGame).mockResolvedValueOnce({
        success: false,
        reason: "not_running",
      });

      await executeStopRunningGame({
        appId: 10,
        romId: 100,
        tag: "testTag",
        stopInFlightRef,
        onClearOverlay,
        confirmModal: confirmed,
      });
      expect(onClearOverlay).toHaveBeenCalledTimes(1);
      expect(stopInFlightRef.current).toBe(false);
    });

    it("handles backend refusal, toasts message, and preserves overlay", async () => {
      const stopInFlightRef = { current: false };
      const onClearOverlay = vi.fn();
      setRunning(true);
      vi.mocked(backend.stopRunningGame).mockResolvedValueOnce({
        success: false,
        reason: "game_not_running",
        message: "No matching process found",
      });

      await executeStopRunningGame({
        appId: 10,
        romId: 100,
        tag: "testTag",
        stopInFlightRef,
        onClearOverlay,
        confirmModal: confirmed,
      });
      expect(onClearOverlay).not.toHaveBeenCalled();
      expect(toast.showToast).toHaveBeenCalledWith("No matching process found");
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("testTag: stop_running_game refused for appId=10 — reason=game_not_running"),
      );
      expect(stopInFlightRef.current).toBe(false);
    });

    it("catches errors, toasts generic failure, and preserves overlay", async () => {
      const stopInFlightRef = { current: false };
      const onClearOverlay = vi.fn();
      setRunning(true);
      vi.mocked(backend.stopRunningGame).mockRejectedValueOnce(new Error("Bridge died"));

      await executeStopRunningGame({
        appId: 10,
        romId: 100,
        tag: "testTag",
        stopInFlightRef,
        onClearOverlay,
        confirmModal: confirmed,
      });
      expect(onClearOverlay).not.toHaveBeenCalled();
      expect(toast.showToast).toHaveBeenCalledWith("Couldn't stop the game");
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("testTag: stop_running_game threw for appId=10: Error: Bridge died"),
      );
      expect(stopInFlightRef.current).toBe(false);
    });
  });
});
