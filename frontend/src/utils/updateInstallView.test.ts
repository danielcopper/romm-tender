import { describe, it, expect } from "vitest";
import type { UpdateInstallAttempt, UpdateInstallFailure, UpdateWaitReason } from "../api/backend";
import {
  INSTALL_FAILURE_SENTENCES,
  downloadPercent,
  furtherAttempt,
  installStepRows,
  pausedDownloadsHint,
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

const statuses = (attempt: UpdateInstallAttempt) => installStepRows(attempt).map((row) => [row.id, row.status]);

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
  ])("words %j", (wait, line) => {
    expect(waitReasonLine(wait)).toBe(line);
  });

  it("names the local time the reload limit frees up, as HH:MM", () => {
    const freesAt = new Date(2026, 8, 29, 9, 5, 42).getTime() / 1000;
    expect(waitReasonLine({ reason: "interface_reload_limit", frees_at: freesAt })).toBe(
      "Steam's interface was just reloaded twice — possible again at 09:05",
    );
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

describe("installStepRows", () => {
  it("labels the three steps in order", () => {
    expect(installStepRows(DOWNLOADING).map((row) => row.label)).toEqual([
      "Downloading",
      "Verifying",
      "Starting the installer",
    ]);
  });

  it("has the download under way and the rest ahead while it downloads", () => {
    expect(statuses(DOWNLOADING)).toEqual([
      ["download", "current"],
      ["verify", "pending"],
      ["installer", "pending"],
    ]);
  });

  it("has the verification under way once the download is done", () => {
    expect(statuses({ ...DOWNLOADING, step: "verifying" })).toEqual([
      ["download", "done"],
      ["verify", "current"],
      ["installer", "pending"],
    ]);
  });

  it("has every step done once the installer started", () => {
    expect(statuses({ ...DOWNLOADING, step: "installer_started" })).toEqual([
      ["download", "done"],
      ["verify", "done"],
      ["installer", "done"],
    ]);
  });

  it.each<[UpdateInstallFailure, string[][]]>([
    [
      "download_failed",
      [
        ["download", "failed"],
        ["verify", "pending"],
        ["installer", "pending"],
      ],
    ],
    [
      "checksum_mismatch",
      [
        ["download", "done"],
        ["verify", "failed"],
        ["installer", "pending"],
      ],
    ],
    [
      "installer_not_started",
      [
        ["download", "done"],
        ["verify", "done"],
        ["installer", "failed"],
      ],
    ],
    [
      "installer_stopped",
      [
        ["download", "done"],
        ["verify", "done"],
        ["installer", "failed"],
      ],
    ],
    [
      "game_started",
      [
        ["download", "done"],
        ["verify", "done"],
        ["installer", "failed"],
      ],
    ],
    [
      "running_apps_unknown",
      [
        ["download", "done"],
        ["verify", "done"],
        ["installer", "failed"],
      ],
    ],
  ])("marks the step %s failed at", (failure, expected) => {
    expect(statuses(failed(failure))).toEqual(expected);
  });
});

describe("INSTALL_FAILURE_SENTENCES", () => {
  it("words every failure, and points an installer that stopped at its journal", () => {
    expect(INSTALL_FAILURE_SENTENCES).toEqual({
      download_failed: "The download failed — nothing was changed.",
      checksum_mismatch: "The download did not match its checksum — nothing was changed.",
      installer_not_started: "The installer could not be started.",
      installer_stopped: "The installer stopped without updating. Details: journalctl --user -u romm-tender-update",
      game_started: "A game was started — nothing was changed. Try again once it has closed.",
      running_apps_unknown: "Could not check whether a game is running — nothing was changed.",
    });
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
