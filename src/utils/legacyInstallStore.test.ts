import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { expectStableSubscribe } from "../test-utils/store-hook-subscription";
import { getLegacyInstallNotice } from "../api/backend";
import {
  getLegacyInstallState,
  setLegacyInstallState,
  onLegacyInstallChange,
  fetchLegacyInstallState,
  useLegacyInstallState,
} from "./legacyInstallStore";

// Fakes nothing — the real useSyncExternalStore runs. The wrapper only records
// what the hook passes it, which is the one way to see whether the subscribe
// reference is stable; expectStableSubscribe's docstring explains why the
// property is unreachable from the store's side. The vi.mock is hoisted, so it
// has to live here rather than in the helper.
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return { ...actual, useSyncExternalStore: vi.fn(actual.useSyncExternalStore) };
});

describe("legacyInstallStore", () => {
  beforeEach(() => {
    setLegacyInstallState({ pending: false, legacyDataPresent: false });
    vi.mocked(getLegacyInstallNotice).mockReset();
  });

  it("starts not-pending", () => {
    expect(getLegacyInstallState()).toEqual({ pending: false, legacyDataPresent: false });
  });

  it("setLegacyInstallState updates the state and notifies subscribers", () => {
    const fn = vi.fn();
    onLegacyInstallChange(fn);
    setLegacyInstallState({ pending: true, legacyDataPresent: true });
    expect(getLegacyInstallState()).toEqual({ pending: true, legacyDataPresent: true });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("onLegacyInstallChange returns an unsubscribe that stops notifications", () => {
    const fn = vi.fn();
    const unsub = onLegacyInstallChange(fn);
    unsub();
    setLegacyInstallState({ pending: true, legacyDataPresent: false });
    expect(fn).not.toHaveBeenCalled();
  });

  it("fetchLegacyInstallState maps the backend shape and updates the store", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: true });
    const result = await fetchLegacyInstallState();
    expect(result).toEqual({ pending: true, legacyDataPresent: true });
    expect(getLegacyInstallState()).toEqual({ pending: true, legacyDataPresent: true });
  });

  it("fetchLegacyInstallState clears the store when the backend reports not-pending", async () => {
    setLegacyInstallState({ pending: true, legacyDataPresent: true });
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: false, legacy_data_present: false });
    const result = await fetchLegacyInstallState();
    expect(result).toEqual({ pending: false, legacyDataPresent: false });
    expect(getLegacyInstallState()).toEqual({ pending: false, legacyDataPresent: false });
  });

  it("leaves the store untouched when the backend read rejects", async () => {
    vi.mocked(getLegacyInstallNotice).mockRejectedValue(new Error("backend down"));
    const before = getLegacyInstallState();

    await expect(fetchLegacyInstallState()).rejects.toThrow("backend down");

    // No card, and no half-written state either: the rejection installs nothing,
    // so the caller's own catch (index.tsx logs it) is the whole handling. The
    // snapshot is the SAME object, which is what a subscriber would compare.
    expect(getLegacyInstallState()).toBe(before);
    expect(getLegacyInstallState()).toEqual({ pending: false, legacyDataPresent: false });
  });

  it("keeps a notice already shown when a later read rejects", async () => {
    setLegacyInstallState({ pending: true, legacyDataPresent: true });
    vi.mocked(getLegacyInstallNotice).mockRejectedValue(new Error("backend down"));

    await expect(fetchLegacyInstallState()).rejects.toThrow("backend down");

    // A failed read is not an answer — retracting the launcher warning on one
    // would be the worst possible reading of it.
    expect(getLegacyInstallState()).toEqual({ pending: true, legacyDataPresent: true });
  });

  describe("snapshot identity", () => {
    it("returns the same object reference while nothing changes", () => {
      setLegacyInstallState({ pending: true, legacyDataPresent: false });
      expect(getLegacyInstallState()).toBe(getLegacyInstallState());
    });

    it("returns a different object reference after a real change", () => {
      setLegacyInstallState({ pending: true, legacyDataPresent: false });
      const before = getLegacyInstallState();
      setLegacyInstallState({ pending: true, legacyDataPresent: true });
      expect(getLegacyInstallState()).not.toBe(before);
      // The old snapshot is untouched — the write did not go in place.
      expect(before.legacyDataPresent).toBe(false);
    });
  });

  describe("useLegacyInstallState", () => {
    it("renders the current notice and re-renders on a real change", () => {
      setLegacyInstallState({ pending: true, legacyDataPresent: false });
      const { result, unmount } = renderHook(() => useLegacyInstallState());
      expect(result.current).toEqual({ pending: true, legacyDataPresent: false });

      act(() => {
        setLegacyInstallState({ pending: true, legacyDataPresent: true });
      });
      expect(result.current).toEqual({ pending: true, legacyDataPresent: true });
      unmount();
    });

    it("stops re-rendering after unmount", () => {
      let renders = 0;
      const { unmount } = renderHook(() => {
        renders += 1;
        return useLegacyInstallState();
      });
      unmount();
      const afterUnmount = renders;
      act(() => {
        setLegacyInstallState({ pending: true, legacyDataPresent: false });
      });
      expect(renders).toBe(afterUnmount);
    });

    it("subscribes with the store's own seam, so a re-render does not re-subscribe", () => {
      expectStableSubscribe(useLegacyInstallState, onLegacyInstallChange);
    });
  });
});
