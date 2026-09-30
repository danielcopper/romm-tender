import { afterEach, describe, expect, it, vi } from "vitest";
import type { UpdateInstallAttempt } from "../api/backend";
import {
  attemptSeenAt,
  getUpdateInstallAttempt,
  installerRestarting,
  installerSeenAt,
  noteAttempt,
  onUpdateInstallAttemptChange,
  setUpdateInstallAttempt,
} from "./updateInstallStore";
import { INSTALLER_OVERDUE_MS } from "./updateInstallView";

const DOWNLOADING: UpdateInstallAttempt = {
  version: "1.1.0",
  step: "downloading",
  bytes_done: 10,
  bytes_total: 100,
  failure: null,
};

describe("updateInstallStore", () => {
  afterEach(() => {
    setUpdateInstallAttempt(null);
  });

  it("holds no attempt until the backend reports one", () => {
    expect(getUpdateInstallAttempt()).toBeNull();
  });

  it("holds the latest frame as it was reported", () => {
    setUpdateInstallAttempt(DOWNLOADING);
    const failed: UpdateInstallAttempt = { ...DOWNLOADING, step: "failed", failure: "checksum_mismatch" };
    setUpdateInstallAttempt(failed);

    expect(getUpdateInstallAttempt()).toEqual(failed);
  });

  it("tells a subscriber of every frame until it stops listening", () => {
    const heard = vi.fn();
    const stop = onUpdateInstallAttemptChange(heard);

    setUpdateInstallAttempt(DOWNLOADING);
    stop();
    setUpdateInstallAttempt({ ...DOWNLOADING, bytes_done: 20 });

    expect(heard).toHaveBeenCalledTimes(1);
  });

  describe("when the installer was first seen started", () => {
    const STARTED: UpdateInstallAttempt = { ...DOWNLOADING, step: "installer_started" };

    it("is the first frame or read that showed it, and a later one does not move it", () => {
      noteAttempt(STARTED, 1000);
      noteAttempt(STARTED, 5000);
      setUpdateInstallAttempt(STARTED);

      expect(installerSeenAt()).toBe(1000);
    });

    it("is nothing for an attempt that has not got that far", () => {
      noteAttempt(DOWNLOADING, 1000);

      expect(installerSeenAt()).toBeNull();
    });

    it("counts as restarting for seven minutes and no longer", () => {
      noteAttempt(STARTED, 1000);

      expect(installerRestarting(1000 + INSTALLER_OVERDUE_MS - 1)).toBe(true);
      expect(installerRestarting(1000 + INSTALLER_OVERDUE_MS)).toBe(false);
    });

    it("is forgotten at a press, which clears the attempt", () => {
      noteAttempt(STARTED, 1000);

      setUpdateInstallAttempt(null);

      expect(installerSeenAt()).toBeNull();
      expect(installerRestarting(1001)).toBe(false);
    });
  });

  describe("when the attempt was first seen under way", () => {
    it("is the first frame or read that showed it, whatever its step, and a later one does not move it", () => {
      noteAttempt(DOWNLOADING, 1000);
      noteAttempt({ ...DOWNLOADING, step: "installer_started" }, 5000);

      expect(attemptSeenAt()).toBe(1000);
    });

    it("is nothing for an attempt first seen failed", () => {
      noteAttempt({ ...DOWNLOADING, step: "failed", failure: "download_failed" }, 1000);

      expect(attemptSeenAt()).toBeNull();
    });

    it("is forgotten at a press, so the next attempt counts from its own start", () => {
      noteAttempt(DOWNLOADING, 1000);

      setUpdateInstallAttempt(null);
      noteAttempt(DOWNLOADING, 9000);

      expect(attemptSeenAt()).toBe(9000);
    });
  });
});
