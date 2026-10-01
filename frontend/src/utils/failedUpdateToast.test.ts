import { describe, it, expect, beforeEach, vi } from "vitest";
import { toaster } from "../api/host";
import {
  acknowledgeUpdateAttemptToast,
  getUpdateAttemptToast,
  logError,
  logWarn,
  type UpdateAttemptToast,
  type UpdateInstallFailure,
} from "../api/backend";
import { PLUGIN_NAME } from "./toast";
import {
  attemptFailureToast,
  raiseFailureToastOnce,
  raiseUpdateToastOnce,
  resetFailedUpdateToastsForTests,
  stillOnToast,
  toastOwedAttempt,
} from "./failedUpdateToast";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  logError: vi.fn(),
  logWarn: vi.fn(),
}));

const failedWith = (failure: UpdateInstallFailure, attempt = 1): UpdateAttemptToast => ({
  attempt,
  version: "1.0.52",
  failure,
});

/** Steam ready for a toast at once: services up, no lock screen, the desktop client's absent Big Picture window. */
function steamReady(): void {
  vi.stubGlobal("App", { GetServicesInitialized: () => true });
  vi.stubGlobal("securitystore", { IsLockScreenActive: () => false });
  vi.stubGlobal("SteamUIStore", { WindowStore: { GamepadUIMainWindowInstance: null } });
}

describe("failedUpdateToast", () => {
  beforeEach(() => {
    steamReady();
    resetFailedUpdateToastsForTests();
    vi.mocked(toaster.toast).mockClear();
    vi.mocked(logWarn).mockClear();
    vi.mocked(logError).mockClear();
    vi.mocked(getUpdateAttemptToast).mockReset();
    vi.mocked(acknowledgeUpdateAttemptToast).mockReset();
  });

  describe("the words", () => {
    it("says an update that left the earlier version in place failed, and where to read why", () => {
      expect(stillOnToast("1.0.52", "1.0.51")).toBe(
        "Update to 1.0.52 failed. You are still on 1.0.51. Settings › Updates shows why.",
      );
    });

    it.each<[UpdateInstallFailure, string]>([
      ["download_failed", "Update to 1.0.52 failed. The download failed."],
      ["checksum_mismatch", "Update to 1.0.52 failed. The download did not match its checksum."],
      ["installer_not_started", "Update to 1.0.52 failed. The installer could not be started."],
      ["installer_stopped", "Update to 1.0.52 failed. The installer stopped without updating."],
      ["new_version_does_not_start", "Update to 1.0.52 failed. The new version does not start."],
      ["game_started", "Update to 1.0.52 was cancelled. A game was started. Nothing was changed."],
      [
        "running_apps_unknown",
        "Update to 1.0.52 was cancelled. Could not check whether a game is running. Nothing was changed.",
      ],
    ])("words an attempt that failed with %s as %s", (failure, body) => {
      expect(attemptFailureToast(failedWith(failure))).toBe(body);
    });

    it("names a failure a later backend reports, and this panel has no line for, by its title alone", () => {
      expect(attemptFailureToast(failedWith("unheard_of" as UpdateInstallFailure))).toBe("Update to 1.0.52 failed.");
    });
  });

  describe("an attempt's toast the backend still owes", () => {
    it("is raised under Tender's name, then acknowledged by the attempt's number", async () => {
      vi.mocked(getUpdateAttemptToast).mockResolvedValue(failedWith("game_started", 3));

      await toastOwedAttempt();

      expect(toaster.toast).toHaveBeenCalledOnce();
      expect(toaster.toast).toHaveBeenCalledWith({
        title: PLUGIN_NAME,
        body: "Update to 1.0.52 was cancelled. A game was started. Nothing was changed.",
      });
      expect(acknowledgeUpdateAttemptToast).toHaveBeenCalledWith(3);
    });

    it("raises nothing where none is owed", async () => {
      vi.mocked(getUpdateAttemptToast).mockResolvedValue(null);

      await toastOwedAttempt();

      expect(toaster.toast).not.toHaveBeenCalled();
      expect(acknowledgeUpdateAttemptToast).not.toHaveBeenCalled();
    });

    it("is raised once for the load's read and a failed frame's read of the same attempt", async () => {
      vi.mocked(getUpdateAttemptToast).mockResolvedValue(failedWith("download_failed", 1));

      await Promise.all([toastOwedAttempt(), toastOwedAttempt()]);

      expect(toaster.toast).toHaveBeenCalledOnce();
      expect(acknowledgeUpdateAttemptToast).toHaveBeenCalledOnce();
    });

    it("is raised again for the next attempt that fails", async () => {
      vi.mocked(getUpdateAttemptToast).mockResolvedValueOnce(failedWith("download_failed", 1));
      vi.mocked(getUpdateAttemptToast).mockResolvedValueOnce(failedWith("download_failed", 2));

      await toastOwedAttempt();
      await toastOwedAttempt();

      expect(toaster.toast).toHaveBeenCalledTimes(2);
    });
  });

  describe("a toast owed once", () => {
    it("is raised, then acknowledged", async () => {
      const order: string[] = [];
      vi.mocked(toaster.toast).mockImplementationOnce(() => {
        order.push("raised");
        return { data: { title: "", body: "" }, dismiss: () => {} };
      });
      const acknowledge = vi.fn(async () => {
        order.push("acknowledged");
      });

      await raiseFailureToastOnce("record 1", "body", acknowledge);

      expect(order).toEqual(["raised", "acknowledged"]);
    });

    it("is raised once for a push and a read of the same failure", async () => {
      const acknowledge = vi.fn(async () => undefined);

      await Promise.all([
        raiseFailureToastOnce("record 1", "body", acknowledge),
        raiseFailureToastOnce("record 1", "body", acknowledge),
      ]);

      expect(toaster.toast).toHaveBeenCalledOnce();
      expect(acknowledge).toHaveBeenCalledOnce();
    });

    it("logs an acknowledgement the backend refused, and leaves it at that", async () => {
      await raiseFailureToastOnce("record 1", "body", async () => ({
        success: false,
        reason: "invalid_value",
        message: "Invalid record",
      }));

      expect(logWarn).toHaveBeenCalledWith("The toast for record 1 was not acknowledged: invalid_value");
    });

    it("says a toast raised but not acknowledged was raised, and answers rather than rejecting", async () => {
      await expect(
        raiseFailureToastOnce("record 1", "body", () => Promise.reject(new Error("socket closed"))),
      ).resolves.toBeUndefined();

      expect(toaster.toast).toHaveBeenCalledOnce();
      expect(logError).toHaveBeenCalledWith(
        "The toast for record 1 was raised, but not acknowledged: Error: socket closed",
      );
    });

    it("is neither raised nor acknowledged where it is no longer wanted once Steam can show it, and can be asked for again", async () => {
      const acknowledge = vi.fn(async () => undefined);

      await raiseUpdateToastOnce("available 1.1.0", "body", "the release's toast", acknowledge, () => false);
      expect(toaster.toast).not.toHaveBeenCalled();
      expect(acknowledge).not.toHaveBeenCalled();

      await raiseUpdateToastOnce("available 1.1.0", "body", "the release's toast", acknowledge, () => true);
      expect(toaster.toast).toHaveBeenCalledOnce();
      expect(acknowledge).toHaveBeenCalledOnce();
    });

    it("is raised again for a different failure", async () => {
      await raiseFailureToastOnce("record 1", "first");
      await raiseFailureToastOnce("record 2", "second");

      expect(vi.mocked(toaster.toast).mock.calls.map(([toast]) => toast.body)).toEqual(["first", "second"]);
    });

    it("waits until Steam can show it", async () => {
      let services = false;
      vi.stubGlobal("App", { GetServicesInitialized: () => services });
      vi.useFakeTimers();
      try {
        const raising = raiseFailureToastOnce("record 1", "body");
        await vi.advanceTimersByTimeAsync(1000);
        expect(toaster.toast).not.toHaveBeenCalled();

        services = true;
        await vi.advanceTimersByTimeAsync(1000);
        await raising;
      } finally {
        vi.useRealTimers();
      }

      expect(toaster.toast).toHaveBeenCalledOnce();
    });
  });
});
