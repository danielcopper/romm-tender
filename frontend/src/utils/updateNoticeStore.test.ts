import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  checkForUpdateNow,
  dismissUpdateNotice,
  getUpdateNotice,
  setUpdateCheckEnabled,
  type UpdateCheckNow,
  type UpdateNotice,
  type UpdateSettingWrite,
} from "../api/backend";
import {
  dismissUpdateForVersion,
  fetchUpdateNotice,
  getUpdateNoticeState,
  onUpdateNoticeChange,
  resetUpdateNoticeStoreForTests,
  runUpdateCheckNow,
  setUpdateCheckSwitch,
} from "./updateNoticeStore";

const NOTICE: UpdateNotice = {
  available: true,
  newer: true,
  latest_version: "0.34.0",
  current_version: "0.33.0",
  enabled: true,
  installed_program: true,
};

const now = (over: Partial<UpdateCheckNow> = {}): UpdateCheckNow => ({ ...NOTICE, reached: true, ...over });

/** A promise the test resolves by hand, to hold a call in flight. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("updateNoticeStore", () => {
  beforeEach(() => {
    resetUpdateNoticeStoreForTests();
    vi.mocked(getUpdateNotice).mockReset();
    vi.mocked(checkForUpdateNow).mockReset();
    vi.mocked(dismissUpdateNotice).mockReset().mockResolvedValue({ success: true });
    vi.mocked(setUpdateCheckEnabled).mockReset().mockResolvedValue({ success: true });
  });

  it("starts with no card, the check on, and no version known", () => {
    expect(getUpdateNoticeState()).toEqual({
      available: false,
      newer: false,
      latestVersion: null,
      currentVersion: "",
      enabled: true,
      installedProgram: false,
    });
  });

  it("maps the backend's answer onto the store and notifies", async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);
    const listener = vi.fn();
    onUpdateNoticeChange(listener);

    await fetchUpdateNotice();

    expect(getUpdateNoticeState()).toEqual({
      available: true,
      newer: true,
      latestVersion: "0.34.0",
      currentVersion: "0.33.0",
      enabled: true,
      installedProgram: true,
    });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("an unsubscribed listener hears nothing more", async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);
    const listener = vi.fn();
    onUpdateNoticeChange(listener)();

    await fetchUpdateNotice();

    expect(listener).not.toHaveBeenCalled();
  });

  describe("Dismiss", () => {
    it("takes the card down only after the backend accepted it", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);
      await fetchUpdateNotice();
      const persist = deferred<UpdateSettingWrite>();
      vi.mocked(dismissUpdateNotice).mockReturnValue(persist.promise);

      const pending = dismissUpdateForVersion("0.34.0");
      expect(getUpdateNoticeState().available).toBe(true);
      persist.resolve({ success: true });
      await pending;

      expect(dismissUpdateNotice).toHaveBeenCalledWith("0.34.0");
      expect(getUpdateNoticeState().available).toBe(false);
      expect(getUpdateNoticeState().newer).toBe(true);
    });

    it("leaves the card up and rejects when the backend refuses the write", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);
      await fetchUpdateNotice();
      vi.mocked(dismissUpdateNotice).mockResolvedValue({
        success: false,
        reason: "invalid_value",
        message: "Invalid version",
      });

      await expect(dismissUpdateForVersion("0.34.0")).rejects.toThrow("invalid_value: Invalid version");

      expect(getUpdateNoticeState().available).toBe(true);
    });

    it("leaves the card up when the write fails", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);
      await fetchUpdateNotice();
      vi.mocked(dismissUpdateNotice).mockRejectedValue(new Error("gone"));

      await expect(dismissUpdateForVersion("0.34.0")).rejects.toThrow("gone");

      expect(getUpdateNoticeState().available).toBe(true);
    });
  });

  describe("the switch", () => {
    it("off drops the card and the version, as the backend does", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);
      await fetchUpdateNotice();

      await setUpdateCheckSwitch(false);

      expect(setUpdateCheckEnabled).toHaveBeenCalledWith(false);
      expect(getUpdateNoticeState()).toMatchObject({
        enabled: false,
        available: false,
        newer: false,
        latestVersion: null,
      });
    });

    it("on reads again, because nothing held here can restore the card", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue({ ...NOTICE, enabled: false, available: false });
      await fetchUpdateNotice();
      vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);

      await setUpdateCheckSwitch(true);
      await vi.waitFor(() => expect(getUpdateNoticeState().available).toBe(true));

      expect(getUpdateNoticeState().enabled).toBe(true);
      expect(getUpdateNotice).toHaveBeenCalledTimes(2);
    });

    it("a refused switch rejects and changes nothing", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);
      await fetchUpdateNotice();
      vi.mocked(setUpdateCheckEnabled).mockResolvedValue({
        success: false,
        reason: "invalid_value",
        message: "Invalid value",
      });

      await expect(setUpdateCheckSwitch(false)).rejects.toThrow("invalid_value: Invalid value");

      expect(getUpdateNoticeState()).toMatchObject({ enabled: true, available: true, latestVersion: "0.34.0" });
      expect(getUpdateNotice).toHaveBeenCalledTimes(1);
    });

    it("a read issued before the switch went off does not put the card back", async () => {
      const read = deferred<UpdateNotice>();
      vi.mocked(getUpdateNotice).mockReturnValue(read.promise);

      const reading = fetchUpdateNotice();
      await setUpdateCheckSwitch(false);
      read.resolve(NOTICE);
      await reading;

      expect(getUpdateNoticeState()).toMatchObject({ enabled: false, available: false });
    });

    it("of two presses in flight, the later one wins", async () => {
      const first = deferred<UpdateSettingWrite>();
      vi.mocked(setUpdateCheckEnabled).mockReturnValueOnce(first.promise).mockResolvedValueOnce({ success: true });

      const on = setUpdateCheckSwitch(true);
      await setUpdateCheckSwitch(false);
      first.resolve({ success: true });
      await on;

      expect(getUpdateNoticeState().enabled).toBe(false);
      expect(getUpdateNotice).not.toHaveBeenCalled();
    });
  });

  describe("Check now", () => {
    it("reports a newer release as found", async () => {
      vi.mocked(checkForUpdateNow).mockResolvedValue(now());

      expect(await runUpdateCheckNow()).toBe("found");
      expect(getUpdateNoticeState().available).toBe(true);
    });

    it("reports a reading that found nothing newer as none", async () => {
      vi.mocked(checkForUpdateNow).mockResolvedValue(now({ available: false, newer: false }));

      expect(await runUpdateCheckNow()).toBe("none");
    });

    it("tells a reading that reached nothing apart from nothing being out", async () => {
      vi.mocked(checkForUpdateNow).mockResolvedValue(now({ reached: false, available: false, newer: false }));

      expect(await runUpdateCheckNow()).toBe("unreachable");
    });

    it("reports a switched-off check as off, whatever else the answer says", async () => {
      vi.mocked(checkForUpdateNow).mockResolvedValue(now({ enabled: false, reached: false, available: false }));

      expect(await runUpdateCheckNow()).toBe("off");
    });

    it("an overtaken press writes nothing and says so", async () => {
      const slow = deferred<UpdateCheckNow>();
      vi.mocked(checkForUpdateNow).mockReturnValueOnce(slow.promise);

      const pressed = runUpdateCheckNow();
      await setUpdateCheckSwitch(false);
      slow.resolve(now());

      expect(await pressed).toBe("superseded");
      expect(getUpdateNoticeState().available).toBe(false);
    });
  });
});
