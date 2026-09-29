import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import * as backend from "../../api/backend";
import {
  getUpdateInstallState,
  installUpdate,
  type UpdateInstallAttempt,
  type UpdateInstallState,
} from "../../api/backend";
import { setUpdateInstallAttempt } from "../../utils/updateInstallStore";
import { INSTALL_REQUEST_FAILED } from "../../utils/updateInstallView";
import { UPDATE_INSTALL_POLL_MS, useUpdateInstall } from "./useUpdateInstall";

const OFFERED: UpdateInstallState = {
  offered: true,
  version: "1.0.0",
  wait_reasons: [],
  paused_downloads: 0,
  attempt: null,
  try_again: false,
};

const DOWNLOADING: UpdateInstallAttempt = {
  version: "1.0.0",
  step: "downloading",
  bytes_done: 10,
  bytes_total: 100,
  failure: null,
};

const FAILED: UpdateInstallAttempt = { ...DOWNLOADING, step: "failed", failure: "download_failed" };

/** A promise the test settles by hand. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

const tick = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(UPDATE_INSTALL_POLL_MS);
  });

describe("useUpdateInstall", () => {
  let logError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.useFakeTimers();
    setUpdateInstallAttempt(null);
    vi.mocked(getUpdateInstallState).mockReset().mockResolvedValue(OFFERED);
    vi.mocked(installUpdate).mockReset().mockResolvedValue({ success: true });
    logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
  });

  afterEach(() => {
    logError.mockRestore();
    vi.useRealTimers();
  });

  it("reads the state at mount and every three seconds, and stops once unmounted", async () => {
    const { result, unmount } = renderHook(() => useUpdateInstall());
    await flush();

    expect(getUpdateInstallState).toHaveBeenCalledTimes(1);
    expect(result.current.offered).toBe(true);
    expect(result.current.version).toBe("1.0.0");

    await tick();
    await tick();
    expect(getUpdateInstallState).toHaveBeenCalledTimes(3);

    unmount();
    await tick();
    await tick();
    expect(getUpdateInstallState).toHaveBeenCalledTimes(3);
  });

  it("issues no second read while the first has not answered", async () => {
    vi.mocked(getUpdateInstallState).mockReturnValue(new Promise(() => {}));
    renderHook(() => useUpdateInstall());

    await tick();
    await tick();

    expect(getUpdateInstallState).toHaveBeenCalledTimes(1);
  });

  it("offers nothing until a read has answered", () => {
    vi.mocked(getUpdateInstallState).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useUpdateInstall());

    expect(result.current.offered).toBe(false);
    expect(result.current.version).toBeNull();
  });

  it("drops the wait reasons once a later read no longer names them", async () => {
    vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, wait_reasons: [{ reason: "library_sync" }] });
    const { result } = renderHook(() => useUpdateInstall());
    await flush();
    expect(result.current.waitReasons).toEqual([{ reason: "library_sync" }]);

    vi.mocked(getUpdateInstallState).mockResolvedValue(OFFERED);
    await tick();

    expect(result.current.waitReasons).toEqual([]);
  });

  it("installs the offered version, and shows the attempt downloading once the press is answered", async () => {
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    await flush();

    expect(installUpdate).toHaveBeenCalledWith("1.0.0");
    expect(result.current.attempt).toMatchObject({ version: "1.0.0", step: "downloading", failure: null });
    expect(result.current.pressing).toBe(false);
  });

  it("forgets an earlier attempt's pushed failure at the press", async () => {
    vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, attempt: FAILED, try_again: true });
    setUpdateInstallAttempt(FAILED);
    const { result } = renderHook(() => useUpdateInstall());
    await flush();
    expect(result.current.attempt?.step).toBe("failed");

    act(() => result.current.install());
    await flush();

    expect(result.current.attempt?.step).toBe("downloading");
  });

  it("writes nothing from a read issued before the press that lands after it", async () => {
    const { result } = renderHook(() => useUpdateInstall());
    await flush();
    const late = deferred<UpdateInstallState>();
    vi.mocked(getUpdateInstallState).mockReturnValueOnce(late.promise);
    await tick();

    act(() => result.current.install());
    await flush();
    await act(async () => {
      late.resolve({ ...OFFERED, attempt: FAILED, wait_reasons: [{ reason: "save_sync" }] });
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.attempt?.step).toBe("downloading");
    expect(result.current.waitReasons).toEqual([]);
  });

  it("moves on with the frames the backend pushes between reads", async () => {
    const { result } = renderHook(() => useUpdateInstall());
    await flush();
    act(() => result.current.install());
    await flush();

    act(() => setUpdateInstallAttempt({ ...DOWNLOADING, step: "verifying" }));

    expect(result.current.attempt?.step).toBe("verifying");
  });

  it("keeps the further step when a read lands behind a pushed frame", async () => {
    const { result } = renderHook(() => useUpdateInstall());
    await flush();
    act(() => result.current.install());
    await flush();
    act(() => setUpdateInstallAttempt({ ...DOWNLOADING, step: "installer_started" }));

    vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, attempt: DOWNLOADING });
    await tick();

    expect(result.current.attempt?.step).toBe("installer_started");
    expect(result.current.restarting).toBe(true);
  });

  it("shows what a refused press waits for", async () => {
    vi.mocked(installUpdate).mockResolvedValue({
      success: false,
      reason: "update_waiting",
      message: "Something that an update would interrupt is still under way",
      wait_reasons: [{ reason: "app_running", apps: ["Celeste"] }],
    });
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    await flush();

    expect(result.current.waitReasons).toEqual([{ reason: "app_running", apps: ["Celeste"] }]);
    expect(result.current.refusal).toBe("");
    expect(result.current.attempt).toBeNull();
  });

  it("shows the message of any other refusal", async () => {
    vi.mocked(installUpdate).mockResolvedValue({
      success: false,
      reason: "version_changed",
      message: "The release offered is now 1.0.1",
    });
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    await flush();

    expect(result.current.refusal).toBe("The release offered is now 1.0.1");
  });

  it("says a press the connection failed to carry could not be requested, and logs it", async () => {
    vi.mocked(installUpdate).mockRejectedValue(new Error("socket gone"));
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    await flush();

    expect(result.current.refusal).toBe(INSTALL_REQUEST_FAILED);
    expect(logError).toHaveBeenCalledWith(expect.stringContaining("socket gone"));
  });

  it("sends one press while the first waits for its answer", async () => {
    const answer = deferred<{ success: true }>();
    vi.mocked(installUpdate).mockReturnValue(answer.promise);
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    act(() => result.current.install());
    expect(result.current.pressing).toBe(true);
    await act(async () => {
      answer.resolve({ success: true });
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(installUpdate).toHaveBeenCalledTimes(1);
  });

  it("says Try again for a pushed failure of the offered version before a read says so", async () => {
    const { result } = renderHook(() => useUpdateInstall());
    await flush();
    expect(result.current.tryAgain).toBe(false);

    act(() => setUpdateInstallAttempt(FAILED));

    expect(result.current.tryAgain).toBe(true);
  });

  it("logs a read that failed while no installer is running", async () => {
    vi.mocked(getUpdateInstallState).mockRejectedValue(new Error("boom"));
    renderHook(() => useUpdateInstall());
    await flush();

    expect(logError).toHaveBeenCalledWith(expect.stringContaining("Failed to read the update install state"));
  });

  it("keeps quiet about a read the installer's restart took down", async () => {
    const { result } = renderHook(() => useUpdateInstall());
    await flush();
    act(() => setUpdateInstallAttempt({ ...DOWNLOADING, step: "installer_started" }));

    vi.mocked(getUpdateInstallState).mockRejectedValue(new Error("connection_lost"));
    await tick();

    expect(logError).not.toHaveBeenCalled();
    expect(result.current.attempt?.step).toBe("installer_started");
    expect(result.current.refusal).toBe("");
  });
});
