import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { expectStableSubscribe } from "../test-utils/store-hook-subscription";
import {
  getRommConnectionState,
  setRommConnectionState,
  reportServerReachable,
  onRommConnectionChange,
  getServerRetryProgress,
  setServerRetryProgress,
  onServerRetryProgressChange,
  beginServerLoad,
  settleServerLoad,
  setVersionError,
  onVersionErrorChange,
  useVersionError,
} from "./connectionState";
import { debugLog } from "../api/backend";

// Fakes nothing — the real useSyncExternalStore runs; the wrapper only records
// what useVersionError passes it. See expectStableSubscribe's docstring for why
// the mock has to live in this file.
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return { ...actual, useSyncExternalStore: vi.fn(actual.useSyncExternalStore) };
});

describe("connectionState store (#1345)", () => {
  beforeEach(() => {
    // Reset the module-level store to the neutral state between tests.
    setRommConnectionState("checking");
    vi.mocked(debugLog).mockClear();
  });

  it("notifies subscribers on a real change", () => {
    const cb = vi.fn();
    onRommConnectionChange(cb);
    setRommConnectionState("offline");
    expect(cb).toHaveBeenCalledExactlyOnceWith("offline");
    expect(getRommConnectionState()).toBe("offline");
  });

  it("does NOT notify when the state is unchanged", () => {
    setRommConnectionState("connected");
    const cb = vi.fn();
    onRommConnectionChange(cb);
    setRommConnectionState("connected"); // same value — no-op
    expect(cb).not.toHaveBeenCalled();
  });

  it("unsubscribe stops further notifications", () => {
    const cb = vi.fn();
    const unsubscribe = onRommConnectionChange(cb);
    setRommConnectionState("offline");
    expect(cb).toHaveBeenCalledTimes(1);
    unsubscribe();
    setRommConnectionState("connected");
    expect(cb).toHaveBeenCalledTimes(1); // no second call after unsubscribe
  });

  it("reportServerReachable(true) → connected, (false) → offline", () => {
    reportServerReachable(false);
    expect(getRommConnectionState()).toBe("offline");
    reportServerReachable(true);
    expect(getRommConnectionState()).toBe("connected");
  });

  it("reportServerReachable notifies every subscriber", () => {
    const a = vi.fn();
    const b = vi.fn();
    onRommConnectionChange(a);
    onRommConnectionChange(b);
    reportServerReachable(false);
    expect(a).toHaveBeenCalledExactlyOnceWith("offline");
    expect(b).toHaveBeenCalledExactlyOnceWith("offline");
  });

  // A dozen call sites write this state and the user only sees the result, so
  // the transition has to name the signal behind it (#1670).
  it("logs each transition with its previous state, next state and reason", () => {
    setRommConnectionState("offline", "fast probe");
    expect(vi.mocked(debugLog)).toHaveBeenCalledExactlyOnceWith("connectionState: checking -> offline (fast probe)");
  });

  it("logs 'unspecified' when a caller names no reason", () => {
    setRommConnectionState("connected");
    expect(vi.mocked(debugLog)).toHaveBeenCalledExactlyOnceWith("connectionState: checking -> connected (unspecified)");
  });

  it("reportServerReachable names itself as the reason", () => {
    reportServerReachable(true);
    expect(vi.mocked(debugLog)).toHaveBeenCalledExactlyOnceWith(
      "connectionState: checking -> connected (reachability report)",
    );
  });

  it("logs nothing when the state is unchanged", () => {
    setRommConnectionState("offline", "fast probe");
    vi.mocked(debugLog).mockClear();
    setRommConnectionState("offline", "authoritative verdict");
    expect(vi.mocked(debugLog)).not.toHaveBeenCalled();
  });
});

describe("serverRetryProgress store (#1345)", () => {
  beforeEach(() => {
    setServerRetryProgress(null);
  });

  it("starts empty (null)", () => {
    expect(getServerRetryProgress()).toBeNull();
  });

  it("sets and reads back a progress value, notifying subscribers", () => {
    const cb = vi.fn();
    onServerRetryProgressChange(cb);
    setServerRetryProgress({ attempt: 2, maxAttempts: 3 });
    expect(getServerRetryProgress()).toEqual({ attempt: 2, maxAttempts: 3 });
    expect(cb).toHaveBeenCalledExactlyOnceWith({ attempt: 2, maxAttempts: 3 });
  });

  it("clearing to null notifies and resets", () => {
    setServerRetryProgress({ attempt: 3, maxAttempts: 3 });
    const cb = vi.fn();
    onServerRetryProgressChange(cb);
    setServerRetryProgress(null);
    expect(getServerRetryProgress()).toBeNull();
    expect(cb).toHaveBeenCalledExactlyOnceWith(null);
  });

  it("does NOT notify when clearing an already-clear store", () => {
    const cb = vi.fn();
    onServerRetryProgressChange(cb);
    setServerRetryProgress(null); // already null — no-op
    expect(cb).not.toHaveBeenCalled();
  });

  it("unsubscribe stops further notifications", () => {
    const cb = vi.fn();
    const unsubscribe = onServerRetryProgressChange(cb);
    setServerRetryProgress({ attempt: 2, maxAttempts: 3 });
    expect(cb).toHaveBeenCalledTimes(1);
    unsubscribe();
    setServerRetryProgress({ attempt: 3, maxAttempts: 3 });
    expect(cb).toHaveBeenCalledTimes(1);
  });
});

describe("shared server-load generation (#1345 F2)", () => {
  beforeEach(() => {
    setServerRetryProgress(null);
  });

  it("clears the retry frame when the settling load is still the newest", () => {
    const load = beginServerLoad();
    setServerRetryProgress({ attempt: 2, maxAttempts: 3 });
    settleServerLoad(load);
    expect(getServerRetryProgress()).toBeNull();
  });

  // The case the pair exists for: the save-slot lane and the achievements lane
  // feed one store, so an older load settling late must leave the newer lane's
  // live frame standing — and that newer lane must still be able to clear it.
  it("keeps the frame a newer load of another lane owns, and lets that lane clear it", () => {
    const slots = beginServerLoad();
    const achievements = beginServerLoad();
    setServerRetryProgress({ attempt: 2, maxAttempts: 3 });

    settleServerLoad(slots);
    expect(getServerRetryProgress()).toEqual({ attempt: 2, maxAttempts: 3 });

    settleServerLoad(achievements);
    expect(getServerRetryProgress()).toBeNull();
  });

  it("supersedes a load re-issued by the same lane", () => {
    const firstAttempt = beginServerLoad();
    const reload = beginServerLoad();
    setServerRetryProgress({ attempt: 1, maxAttempts: 3 });

    settleServerLoad(firstAttempt);
    expect(getServerRetryProgress()).toEqual({ attempt: 1, maxAttempts: 3 });

    settleServerLoad(reload);
    expect(getServerRetryProgress()).toBeNull();
  });

  it("hands out a distinct claim per call", () => {
    expect(beginServerLoad()).not.toEqual(beginServerLoad());
  });
});

// One load's frames come from several lanes' ladders writing one slot, so
// without an order the shown attempt walks backwards mid-load (#1758).
describe("retry-progress monotonicity within one load (#1758)", () => {
  beforeEach(() => {
    setServerRetryProgress(null);
  });

  it("ignores a frame below the one already shown", () => {
    beginServerLoad();
    setServerRetryProgress({ attempt: 3, maxAttempts: 3 });
    setServerRetryProgress({ attempt: 2, maxAttempts: 3 });
    expect(getServerRetryProgress()).toEqual({ attempt: 3, maxAttempts: 3 });
  });

  it("does not notify subscribers about a frame it ignored", () => {
    beginServerLoad();
    setServerRetryProgress({ attempt: 3, maxAttempts: 3 });
    const cb = vi.fn();
    onServerRetryProgressChange(cb);
    setServerRetryProgress({ attempt: 1, maxAttempts: 3 });
    expect(cb).not.toHaveBeenCalled();
  });

  it("accepts a frame above the one already shown", () => {
    beginServerLoad();
    setServerRetryProgress({ attempt: 2, maxAttempts: 3 });
    setServerRetryProgress({ attempt: 3, maxAttempts: 3 });
    expect(getServerRetryProgress()).toEqual({ attempt: 3, maxAttempts: 3 });
  });

  // The climb is per load, not for all time: the next load's ladder starts at
  // attempt 1 and that 1 is the truth about it.
  it("starts the climb over on the next load", () => {
    beginServerLoad();
    setServerRetryProgress({ attempt: 3, maxAttempts: 3 });
    beginServerLoad();
    setServerRetryProgress({ attempt: 1, maxAttempts: 3 });
    expect(getServerRetryProgress()).toEqual({ attempt: 1, maxAttempts: 3 });
  });

  it("starts the climb over once the shown frame is cleared", () => {
    beginServerLoad();
    setServerRetryProgress({ attempt: 3, maxAttempts: 3 });
    setServerRetryProgress(null);
    setServerRetryProgress({ attempt: 1, maxAttempts: 3 });
    expect(getServerRetryProgress()).toEqual({ attempt: 1, maxAttempts: 3 });
  });

  it("a settling load clears the frame it holds, however high it climbed", () => {
    const load = beginServerLoad();
    setServerRetryProgress({ attempt: 3, maxAttempts: 3 });
    settleServerLoad(load);
    expect(getServerRetryProgress()).toBeNull();
  });
});

describe("useVersionError", () => {
  // The store is a module singleton; reset it so no test leaks an error into a
  // sibling. act-wrapped: a hook may still be mounted when this runs, and the
  // reset notifies it.
  afterEach(() => {
    act(() => {
      setVersionError(null);
    });
  });

  it("returns the current error on mount", () => {
    setVersionError("initial err");
    const { result } = renderHook(() => useVersionError());
    expect(result.current).toBe("initial err");
  });

  it("returns null when no error is set", () => {
    setVersionError(null);
    const { result } = renderHook(() => useVersionError());
    expect(result.current).toBeNull();
  });

  it("updates when setVersionError fires after mount", () => {
    setVersionError(null);
    const { result } = renderHook(() => useVersionError());
    expect(result.current).toBeNull();
    act(() => {
      setVersionError("new err");
    });
    expect(result.current).toBe("new err");
  });

  it("subscribes with the store's own seam, so a re-render does not re-subscribe", () => {
    expectStableSubscribe(useVersionError, onVersionErrorChange);
  });
});
