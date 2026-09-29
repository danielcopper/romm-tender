import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import * as backend from "../../api/backend";
import {
  getUpdateInstallState,
  installUpdate,
  type UpdateInstallAttempt,
  type UpdateInstallState,
} from "../../api/backend";
import {
  getStoppedUpdateAttempt,
  resetStoppedUpdateStoreForTests,
  takePushedStoppedAttempt,
} from "../../utils/stoppedUpdateStore";
import { setUpdateInstallAttempt } from "../../utils/updateInstallStore";
import { INSTALL_REQUEST_FAILED, INSTALLER_OVERDUE_MS } from "../../utils/updateInstallView";
import { UPDATE_INSTALL_POLL_MS, UPDATE_INSTALL_READ_DEADLINE_MS, useUpdateInstall } from "./useUpdateInstall";

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
    resetStoppedUpdateStoreForTests();
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

  it("takes a stopped attempt's card down once the backend accepted the press, and not before", async () => {
    takePushedStoppedAttempt({ attempted_version: "1.0.0", from_version: "0.9.0", started_at: "2026-09-29T10:00:00Z" });
    const answer = deferred<{ success: true }>();
    vi.mocked(installUpdate).mockReturnValue(answer.promise);
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    await flush();
    expect(getStoppedUpdateAttempt()).not.toBeNull();

    await act(async () => {
      answer.resolve({ success: true });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getStoppedUpdateAttempt()).toBeNull();
  });

  it("leaves a stopped attempt's card up where the press was refused", async () => {
    takePushedStoppedAttempt({ attempted_version: "1.0.0", from_version: "0.9.0", started_at: "2026-09-29T10:00:00Z" });
    vi.mocked(installUpdate).mockResolvedValue({ success: false, reason: "not_offered", message: "none" });
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    await flush();

    expect(getStoppedUpdateAttempt()).not.toBeNull();
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

  it.each([
    ["update_in_progress", "An update is already being installed."],
    ["not_offered", "There is no newer release to install."],
    ["version_changed", "The release on offer has changed — press again to install it."],
  ] as const)("words a %s refusal itself, whatever the backend's message says", async (reason, sentence) => {
    vi.mocked(installUpdate).mockResolvedValue({ success: false, reason, message: "the backend's own words" });
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    await flush();

    expect(result.current.refusal).toBe(sentence);
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

  it("writes nothing from a read issued while the press waited that lands after its answer", async () => {
    const answer = deferred<{ success: true }>();
    vi.mocked(installUpdate).mockReturnValue(answer.promise);
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    const late = deferred<UpdateInstallState>();
    vi.mocked(getUpdateInstallState).mockReturnValueOnce(late.promise);
    await tick();
    await act(async () => {
      answer.resolve({ success: true });
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      late.resolve({ ...OFFERED, attempt: FAILED });
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.attempt?.step).toBe("downloading");
  });

  it("takes no read that lands while the press still waits for its answer", async () => {
    const answer = deferred<{ success: true }>();
    vi.mocked(installUpdate).mockReturnValue(answer.promise);
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, attempt: FAILED, try_again: true });
    await tick();

    expect(result.current.attempt).toBeNull();
    expect(result.current.tryAgain).toBe(false);
  });

  it("sends no press while the last read names something to wait for", async () => {
    vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, wait_reasons: [{ reason: "save_sync" }] });
    const { result } = renderHook(() => useUpdateInstall());
    await flush();

    act(() => result.current.install());
    await flush();

    expect(installUpdate).not.toHaveBeenCalled();
  });

  it("sends no press while an attempt is under way", async () => {
    vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, attempt: DOWNLOADING });
    const { result } = renderHook(() => useUpdateInstall());
    await flush();
    expect(result.current.underWay).toBe(true);

    act(() => result.current.install());
    await flush();

    expect(installUpdate).not.toHaveBeenCalled();
  });

  describe("a refusal", () => {
    const refusedWith = async (reason: "update_in_progress" | "not_offered" | "version_changed") => {
      vi.mocked(installUpdate).mockResolvedValue({ success: false, reason, message: "" });
      const view = renderHook(() => useUpdateInstall());
      await flush();
      act(() => view.result.current.install());
      await flush();
      expect(view.result.current.refusal).not.toBe("");
      return view;
    };

    it("goes once a read names another version than the press did", async () => {
      const { result } = await refusedWith("version_changed");
      vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, version: "1.0.1" });

      await tick();

      expect(result.current.refusal).toBe("");
    });

    it("stays while a read still names the version the press did", async () => {
      const { result } = await refusedWith("version_changed");

      await tick();

      expect(result.current.refusal).not.toBe("");
    });

    it("goes, for one nothing was offered for, once a read names nothing", async () => {
      const { result } = await refusedWith("not_offered");
      vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, offered: false, version: null });

      await tick();

      expect(result.current.refusal).toBe("");
    });

    it("stays, for an update already under way, while one is, and goes once none is", async () => {
      const { result } = await refusedWith("update_in_progress");
      vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, attempt: DOWNLOADING });
      await tick();
      expect(result.current.refusal).toBe("An update is already being installed.");

      vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, attempt: FAILED });
      await tick();

      expect(result.current.refusal).toBe("");
    });

    it("goes, for a press the connection did not carry, once a read answers again", async () => {
      vi.mocked(installUpdate).mockRejectedValue(new Error("socket gone"));
      const { result } = renderHook(() => useUpdateInstall());
      await flush();
      act(() => result.current.install());
      await flush();
      expect(result.current.refusal).toBe(INSTALL_REQUEST_FAILED);

      await tick();

      expect(result.current.refusal).toBe("");
    });
  });

  describe("a read that does not answer", () => {
    it("counts as failed once its deadline passes, and stays the one read in flight", async () => {
      const pending = deferred<UpdateInstallState>();
      vi.mocked(getUpdateInstallState).mockReturnValueOnce(pending.promise);
      const { result } = renderHook(() => useUpdateInstall());
      await act(async () => {
        await vi.advanceTimersByTimeAsync(UPDATE_INSTALL_READ_DEADLINE_MS - 1);
      });
      expect(result.current.readFailed).toBe(false);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(result.current.readFailed).toBe(true);
      await tick();
      expect(getUpdateInstallState).toHaveBeenCalledTimes(1);

      await act(async () => {
        pending.resolve(OFFERED);
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(result.current.readFailed).toBe(false);
      expect(result.current.offered).toBe(true);
    });

    it("is said, and is no longer once a read answers again", async () => {
      vi.mocked(getUpdateInstallState).mockRejectedValue(new Error("boom"));
      const { result } = renderHook(() => useUpdateInstall());
      await flush();
      expect(result.current.readFailed).toBe(true);

      vi.mocked(getUpdateInstallState).mockResolvedValue(OFFERED);
      await tick();

      expect(result.current.readFailed).toBe(false);
      expect(result.current.offered).toBe(true);
    });
  });

  describe("an installer that has not stopped this backend within five minutes", () => {
    const INSTALLER_STARTED: UpdateInstallAttempt = { ...DOWNLOADING, step: "installer_started" };

    it("is not overdue before five minutes have passed since the panel first saw it", async () => {
      const { result } = renderHook(() => useUpdateInstall());
      await flush();
      act(() => setUpdateInstallAttempt(INSTALLER_STARTED));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(INSTALLER_OVERDUE_MS - 1000);
      });

      expect(result.current.restarting).toBe(true);
      expect(result.current.overdue).toBe(false);
    });

    it("is overdue after five minutes, with reads failing where the backend is gone", async () => {
      const { result } = renderHook(() => useUpdateInstall());
      await flush();
      act(() => setUpdateInstallAttempt(INSTALLER_STARTED));
      vi.mocked(getUpdateInstallState).mockRejectedValue(new Error("connection_lost"));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(INSTALLER_OVERDUE_MS);
      });

      expect(result.current.overdue).toBe(true);
      expect(result.current.readFailed).toBe(true);
    });

    it("is overdue after five minutes with the backend gone, where a read never settles rather than failing", async () => {
      const { result } = renderHook(() => useUpdateInstall());
      await flush();
      act(() => setUpdateInstallAttempt(INSTALLER_STARTED));
      vi.mocked(getUpdateInstallState).mockReturnValue(new Promise(() => {}));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(INSTALLER_OVERDUE_MS);
      });

      expect(result.current.overdue).toBe(true);
      expect(result.current.readFailed).toBe(true);
    });

    it("is overdue after five minutes with reads still answering where the installer has not stopped it", async () => {
      vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, attempt: INSTALLER_STARTED });
      const { result } = renderHook(() => useUpdateInstall());
      await flush();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(INSTALLER_OVERDUE_MS);
      });

      expect(result.current.overdue).toBe(true);
      expect(result.current.readFailed).toBe(false);
    });

    it("counts the five minutes from when the panel first saw it, across the section leaving the screen", async () => {
      const first = renderHook(() => useUpdateInstall());
      await flush();
      act(() => setUpdateInstallAttempt(INSTALLER_STARTED));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(INSTALLER_OVERDUE_MS - 1000);
      });
      first.unmount();

      const { result } = renderHook(() => useUpdateInstall());
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });

      expect(result.current.overdue).toBe(true);
    });
  });
});
