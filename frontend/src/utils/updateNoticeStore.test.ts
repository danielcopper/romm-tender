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
  takePushedUpdateNotice,
} from "./updateNoticeStore";

const NOTICE: UpdateNotice = {
  available: true,
  newer: true,
  latest_version: "0.34.0",
  current_version: "0.33.0",
  enabled: true,
  installed_program: true,
  toast_owed: false,
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
      toastOwed: false,
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
      toastOwed: false,
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

    it("a refused Dismiss still rejects when a later press overtook it, and the later press stands", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);
      await fetchUpdateNotice();
      const refused = deferred<UpdateSettingWrite>();
      vi.mocked(dismissUpdateNotice).mockReturnValueOnce(refused.promise);

      const dismissing = dismissUpdateForVersion("0.34.0");
      await setUpdateCheckSwitch(false);
      refused.resolve({ success: false, reason: "invalid_value", message: "Invalid version" });

      await expect(dismissing).rejects.toThrow("invalid_value: Invalid version");
      expect(getUpdateNoticeState()).toMatchObject({ enabled: false, available: true, latestVersion: "0.34.0" });
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
    it("off keeps the card and the version, as the backend does, and reads nothing", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue(NOTICE);
      await fetchUpdateNotice();

      await setUpdateCheckSwitch(false);

      expect(setUpdateCheckEnabled).toHaveBeenCalledWith(false);
      expect(getUpdateNoticeState()).toMatchObject({
        enabled: false,
        available: true,
        newer: true,
        latestVersion: "0.34.0",
      });
      expect(getUpdateNotice).toHaveBeenCalledTimes(1);
    });

    it("on starts the read the switch allows again", async () => {
      vi.mocked(getUpdateNotice).mockResolvedValue({ ...NOTICE, enabled: false });
      await fetchUpdateNotice();
      vi.mocked(getUpdateNotice).mockResolvedValue({ ...NOTICE, latest_version: "0.35.0" });

      await setUpdateCheckSwitch(true);
      await vi.waitFor(() => expect(getUpdateNoticeState().latestVersion).toBe("0.35.0"));

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

    it("a refused switch still rejects when a later press overtook it, and the later press stands", async () => {
      const refused = deferred<UpdateSettingWrite>();
      vi.mocked(setUpdateCheckEnabled).mockReturnValueOnce(refused.promise).mockResolvedValueOnce({ success: true });

      const first = setUpdateCheckSwitch(true);
      await setUpdateCheckSwitch(false);
      refused.resolve({ success: false, reason: "invalid_value", message: "Invalid value" });

      await expect(first).rejects.toThrow("invalid_value: Invalid value");
      expect(getUpdateNoticeState().enabled).toBe(false);
    });

    it("a read issued before the switch went off does not show it on again", async () => {
      const read = deferred<UpdateNotice>();
      vi.mocked(getUpdateNotice).mockReturnValue(read.promise);

      const reading = fetchUpdateNotice();
      await setUpdateCheckSwitch(false);
      read.resolve(NOTICE);
      await reading;

      expect(getUpdateNoticeState()).toMatchObject({ enabled: false, latestVersion: null });
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

    it("reports what a check found with the switch off as it would with the switch on", async () => {
      vi.mocked(checkForUpdateNow).mockResolvedValue(now({ enabled: false }));

      expect(await runUpdateCheckNow()).toBe("found");
      expect(getUpdateNoticeState()).toMatchObject({ enabled: false, available: true });
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
  describe("a notice the backend pushed", () => {
    it("is taken as the answer", () => {
      takePushedUpdateNotice({ ...NOTICE, latest_version: "0.35.0" });

      expect(getUpdateNoticeState()).toEqual({
        available: true,
        newer: true,
        latestVersion: "0.35.0",
        currentVersion: "0.33.0",
        enabled: true,
        installedProgram: true,
        toastOwed: false,
      });
    });

    it("tells every subscriber", () => {
      const heard = vi.fn();
      const stop = onUpdateNoticeChange(heard);

      takePushedUpdateNotice(NOTICE);
      stop();

      expect(heard).toHaveBeenCalledTimes(1);
    });

    it("overtakes a read still in flight, whose older answer then writes nothing", async () => {
      const slow = deferred<UpdateNotice>();
      vi.mocked(getUpdateNotice).mockReturnValueOnce(slow.promise);

      const reading = fetchUpdateNotice();
      takePushedUpdateNotice({ ...NOTICE, latest_version: "0.35.0" });
      slow.resolve({ ...NOTICE, latest_version: "0.34.0" });
      await reading;

      expect(getUpdateNoticeState().latestVersion).toBe("0.35.0");
    });
  });
});
