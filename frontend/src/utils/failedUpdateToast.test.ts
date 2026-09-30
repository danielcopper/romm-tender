import { describe, it, expect, beforeEach, vi } from "vitest";
import { toaster } from "../api/host";
import type { UpdateInstallAttempt, UpdateInstallFailure } from "../api/backend";
import { PLUGIN_NAME } from "./toast";
import {
  attemptFailureToast,
  raiseFailureToastOnce,
  resetFailedUpdateToastsForTests,
  stillOnToast,
  toastFailedAttempt,
} from "./failedUpdateToast";

const FAILED: UpdateInstallAttempt = {
  version: "1.0.52",
  step: "failed",
  bytes_done: 0,
  bytes_total: null,
  failure: "download_failed",
};

const failedWith = (failure: UpdateInstallFailure): UpdateInstallAttempt => ({ ...FAILED, failure });

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
      ["game_started", "Update to 1.0.52 was cancelled. A game was started. Nothing was changed."],
      [
        "running_apps_unknown",
        "Update to 1.0.52 was cancelled. Could not check whether a game is running. Nothing was changed.",
      ],
    ])("words an attempt that failed with %s as %s", (failure, body) => {
      expect(attemptFailureToast(failedWith(failure))).toBe(body);
    });

    it("leaves a refusal by the pre-install check to its record's toast", () => {
      expect(attemptFailureToast(failedWith("new_version_does_not_start"))).toBeNull();
    });
  });

  describe("an attempt's frame", () => {
    it("raises the toast under Tender's name once the frame turns failed", async () => {
      await toastFailedAttempt(failedWith("game_started"));

      expect(toaster.toast).toHaveBeenCalledOnce();
      expect(toaster.toast).toHaveBeenCalledWith({
        title: PLUGIN_NAME,
        body: "Update to 1.0.52 was cancelled. A game was started. Nothing was changed.",
      });
    });

    it.each<UpdateInstallAttempt["step"]>(["downloading", "verifying", "installer_started"])(
      "raises nothing for a %s frame",
      async (step) => {
        await toastFailedAttempt({ ...FAILED, step, failure: null });

        expect(toaster.toast).not.toHaveBeenCalled();
      },
    );

    it("raises nothing for a refusal by the pre-install check, whose record raises it", async () => {
      await toastFailedAttempt(failedWith("new_version_does_not_start"));

      expect(toaster.toast).not.toHaveBeenCalled();
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
