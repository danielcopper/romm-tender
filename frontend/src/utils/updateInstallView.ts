/**
 * The words and the step list Settings › Updates shows for an install.
 *
 * The backend answers in discriminants (`UpdateWaitReason`,
 * `UpdateInstallStep`, `UpdateInstallFailure`, a refusal's `reason`); the
 * sentence a reader sees for each is here. The button's labels, the captions
 * and the failure titles sit with the block that shows them, in
 * `UpdateInstallRows.tsx`.
 */

import type { UpdateInstallAttempt, UpdateInstallFailure, UpdateWaitReason } from "../api/backend";

export const WAITING_FOR = "Waiting for:";

/**
 * What stands under the steps once this backend has gone: nothing more is
 * reported until Steam's interface reloads. Which of the installer's waits the
 * five minutes cover is `docs/architecture/qam-panel.md`, "The install under
 * Updates".
 */
export function restartWaitLine(installed: string): string {
  return `Steam's interface reloads when it is done — usually within a minute, and up to about 5 minutes if Tender has to go back to ${installed}.`;
}

/** A press the connection failed to carry; the log names the error. */
export const INSTALL_REQUEST_FAILED = "The install could not be requested.";

/** While the release downloads or is checked: the backend asks Steam once more before the installer starts. */
export const GAME_STARTS_CANCEL = "Starting a game now cancels the update.";

/** A read of the install's state that did not answer, while no installer is running. */
export const INSTALL_STATE_UNREAD = "Could not read the update state.";

const INSTALLER_UNIT_JOURNAL = "journalctl --user -u romm-tender-update";

/** Where the installer's own account of an attempt is, as every line about it names it. */
const INSTALLER_JOURNAL = `Details: ${INSTALLER_UNIT_JOURNAL}`;

/** Five minutes after the installer started, with the backend gone: it has not come back up. */
export const NOT_BACK_LINE = `Tender has not come back. ${INSTALLER_JOURNAL} — start it again with: systemctl --user start romm-tender`;

/** Five minutes after the installer started, with the backend still answering: the installer has not stopped it yet. */
export const TAKING_LONG_LINE = `The installer is taking unusually long. ${INSTALLER_JOURNAL}`;

/** How long after the panel first sees the installer started the line under the steps gives way to one of the two above. */
export const INSTALLER_OVERDUE_MS = 5 * 60 * 1000;

/** A press the backend refused for something other than a wait, and a press the connection did not carry. */
export type InstallRefusalReason = "update_in_progress" | "not_offered" | "version_changed" | "request_failed";

export const INSTALL_REFUSAL_SENTENCES: Record<InstallRefusalReason, string> = {
  update_in_progress: "An update is already being installed.",
  not_offered: "There is no newer release to install.",
  version_changed: "The release on offer has changed — press again to install it.",
  request_failed: INSTALL_REQUEST_FAILED,
};

/** What refused a press, and the version that press named. */
export interface InstallRefusal {
  reason: InstallRefusalReason;
  version: string;
}

/**
 * Whether a refusal still says something true once a later read has answered.
 *
 * A press the connection did not carry is over once a read answers again. A
 * release no longer offered, or another one offered, is over once a read
 * names a different version than the press did — the button then says what is
 * offered. An update already under way is over once no attempt is.
 */
export function refusalStands(
  refusal: InstallRefusal,
  read: { version: string | null; attempt: UpdateInstallAttempt | null },
): boolean {
  switch (refusal.reason) {
    case "request_failed":
      return false;
    case "update_in_progress":
      return read.attempt !== null && read.attempt.step !== "failed";
    case "not_offered":
    case "version_changed":
      return read.version === refusal.version;
  }
}

/**
 * How many takedowns Steam's interface reload limit allows in its window —
 * `RELOAD_LIMIT` in `backend/host/inject/reload_limit.py`, which
 * `updateInstallView.test.ts` holds this equal to.
 */
export const RELOAD_LIMIT: number = 2;

/** The line under a failed attempt's title, which says for every kind but `installer_stopped` that nothing was changed. */
export const INSTALL_FAILURE_SENTENCES: Record<UpdateInstallFailure, string> = {
  download_failed: "The download failed.",
  checksum_mismatch: "The download did not match its checksum.",
  installer_not_started: "The installer could not be started.",
  installer_stopped: `The installer stopped without updating. ${INSTALLER_JOURNAL}`,
  game_started: "A game was started. Try again once it has closed.",
  running_apps_unknown: "Could not check whether a game is running.",
  new_version_does_not_start: `The new version does not start. The installer's output says why: ${INSTALLER_UNIT_JOURNAL}`,
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
  interface_reload_limit_unknown: "Could not check when Steam's interface may be reloaded",
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
    case "interface_reload_limit": {
      const times = RELOAD_LIMIT === 2 ? "twice" : `${RELOAD_LIMIT} times`;
      return `Steam's interface was just reloaded ${times} — possible again at ${clockTime(wait.frees_at)}`;
    }
    default:
      return PLAIN_WAIT_LINES[wait.reason];
  }
}

/** `""` where nothing is paused; a paused download never holds the button back. */
export function pausedDownloadsHint(count: number): string {
  if (count <= 0) return "";
  return count === 1 ? "1 paused download will be cancelled." : `${count} paused downloads will be cancelled.`;
}

export type InstallStepId = "download" | "verify" | "check" | "install";
export type InstallStepStatus = "pending" | "current" | "done" | "failed";

export interface InstallStepRow {
  id: InstallStepId;
  label: string;
  status: InstallStepStatus;
}

const STEPS: readonly [InstallStepId, string][] = [
  ["download", "Download"],
  ["verify", "Verify"],
  ["check", "Check new version"],
  ["install", "Install"],
];

/**
 * The step a failure is marked at. Everything between the checksum and the
 * end of the installer's pre-install check is marked at the check, so no step
 * the attempt never reached reads as done. An installer that stopped is marked
 * there only where this panel saw it start: one a backend found at its own
 * start had stopped the backend before it, which is Install.
 */
export function failedStep(failure: UpdateInstallFailure, installerSeen: boolean): InstallStepId {
  if (failure === "download_failed") return "download";
  if (failure === "checksum_mismatch") return "verify";
  return failure === "installer_stopped" && !installerSeen ? "install" : "check";
}

/** The four steps with every one before *at* done, *at* current or failed, and the rest still to do. */
export function installSteps(at: InstallStepId, failed: boolean): InstallStepRow[] {
  const index = STEPS.findIndex(([id]) => id === at);
  return STEPS.map(([id, label], i) => ({
    id,
    label,
    status: i < index ? "done" : i > index ? "pending" : failed ? "failed" : "current",
  }));
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
