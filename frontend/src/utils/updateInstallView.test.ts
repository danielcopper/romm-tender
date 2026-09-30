import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, it, expect } from "vitest";
import type { UpdateInstallAttempt, UpdateInstallFailure, UpdateWaitReason } from "../api/backend";
import {
  INSTALL_FAILURE_NOTES,
  INSTALLER_STOPPED_SENTENCE,
  INSTALLER_OVERDUE_MS,
  RELOAD_LIMIT,
  refusalStands,
  downloadPercent,
  failedStep,
  furtherAttempt,
  installSteps,
  pausedDownloadsHint,
  restartWaitLine,
  waitReasonLine,
} from "./updateInstallView";

const DOWNLOADING: UpdateInstallAttempt = {
  version: "1.0.0",
  step: "downloading",
  bytes_done: 50,
  bytes_total: 200,
  failure: null,
};

const failed = (failure: UpdateInstallFailure): UpdateInstallAttempt => ({ ...DOWNLOADING, step: "failed", failure });

describe("waitReasonLine", () => {
  it.each<[UpdateWaitReason, string]>([
    [{ reason: "app_running", apps: ["Celeste", "Hades"] }, "A game to close (Celeste, Hades)"],
    [{ reason: "running_apps_unknown" }, "Could not check whether a game is running"],
    [{ reason: "library_sync" }, "Library sync"],
    [{ reason: "rom_downloads" }, "Game downloads"],
    [{ reason: "save_sync" }, "Save sync"],
    [{ reason: "firmware_downloads" }, "BIOS downloads"],
    [{ reason: "save_directory_move" }, "A save directory move"],
    [{ reason: "removed_games_cleanup" }, "A removed-game cleanup"],
    [{ reason: "retrodeck_migration" }, "A RetroDECK migration"],
    [{ reason: "other_work" }, "Other Tender work"],
    [{ reason: "interface_reload_limit_unknown" }, "Could not check when Steam's interface may be reloaded"],
  ])("words %j", (wait, line) => {
    expect(waitReasonLine(wait)).toBe(line);
  });

  describe("in a time zone away from UTC", () => {
    const zone = process.env.TZ;

    beforeEach(() => {
      process.env.TZ = "Asia/Kolkata";
    });

    afterEach(() => {
      process.env.TZ = zone;
    });

    it("names the local time the reload limit frees up, as HH:MM", () => {
      // 03:35:42 UTC is 09:05 in Kolkata, five and a half hours ahead: a
      // rendering in UTC would say 03:35.
      const freesAt = Date.UTC(2026, 8, 29, 3, 35, 42) / 1000;
      expect(waitReasonLine({ reason: "interface_reload_limit", frees_at: freesAt })).toBe(
        "Steam's interface was just reloaded twice — possible again at 09:05",
      );
    });
  });

  it("counts the reloads the backend's limit allows", () => {
    const source = readFileSync(`${process.cwd()}/../backend/host/inject/reload_limit.py`, "utf8");
    expect(source.match(/^RELOAD_LIMIT = (\d+)$/m)?.[1]).toBe(String(RELOAD_LIMIT));
  });
});

describe("refusalStands", () => {
  const read = (version: string | null, attempt: UpdateInstallAttempt | null = null) => ({ version, attempt });

  it("lets a press the connection did not carry go at the next read that answers", () => {
    expect(refusalStands({ reason: "request_failed", version: "1.0.0" }, read("1.0.0"))).toBe(false);
  });

  it("holds a changed or withdrawn release while the read still names the pressed version", () => {
    for (const reason of ["version_changed", "not_offered"] as const) {
      expect(refusalStands({ reason, version: "1.0.0" }, read("1.0.0"))).toBe(true);
      expect(refusalStands({ reason, version: "1.0.0" }, read("1.0.1"))).toBe(false);
      expect(refusalStands({ reason, version: "1.0.0" }, read(null))).toBe(false);
    }
  });

  it("holds an update already under way while an attempt is", () => {
    const refusal = { reason: "update_in_progress" as const, version: "1.0.0" };
    expect(refusalStands(refusal, read("1.0.0", DOWNLOADING))).toBe(true);
    expect(refusalStands(refusal, read("1.0.0", failed("download_failed")))).toBe(false);
    expect(refusalStands(refusal, read("1.0.0"))).toBe(false);
  });
});

describe("pausedDownloadsHint", () => {
  it("says nothing where nothing is paused", () => {
    expect(pausedDownloadsHint(0)).toBe("");
  });

  it("counts one paused download in the singular", () => {
    expect(pausedDownloadsHint(1)).toBe("1 paused download will be cancelled.");
  });

  it("counts several in the plural", () => {
    expect(pausedDownloadsHint(3)).toBe("3 paused downloads will be cancelled.");
  });
});

describe("installSteps", () => {
  const statuses = (...args: Parameters<typeof installSteps>) =>
    installSteps(...args).map((row) => `${row.id}:${row.status}`);

  it("labels the four steps in order", () => {
    expect(installSteps("download", false).map((row) => row.label)).toEqual([
      "Download",
      "Verify",
      "Check the new version",
      "Install",
    ]);
  });

  it("has every step before the one under way done and every one after it still to do", () => {
    expect(statuses("check", false)).toEqual(["download:done", "verify:done", "check:current", "install:pending"]);
  });

  it("marks the step a failure stopped at failed instead", () => {
    expect(statuses("verify", true)).toEqual(["download:done", "verify:failed", "check:pending", "install:pending"]);
  });

  it("can have the first step under way and the last one failed", () => {
    expect(statuses("download", false)[0]).toBe("download:current");
    expect(statuses("install", true)).toEqual(["download:done", "verify:done", "check:done", "install:failed"]);
  });
});

describe("failedStep", () => {
  it.each<[UpdateInstallFailure, string | null]>([
    ["download_failed", "download"],
    ["checksum_mismatch", "verify"],
    ["installer_not_started", null],
    ["game_started", null],
    ["running_apps_unknown", null],
    ["new_version_does_not_start", "check"],
    ["installer_stopped", "check"],
  ])("marks %s at %s (null: no step line) where this panel saw the installer start", (failure, step) => {
    expect(failedStep(failure, true)).toBe(step);
  });

  it("marks an installer that stopped at Install where a backend found it at its start", () => {
    expect(failedStep("installer_stopped", false)).toBe("install");
  });

  it("marks every other failure at the same step either way", () => {
    expect(failedStep("new_version_does_not_start", false)).toBe("check");
    expect(failedStep("download_failed", false)).toBe("download");
    expect(failedStep("game_started", false)).toBeNull();
  });
});

describe("INSTALLER_OVERDUE_MS", () => {
  it("gives the installer seven minutes before the line under the steps gives way", () => {
    expect(INSTALLER_OVERDUE_MS).toBe(7 * 60 * 1000);
  });
});

describe("restartWaitLine", () => {
  it("names the version Tender would go back to", () => {
    expect(restartWaitLine("1.0.20")).toBe(
      "Steam's interface reloads when it is done — usually within a minute, and up to about 5 minutes if Tender has to go back to 1.0.20.",
    );
  });
});

describe("INSTALL_FAILURE_NOTES and INSTALLER_STOPPED_SENTENCE", () => {
  it("words every failure without what its title says, and names no journal", () => {
    expect(INSTALL_FAILURE_NOTES).toEqual({
      download_failed: "The download failed.",
      checksum_mismatch: "The download did not match its checksum.",
      installer_not_started: "The installer could not be started.",
      installer_stopped: "The installer stopped without updating.",
      game_started: "A game was started. Try again once it has closed.",
      running_apps_unknown: "Could not check whether a game is running.",
      new_version_does_not_start: "The new version does not start.",
    });
  });

  it("points an installer that stopped at its journal where no button stands beside it", () => {
    expect(INSTALLER_STOPPED_SENTENCE).toBe(
      "The installer stopped without updating. Details: journalctl --user -u romm-tender-update",
    );
  });
});

describe("downloadPercent", () => {
  it("is the whole percent of the announced size", () => {
    expect(downloadPercent({ ...DOWNLOADING, bytes_done: 199, bytes_total: 200 })).toBe(99);
  });

  it("is null where no size was announced", () => {
    expect(downloadPercent({ ...DOWNLOADING, bytes_total: null })).toBeNull();
  });

  it("is null for an announced size of nothing", () => {
    expect(downloadPercent({ ...DOWNLOADING, bytes_done: 0, bytes_total: 0 })).toBeNull();
  });

  it("never passes a hundred when more arrived than was announced", () => {
    expect(downloadPercent({ ...DOWNLOADING, bytes_done: 300, bytes_total: 200 })).toBe(100);
  });
});

describe("furtherAttempt", () => {
  it("takes whichever of the two exists", () => {
    expect(furtherAttempt(null, DOWNLOADING)).toBe(DOWNLOADING);
    expect(furtherAttempt(DOWNLOADING, null)).toBe(DOWNLOADING);
    expect(furtherAttempt(null, null)).toBeNull();
  });

  it("takes the pushed frame where it got further than the read", () => {
    const verifying = { ...DOWNLOADING, step: "verifying" as const };
    expect(furtherAttempt(verifying, DOWNLOADING)).toBe(verifying);
  });

  it("takes the read where it got further than the pushed frame", () => {
    const stopped = failed("installer_stopped");
    expect(furtherAttempt({ ...DOWNLOADING, step: "installer_started" }, stopped)).toBe(stopped);
  });

  it("takes the one with more bytes while both are downloading", () => {
    const further = { ...DOWNLOADING, bytes_done: 120 };
    expect(furtherAttempt(DOWNLOADING, further)).toBe(further);
    expect(furtherAttempt(further, DOWNLOADING)).toBe(further);
  });

  it("takes the read where the two name different versions", () => {
    const other = { ...DOWNLOADING, version: "1.1.0" };
    expect(furtherAttempt(failed("download_failed"), other)).toBe(other);
  });
});
