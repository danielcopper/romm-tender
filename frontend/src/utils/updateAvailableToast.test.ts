import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { toaster } from "../api/host";
import {
  acknowledgeUpdateAvailableToast,
  getUpdateNotice,
  type UpdateInstallAttempt,
  type UpdateNotice,
} from "../api/backend";
import { PLUGIN_NAME } from "./toast";
import { resetFailedUpdateToastsForTests } from "./failedUpdateToast";
import { updateAvailableToast, watchUpdateAvailableToast } from "./updateAvailableToast";
import {
  fetchUpdateNotice,
  getUpdateNoticeState,
  resetUpdateNoticeStoreForTests,
  setUpdateNoticeState,
  takePushedUpdateNotice,
} from "./updateNoticeStore";
import {
  onUpdateOutcomeChange,
  resetUpdateOutcomeStoreForTests,
  setUpdateOutcomeState,
  type UpdateOutcomeState,
} from "./updateOutcomeStore";
import { endStoppedAttempt, resetStoppedUpdateStoreForTests, takePushedStoppedAttempt } from "./stoppedUpdateStore";
import {
  endPress,
  notePress,
  resetUpdateInstallStoreForTests,
  seedUpdateInstallAttempt,
  setUpdateInstallAttempt,
} from "./updateInstallStore";
import { resetNotificationsHealthForTests, setNotificationsUnavailable } from "./notificationsHealth";
import { TOAST_READINESS_POLL_MS } from "./steamReadyForToasts";

const OWED: UpdateNotice = {
  available: true,
  newer: true,
  latest_version: "1.1.0",
  current_version: "1.0.0",
  enabled: true,
  installed_program: true,
  toast_owed: true,
};

const BODY = "Tender 1.1.0 is available. Settings › Updates to install it.";

const downloading: UpdateInstallAttempt = {
  version: "1.1.0",
  step: "downloading",
  bytes_done: 0,
  bytes_total: null,
  failure: null,
};

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

/** The reads the toast must see succeed, each already answered as *succeeded*. */
const reads = (...succeeded: boolean[]) => succeeded.map((ok) => Promise.resolve(ok));

/** Let every pending promise chain run, the Steam-readiness wait's included. */
async function flush(): Promise<void> {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
}

describe("the toast that a newer release is out", () => {
  let stop: () => void = () => undefined;

  beforeEach(() => {
    vi.stubGlobal("App", { GetServicesInitialized: () => true });
    vi.stubGlobal("securitystore", { IsLockScreenActive: () => false });
    vi.stubGlobal("SteamUIStore", { WindowStore: { GamepadUIMainWindowInstance: null } });
    resetFailedUpdateToastsForTests();
    resetUpdateNoticeStoreForTests();
    resetUpdateOutcomeStoreForTests();
    resetStoppedUpdateStoreForTests();
    resetUpdateInstallStoreForTests();
    resetNotificationsHealthForTests();
    vi.mocked(toaster.toast).mockClear();
    vi.mocked(acknowledgeUpdateAvailableToast).mockReset().mockResolvedValue({ success: true });
  });

  afterEach(() => stop());

  /** The load-time reads, already answered. */
  const watchAnswered = () => {
    stop = watchUpdateAvailableToast(Promise.resolve(), reads(true, true, true));
  };

  it("says which release is out and where to install it", () => {
    expect(updateAvailableToast("1.1.0")).toBe(BODY);
  });

  it("is raised once under Tender's name, then acknowledged by its version", async () => {
    takePushedUpdateNotice(OWED);
    watchAnswered();
    await flush();

    expect(toaster.toast).toHaveBeenCalledOnce();
    expect(toaster.toast).toHaveBeenCalledWith({ title: PLUGIN_NAME, body: BODY });
    expect(acknowledgeUpdateAvailableToast).toHaveBeenCalledExactlyOnceWith("1.1.0");
    const [acknowledged] = vi.mocked(acknowledgeUpdateAvailableToast).mock.invocationCallOrder;
    const [raised] = vi.mocked(toaster.toast).mock.invocationCallOrder;
    expect(acknowledged).toBeGreaterThan(raised ?? Infinity);
  });

  it("is not raised where the backend owes none", async () => {
    takePushedUpdateNotice({ ...OWED, toast_owed: false });
    watchAnswered();
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("is not raised once the switch went off after the backend answered", async () => {
    takePushedUpdateNotice(OWED);
    setUpdateNoticeState({ ...getUpdateNoticeState(), enabled: false });
    watchAnswered();
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("is not raised where the card would not show: a failure record for the same release stands", async () => {
    takePushedUpdateNotice(OWED);
    setUpdateOutcomeState({
      announcement: null,
      failure: {
        attemptedVersion: "1.1.0",
        restoredVersion: "1.0.0",
        rolledBackAt: "2026-09-29T10:00:00Z",
        kind: "rollback",
      },
      failureDismissed: true,
    });
    watchAnswered();
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("is not raised where a stopped attempt at the same release stands", async () => {
    takePushedUpdateNotice(OWED);
    takePushedStoppedAttempt({
      attempted_version: "1.1.0",
      from_version: "1.0.0",
      started_at: "2026-09-29T10:00:00Z",
      toast_owed: false,
    });
    watchAnswered();
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("is not raised for a dismissed card", async () => {
    takePushedUpdateNotice({ ...OWED, available: false });
    watchAnswered();
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("waits while an install attempt is under way, and is raised once it ended in failure", async () => {
    takePushedUpdateNotice(OWED);
    setUpdateInstallAttempt(downloading);
    watchAnswered();
    await flush();
    expect(toaster.toast).not.toHaveBeenCalled();

    setUpdateInstallAttempt({ ...downloading, step: "failed", failure: "download_failed" });
    await flush();

    expect(toaster.toast).toHaveBeenCalledExactlyOnceWith({ title: PLUGIN_NAME, body: BODY });
  });

  it("waits until every load-time read has settled, so a failure record answering last suppresses it", async () => {
    const notice = deferred();
    const outcome = deferred<boolean>();
    const stopped = deferred<boolean>();
    stop = watchUpdateAvailableToast(notice.promise, [outcome.promise, stopped.promise]);

    takePushedUpdateNotice(OWED);
    notice.resolve();
    stopped.resolve(true);
    await flush();
    expect(toaster.toast).not.toHaveBeenCalled();

    setUpdateOutcomeState({
      announcement: null,
      failure: {
        attemptedVersion: "1.1.0",
        restoredVersion: "1.0.0",
        rolledBackAt: "2026-09-29T10:00:00Z",
        kind: "check",
      },
      failureDismissed: false,
    });
    outcome.resolve(true);
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("is raised once the last read settles, where nothing suppresses it", async () => {
    const outcome = deferred<boolean>();
    stop = watchUpdateAvailableToast(Promise.resolve(), [outcome.promise, Promise.resolve(true)]);
    takePushedUpdateNotice(OWED);
    await flush();
    expect(toaster.toast).not.toHaveBeenCalled();

    outcome.resolve(true);
    await flush();

    expect(toaster.toast).toHaveBeenCalledOnce();
  });

  it("raises one toast for a read and a push of the same release", async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue(OWED);
    const read = fetchUpdateNotice();
    stop = watchUpdateAvailableToast(read, reads(true, true));
    await flush();

    takePushedUpdateNotice({ ...OWED });
    await flush();

    expect(toaster.toast).toHaveBeenCalledOnce();
    expect(acknowledgeUpdateAvailableToast).toHaveBeenCalledOnce();
  });

  it("raises its own toast for a newer release pushed later", async () => {
    takePushedUpdateNotice(OWED);
    watchAnswered();
    await flush();

    takePushedUpdateNotice({ ...OWED, latest_version: "1.2.0" });
    await flush();

    expect(toaster.toast).toHaveBeenCalledTimes(2);
    expect(toaster.toast).toHaveBeenLastCalledWith({
      title: PLUGIN_NAME,
      body: "Tender 1.2.0 is available. Settings › Updates to install it.",
    });
    expect(acknowledgeUpdateAvailableToast).toHaveBeenLastCalledWith("1.2.0");
  });

  it("raises nothing for reads that settle after it was unsubscribed", async () => {
    const outcome = deferred<boolean>();
    takePushedUpdateNotice(OWED);
    watchUpdateAvailableToast(Promise.resolve(), [outcome.promise])();

    outcome.resolve(true);
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("ends an earlier watch when a second one starts, so only the second one's reads decide", async () => {
    watchUpdateAvailableToast(Promise.resolve(), reads(true));
    await flush();
    const pending = deferred<boolean>();
    stop = watchUpdateAvailableToast(Promise.resolve(), [pending.promise]);

    takePushedUpdateNotice(OWED);
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("asks nothing once unsubscribed", async () => {
    watchAnswered();
    await flush();
    stop();

    takePushedUpdateNotice(OWED);
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it.each<[string, () => Promise<boolean>]>([
    ["answered that it failed", () => Promise.resolve(false)],
    ["rejected", () => Promise.reject(new Error("socket closed"))],
  ])(
    "raises nothing in this context, and acknowledges nothing, where a read it waits for %s",
    async (_case, failed) => {
      // The failed read is what the last update did: a record for 1.1.0 it
      // would have filled in may stand, so the toast stays owed instead.
      takePushedUpdateNotice(OWED);
      stop = watchUpdateAvailableToast(Promise.resolve(), [failed(), Promise.resolve(true)]);
      await flush();

      takePushedUpdateNotice({ ...OWED });
      await flush();

      expect(toaster.toast).not.toHaveBeenCalled();
      expect(acknowledgeUpdateAvailableToast).not.toHaveBeenCalled();
    },
  );

  it("raises it where only the release read failed, once a push says it is owed", async () => {
    stop = watchUpdateAvailableToast(Promise.reject(new Error("socket closed")), reads(true, true));
    await flush();

    takePushedUpdateNotice(OWED);
    await flush();

    expect(toaster.toast).toHaveBeenCalledExactlyOnceWith({ title: PLUGIN_NAME, body: BODY });
  });

  it("is held from a press of Install until that attempt ended, a stopped attempt's card coming down at it included", async () => {
    takePushedUpdateNotice(OWED);
    takePushedStoppedAttempt({
      attempted_version: "1.1.0",
      from_version: "1.0.0",
      started_at: "2026-09-29T10:00:00Z",
      toast_owed: false,
    });
    watchAnswered();
    await flush();

    notePress();
    endStoppedAttempt();
    await flush();
    expect(toaster.toast).not.toHaveBeenCalled();

    setUpdateInstallAttempt(downloading);
    await flush();
    expect(toaster.toast).not.toHaveBeenCalled();

    setUpdateInstallAttempt({ ...downloading, step: "failed", failure: "download_failed" });
    await flush();
    expect(toaster.toast).toHaveBeenCalledExactlyOnceWith({ title: PLUGIN_NAME, body: BODY });
  });

  it("is raised once a press of Install did not start an attempt", async () => {
    takePushedUpdateNotice(OWED);
    notePress();
    watchAnswered();
    await flush();
    expect(toaster.toast).not.toHaveBeenCalled();

    endPress();
    await flush();

    expect(toaster.toast).toHaveBeenCalledOnce();
  });

  it("is held by an attempt under way that the install's read at panel load found", async () => {
    takePushedUpdateNotice(OWED);
    seedUpdateInstallAttempt(downloading);
    watchAnswered();
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
    expect(acknowledgeUpdateAvailableToast).not.toHaveBeenCalled();
  });

  it("is neither raised nor acknowledged while Steam's notification lookups are missing", async () => {
    setNotificationsUnavailable(true);
    takePushedUpdateNotice(OWED);
    watchAnswered();
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
    expect(acknowledgeUpdateAvailableToast).not.toHaveBeenCalled();
  });

  it("throws nothing over a store state it cannot be worked out from, so the listeners after it still run", async () => {
    takePushedUpdateNotice(OWED);
    watchAnswered();
    await flush();
    vi.mocked(toaster.toast).mockClear();
    const after = vi.fn();
    const unsubscribe = onUpdateOutcomeChange(after);

    try {
      expect(() => setUpdateOutcomeState(null as unknown as UpdateOutcomeState)).not.toThrow();
    } finally {
      unsubscribe();
    }

    expect(after).toHaveBeenCalledOnce();
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  describe("while Steam cannot show it yet", () => {
    let servicesUp = false;

    beforeEach(() => {
      servicesUp = false;
      vi.stubGlobal("App", { GetServicesInitialized: () => servicesUp });
      vi.useFakeTimers();
    });

    afterEach(() => vi.useRealTimers());

    /** Let the watch's reads settle and the readiness wait take a few rounds. */
    const wait = () => vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS * 4);

    const steamUp = async () => {
      servicesUp = true;
      await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS);
    };

    it("waits until it can, and is acknowledged only once raised", async () => {
      takePushedUpdateNotice(OWED);
      watchAnswered();
      await wait();

      expect(toaster.toast).not.toHaveBeenCalled();
      expect(acknowledgeUpdateAvailableToast).not.toHaveBeenCalled();

      await steamUp();

      expect(toaster.toast).toHaveBeenCalledExactlyOnceWith({ title: PLUGIN_NAME, body: BODY });
      expect(acknowledgeUpdateAvailableToast).toHaveBeenCalledExactlyOnceWith("1.1.0");
      const [raised] = vi.mocked(toaster.toast).mock.invocationCallOrder;
      const [acknowledged] = vi.mocked(acknowledgeUpdateAvailableToast).mock.invocationCallOrder;
      expect(acknowledged).toBeGreaterThan(raised ?? Infinity);
    });

    it("is neither raised nor acknowledged where the card was dismissed meanwhile", async () => {
      takePushedUpdateNotice(OWED);
      watchAnswered();
      await wait();

      setUpdateNoticeState({ ...getUpdateNoticeState(), available: false });
      await steamUp();

      expect(toaster.toast).not.toHaveBeenCalled();
      expect(acknowledgeUpdateAvailableToast).not.toHaveBeenCalled();
    });

    it("is neither raised nor acknowledged where Install was pressed meanwhile, and is raised once that attempt failed", async () => {
      takePushedUpdateNotice(OWED);
      watchAnswered();
      await wait();

      notePress();
      await steamUp();
      expect(toaster.toast).not.toHaveBeenCalled();
      expect(acknowledgeUpdateAvailableToast).not.toHaveBeenCalled();

      setUpdateInstallAttempt({ ...downloading, step: "failed", failure: "download_failed" });
      await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS);

      expect(toaster.toast).toHaveBeenCalledExactlyOnceWith({ title: PLUGIN_NAME, body: BODY });
      expect(acknowledgeUpdateAvailableToast).toHaveBeenCalledExactlyOnceWith("1.1.0");
    });

    it("raises only the newer release where one was pushed meanwhile", async () => {
      takePushedUpdateNotice(OWED);
      watchAnswered();
      await wait();

      takePushedUpdateNotice({ ...OWED, latest_version: "1.2.0" });
      await steamUp();

      expect(toaster.toast).toHaveBeenCalledExactlyOnceWith({
        title: PLUGIN_NAME,
        body: "Tender 1.2.0 is available. Settings › Updates to install it.",
      });
      expect(acknowledgeUpdateAvailableToast).toHaveBeenCalledExactlyOnceWith("1.2.0");
    });
  });
});
