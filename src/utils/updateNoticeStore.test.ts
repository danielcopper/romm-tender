import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { expectStableSubscribe } from "../test-utils/store-hook-subscription";
import { dismissUpdateNotice, getUpdateNotice, setUpdateCheckEnabled } from "../api/backend";
import {
  dismissUpdateForVersion,
  fetchUpdateNotice,
  getUpdateNoticeState,
  onUpdateNoticeChange,
  resetUpdateNoticeStoreForTests,
  setUpdateCheckSwitch,
  setUpdateNoticeState,
  useUpdateNoticeState,
  type UpdateNoticeState,
} from "./updateNoticeStore";

// Fakes nothing — the real useSyncExternalStore runs. See the same mock in
// legacyInstallStore.test.ts; the vi.mock is hoisted, so it has to live here.
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return { ...actual, useSyncExternalStore: vi.fn(actual.useSyncExternalStore) };
});

const WIRE = {
  available: true,
  latest_version: "0.33.0",
  current_version: "0.32.0",
  download_url: "https://example.invalid/Tender.zip",
  install_url: "https://example.invalid/tender-v0.33.0/Tender.zip",
  plugin_name: "Tender",
  digest: "abc123",
  enabled: true,
};

const AVAILABLE: UpdateNoticeState = {
  available: true,
  latestVersion: "0.33.0",
  currentVersion: "0.32.0",
  downloadUrl: "https://example.invalid/Tender.zip",
  installUrl: "https://example.invalid/tender-v0.33.0/Tender.zip",
  pluginName: "Tender",
  digest: "abc123",
  enabled: true,
};

describe("updateNoticeStore", () => {
  beforeEach(() => {
    resetUpdateNoticeStoreForTests();
    vi.mocked(getUpdateNotice).mockReset();
    vi.mocked(dismissUpdateNotice).mockReset();
    vi.mocked(setUpdateCheckEnabled).mockReset();
  });

  it("starts with nothing available and the check switched on", () => {
    expect(getUpdateNoticeState()).toEqual({
      available: false,
      latestVersion: null,
      currentVersion: "",
      downloadUrl: "",
      installUrl: "",
      pluginName: "",
      digest: null,
      enabled: true,
    });
  });

  it("setUpdateNoticeState updates the state and notifies subscribers", () => {
    const fn = vi.fn();
    onUpdateNoticeChange(fn);
    setUpdateNoticeState(AVAILABLE);
    expect(getUpdateNoticeState()).toEqual(AVAILABLE);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("onUpdateNoticeChange returns an unsubscribe that stops notifications", () => {
    const fn = vi.fn();
    const unsub = onUpdateNoticeChange(fn);
    unsub();
    setUpdateNoticeState(AVAILABLE);
    expect(fn).not.toHaveBeenCalled();
  });

  it("every write installs a new snapshot object", () => {
    const before = getUpdateNoticeState();
    setUpdateNoticeState(AVAILABLE);
    expect(getUpdateNoticeState()).not.toBe(before);
    expect(before.available).toBe(false);
  });

  describe("fetchUpdateNotice", () => {
    it("maps the backend shape and updates the store", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue(WIRE);
      const result = await fetchUpdateNotice();
      expect(result).toEqual(AVAILABLE);
      expect(getUpdateNoticeState()).toEqual(AVAILABLE);
    });

    it("carries a null digest and a null version through untouched", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue({
        ...WIRE,
        available: false,
        latest_version: null,
        digest: null,
      });
      const result = await fetchUpdateNotice();
      expect(result.latestVersion).toBeNull();
      expect(result.digest).toBeNull();
      expect(result.available).toBe(false);
    });
  });

  describe("dismissUpdateForVersion", () => {
    it("persists the version the card is showing, then takes the card down", async () => {
      setUpdateNoticeState(AVAILABLE);
      vi.mocked(dismissUpdateNotice).mockResolvedValue({ success: true });
      await dismissUpdateForVersion("0.33.0");
      expect(dismissUpdateNotice).toHaveBeenCalledWith("0.33.0");
      expect(getUpdateNoticeState().available).toBe(false);
      // Everything an install would need survives the dismissal.
      expect(getUpdateNoticeState().latestVersion).toBe("0.33.0");
    });

    it("leaves the card standing when the backend write fails", async () => {
      setUpdateNoticeState(AVAILABLE);
      vi.mocked(dismissUpdateNotice).mockRejectedValue(new Error("nope"));
      await expect(dismissUpdateForVersion("0.33.0")).rejects.toThrow("nope");
      expect(getUpdateNoticeState().available).toBe(true);
    });
  });

  describe("setUpdateCheckSwitch", () => {
    it("switching off persists it and clears the card", async () => {
      setUpdateNoticeState(AVAILABLE);
      vi.mocked(setUpdateCheckEnabled).mockResolvedValue({ success: true });
      await setUpdateCheckSwitch(false);
      expect(setUpdateCheckEnabled).toHaveBeenCalledWith(false);
      expect(getUpdateNoticeState().enabled).toBe(false);
      expect(getUpdateNoticeState().available).toBe(false);
      expect(getUpdateNotice).not.toHaveBeenCalled();
    });

    it("switching on persists it and starts a fresh read without awaiting it", async () => {
      setUpdateNoticeState({ ...AVAILABLE, enabled: false, available: false });
      vi.mocked(setUpdateCheckEnabled).mockResolvedValue({ success: true });
      let release: (() => void) | undefined;
      vi.mocked(getUpdateNotice).mockReturnValue(
        new Promise((resolve) => {
          release = () => resolve(WIRE);
        }),
      );

      await setUpdateCheckSwitch(true);
      // The switch is already reflected while the read is still in flight.
      expect(getUpdateNoticeState().enabled).toBe(true);
      expect(getUpdateNoticeState().available).toBe(false);
      expect(getUpdateNotice).toHaveBeenCalledTimes(1);

      release?.();
      await vi.waitFor(() => expect(getUpdateNoticeState().available).toBe(true));
    });

    it("leaves the switch alone when the backend write fails", async () => {
      setUpdateNoticeState(AVAILABLE);
      vi.mocked(setUpdateCheckEnabled).mockRejectedValue(new Error("nope"));
      await expect(setUpdateCheckSwitch(false)).rejects.toThrow("nope");
      expect(getUpdateNoticeState().enabled).toBe(true);
      expect(getUpdateNoticeState().available).toBe(true);
    });

    it("discards a read still in flight when the user switches back off (#race)", async () => {
      // The press the fence exists for: switch on, the read sits on GitHub, the
      // user changes their mind. The stale payload carries `enabled: true`.
      setUpdateNoticeState({ ...AVAILABLE, enabled: false, available: false });
      vi.mocked(setUpdateCheckEnabled).mockResolvedValue({ success: true });
      let release: (() => void) | undefined;
      vi.mocked(getUpdateNotice).mockReturnValue(
        new Promise((resolve) => {
          release = () => resolve(WIRE);
        }),
      );

      await setUpdateCheckSwitch(true);
      expect(getUpdateNoticeState().enabled).toBe(true);

      await setUpdateCheckSwitch(false);
      expect(getUpdateNoticeState().enabled).toBe(false);

      // The read from the first press lands only now, with enabled: true on it.
      release?.();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();

      expect(getUpdateNoticeState().enabled).toBe(false);
      expect(getUpdateNoticeState().available).toBe(false);
    });

    it("discards a read still in flight when the user dismisses the card", async () => {
      let release: (() => void) | undefined;
      vi.mocked(getUpdateNotice).mockReturnValue(
        new Promise((resolve) => {
          release = () => resolve(WIRE);
        }),
      );
      vi.mocked(dismissUpdateNotice).mockResolvedValue({ success: true });

      const inFlight = fetchUpdateNotice();
      setUpdateNoticeState(AVAILABLE);
      await dismissUpdateForVersion("0.33.0");
      expect(getUpdateNoticeState().available).toBe(false);

      release?.();
      await inFlight;

      // The plugin-load read would have put the card back up.
      expect(getUpdateNoticeState().available).toBe(false);
    });

    it("swallows a failed refresh after switching on, leaving the switch on", async () => {
      setUpdateNoticeState({ ...AVAILABLE, enabled: false, available: false });
      vi.mocked(setUpdateCheckEnabled).mockResolvedValue({ success: true });
      vi.mocked(getUpdateNotice).mockRejectedValue(new Error("offline"));
      await expect(setUpdateCheckSwitch(true)).resolves.toBeUndefined();
      expect(getUpdateNoticeState().enabled).toBe(true);
      expect(getUpdateNoticeState().available).toBe(false);
    });
  });

  describe("useUpdateNoticeState", () => {
    it("renders the current notice and re-renders on a real change", () => {
      const { result, unmount } = renderHook(() => useUpdateNoticeState());
      expect(result.current.available).toBe(false);

      act(() => {
        setUpdateNoticeState(AVAILABLE);
      });
      expect(result.current).toEqual(AVAILABLE);
      unmount();
    });

    it("stops re-rendering after unmount", () => {
      let renders = 0;
      const { unmount } = renderHook(() => {
        renders += 1;
        return useUpdateNoticeState();
      });
      unmount();
      const afterUnmount = renders;
      act(() => {
        setUpdateNoticeState(AVAILABLE);
      });
      expect(renders).toBe(afterUnmount);
    });

    it("subscribes with the store's own seam, so a re-render does not re-subscribe", () => {
      expectStableSubscribe(useUpdateNoticeState, onUpdateNoticeChange);
    });
  });
});
