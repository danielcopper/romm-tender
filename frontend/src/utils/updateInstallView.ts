/**
 * The words and the step list Settings › Updates shows for an install.
 *
 * The backend answers in discriminants (`UpdateWaitReason`,
 * `UpdateInstallStep`, `UpdateInstallFailure`); every sentence a reader sees
 * for one is here.
 */

import type { UpdateInstallAttempt, UpdateInstallFailure, UpdateWaitReason } from "../api/backend";

export const WAITING_FOR = "Waiting for:";

/** What stands after the installer started: this backend is on its way out, and nothing more is reported. */
export const RESTARTING_LINE = "Tender is restarting — Steam's interface will reload in a moment.";

/** A press the connection failed to carry; the log names the error. */
export const INSTALL_REQUEST_FAILED = "The install could not be requested.";

export const INSTALL_FAILURE_SENTENCES: Record<UpdateInstallFailure, string> = {
  download_failed: "The download failed — nothing was changed.",
  checksum_mismatch: "The download did not match its checksum — nothing was changed.",
  installer_not_started: "The installer could not be started.",
  installer_stopped: "The installer stopped without updating. Details: journalctl --user -u romm-tender-update",
};

type PlainWaitReason = Exclude<UpdateWaitReason, { apps: string[] } | { frees_at: number }>["reason"];

const PLAIN_WAIT_LINES: Record<PlainWaitReason, string> = {
  running_apps_unknown: "Could not check whether a game is running",
  library_sync: "Library sync",
  rom_downloads: "Game downloads",
  save_sync: "Save sync",
  firmware_downloads: "BIOS downloads",
  save_directory_move: "A save directory move",
  removed_games_cleanup: "A removed-game cleanup",
  retrodeck_migration: "A RetroDECK migration",
  other_work: "Other Tender work",
};

/** Local wall-clock `HH:MM` of an epoch-seconds instant. */
function clockTime(epochSeconds: number): string {
  const at = new Date(epochSeconds * 1000);
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}

export function waitReasonLine(wait: UpdateWaitReason): string {
  switch (wait.reason) {
    case "app_running":
      return `A game to close (${wait.apps.join(", ")})`;
    case "interface_reload_limit":
      return `Steam's interface was just reloaded twice — possible again at ${clockTime(wait.frees_at)}`;
    default:
      return PLAIN_WAIT_LINES[wait.reason];
  }
}

/** `""` where nothing is paused; a paused download never holds the button back. */
export function pausedDownloadsHint(count: number): string {
  if (count <= 0) return "";
  return count === 1 ? "1 paused download will be cancelled." : `${count} paused downloads will be cancelled.`;
}

export type InstallStepId = "download" | "verify" | "installer";
export type InstallStepStatus = "pending" | "current" | "done" | "failed";

export interface InstallStepRow {
  id: InstallStepId;
  label: string;
  status: InstallStepStatus;
}

const STEPS: readonly { id: InstallStepId; label: string }[] = [
  { id: "download", label: "Downloading" },
  { id: "verify", label: "Verifying" },
  { id: "installer", label: "Starting the installer" },
];

const FAILED_AT: Record<UpdateInstallFailure, InstallStepId> = {
  download_failed: "download",
  checksum_mismatch: "verify",
  installer_not_started: "installer",
  installer_stopped: "installer",
};

/**
 * Where each step stands. The backend's `verifying` covers everything up to the
 * installer's start, so "Starting the installer" is never the current step: it
 * is pending until `installer_started` and done from then on.
 */
export function installStepRows(attempt: UpdateInstallAttempt): InstallStepRow[] {
  const index = (id: InstallStepId) => STEPS.findIndex((step) => step.id === id);
  const statusOf = (at: number): InstallStepStatus => {
    switch (attempt.step) {
      case "downloading":
        return at === 0 ? "current" : "pending";
      case "verifying":
        if (at === 0) return "done";
        return at === 1 ? "current" : "pending";
      case "installer_started":
        return "done";
      case "failed": {
        const failedAt = index(attempt.failure === null ? "download" : FAILED_AT[attempt.failure]);
        if (at < failedAt) return "done";
        return at === failedAt ? "failed" : "pending";
      }
    }
  };
  return STEPS.map((step, at) => ({ ...step, status: statusOf(at) }));
}

/** The download's whole percent, or `null` where it announced no size. */
export function downloadPercent(attempt: UpdateInstallAttempt): number | null {
  if (attempt.bytes_total === null || attempt.bytes_total <= 0) return null;
  return Math.min(100, Math.floor((attempt.bytes_done / attempt.bytes_total) * 100));
}

const STEP_RANK: Record<UpdateInstallAttempt["step"], number> = {
  downloading: 0,
  verifying: 1,
  installer_started: 2,
  failed: 3,
};

/**
 * The attempt to show, from the pushed frame and the last read.
 *
 * Both describe the same attempt — the page clears the pushed one and fences
 * off older reads at every press — so whichever got further is the newer: a
 * read can land after a frame it was issued before. A read naming another
 * version is the backend's own word on which attempt is the latest.
 */
export function furtherAttempt(
  pushed: UpdateInstallAttempt | null,
  read: UpdateInstallAttempt | null,
): UpdateInstallAttempt | null {
  if (pushed === null) return read;
  if (read === null) return pushed;
  if (pushed.version !== read.version) return read;
  const byStep = STEP_RANK[pushed.step] - STEP_RANK[read.step];
  if (byStep !== 0) return byStep > 0 ? pushed : read;
  return pushed.bytes_done >= read.bytes_done ? pushed : read;
}
