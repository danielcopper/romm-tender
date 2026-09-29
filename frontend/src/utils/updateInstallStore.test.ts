import { afterEach, describe, expect, it, vi } from "vitest";
import type { UpdateInstallAttempt } from "../api/backend";
import { getUpdateInstallAttempt, onUpdateInstallAttemptChange, setUpdateInstallAttempt } from "./updateInstallStore";

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
});
