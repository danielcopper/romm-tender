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
import { resetUpdateOutcomeStoreForTests, setUpdateOutcomeState } from "./updateOutcomeStore";
import { resetStoppedUpdateStoreForTests, takePushedStoppedAttempt } from "./stoppedUpdateStore";
import { setUpdateInstallAttempt } from "./updateInstallStore";

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

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}

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
    setUpdateInstallAttempt(null);
    vi.mocked(toaster.toast).mockClear();
    vi.mocked(acknowledgeUpdateAvailableToast).mockReset().mockResolvedValue({ success: true });
  });

  afterEach(() => stop());

  /** The three load-time reads, already answered. */
  const watchAnswered = () => {
    stop = watchUpdateAvailableToast([Promise.resolve(), Promise.resolve(), Promise.resolve()]);
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

  it("waits until all three load-time reads have settled, so a failure record answering last suppresses it", async () => {
    const notice = deferred();
    const outcome = deferred();
    const stopped = deferred();
    stop = watchUpdateAvailableToast([notice.promise, outcome.promise, stopped.promise]);

    takePushedUpdateNotice(OWED);
    notice.resolve();
    stopped.resolve();
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
    outcome.resolve();
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("is raised once the last read settles, where nothing suppresses it", async () => {
    const outcome = deferred();
    stop = watchUpdateAvailableToast([Promise.resolve(), outcome.promise, Promise.resolve()]);
    takePushedUpdateNotice(OWED);
    await flush();
    expect(toaster.toast).not.toHaveBeenCalled();

    outcome.resolve();
    await flush();

    expect(toaster.toast).toHaveBeenCalledOnce();
  });

  it("raises one toast for a read and a push of the same release", async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue(OWED);
    const read = fetchUpdateNotice();
    stop = watchUpdateAvailableToast([read, Promise.resolve(), Promise.resolve()]);
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
    const outcome = deferred();
    takePushedUpdateNotice(OWED);
    watchUpdateAvailableToast([Promise.resolve(), outcome.promise, Promise.resolve()])();

    outcome.resolve();
    await flush();

    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("ends an earlier watch when a second one starts, so only the second one's reads decide", async () => {
    watchUpdateAvailableToast([Promise.resolve()]);
    await flush();
    const pending = deferred();
    stop = watchUpdateAvailableToast([pending.promise]);

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
});
