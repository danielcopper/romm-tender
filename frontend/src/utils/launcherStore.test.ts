import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { expectStableSubscribe } from "../test-utils/store-hook-subscription";
import {
  getLauncherState,
  onLauncherChange,
  resetLauncherStoreForTests,
  setLauncherRelocated,
  useLauncherRelocated,
} from "./launcherStore";

// Fakes nothing — the real useSyncExternalStore runs. The wrapper only records
// what the hook passes it; expectStableSubscribe's docstring explains why the
// property is unreachable from the store's side. The vi.mock is hoisted, so it
// has to live here rather than in the helper.
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return { ...actual, useSyncExternalStore: vi.fn(actual.useSyncExternalStore) };
});

describe("launcherStore", () => {
  beforeEach(() => {
    resetLauncherStoreForTests();
  });

  it("starts unestablished, because no pass has run yet", () => {
    expect(getLauncherState()).toEqual({ relocated: false });
  });

  it("setLauncherRelocated updates the state and notifies subscribers", () => {
    const fn = vi.fn();
    onLauncherChange(fn);
    setLauncherRelocated(true);
    expect(getLauncherState()).toEqual({ relocated: true });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("onLauncherChange returns an unsubscribe that stops notifications", () => {
    const fn = vi.fn();
    const unsub = onLauncherChange(fn);
    unsub();
    setLauncherRelocated(true);
    expect(fn).not.toHaveBeenCalled();
  });

  describe("snapshot identity", () => {
    it("returns the same object reference while nothing changes", () => {
      expect(getLauncherState()).toBe(getLauncherState());
    });

    it("returns a different object reference after a write", () => {
      const before = getLauncherState();
      setLauncherRelocated(true);
      expect(getLauncherState()).not.toBe(before);
      // The old snapshot is untouched — the write did not go in place.
      expect(before.relocated).toBe(false);
    });
  });

  describe("useLauncherRelocated", () => {
    it("renders the current answer and re-renders on a change", () => {
      const { result, unmount } = renderHook(() => useLauncherRelocated());
      expect(result.current).toBe(false);

      act(() => {
        setLauncherRelocated(true);
      });
      expect(result.current).toBe(true);
      unmount();
    });

    it("stops re-rendering after unmount", () => {
      let renders = 0;
      const { unmount } = renderHook(() => {
        renders += 1;
        return useLauncherRelocated();
      });
      unmount();
      const afterUnmount = renders;
      act(() => {
        setLauncherRelocated(true);
      });
      expect(renders).toBe(afterUnmount);
    });

    it("subscribes with the store's own seam, so a re-render does not re-subscribe", () => {
      expectStableSubscribe(useLauncherRelocated, onLauncherChange);
    });
  });
});
