/**
 * The toast for an update that failed: its words, and raising it once.
 *
 * Raised by:
 *   - updateOutcomeStore.ts, for the installer's record — at panel load where
 *     the backend still owes it, and for a refusal by the pre-install check
 *     the backend pushed
 *   - stoppedUpdateStore.ts, for an installer an earlier start found stopped,
 *     where the backend still owes it
 *   - the `update_install_progress` listener in index.tsx, for an attempt of
 *     this backend's whose frame turned failed — never from a read, so a
 *     reloaded panel does not raise it again
 *
 * Once means once across starts for a record and a stopped attempt: the
 * backend keeps the acknowledgement. Which failure owes a toast, and why the
 * check's refusal is raised off its record rather than off its frame, is
 * `docs/architecture/qam-panel.md`, "Notices and homes".
 */

import { logWarn, type UpdateInstallAttempt } from "../api/backend";
import { TOAST_READINESS_DEADLINE_MS, waitUntilSteamCanShowToasts } from "./steamReadyForToasts";
import { showToast } from "./toast";
import { INSTALL_FAILURE_NOTES } from "./updateInstallView";

/** What a toast raised in this JavaScript context was for, so a push and a read of the same failure raise one. */
const raised = new Set<string>();

/** Test seam: forget what was raised, as a fresh JavaScript context has it. */
export function resetFailedUpdateToastsForTests(): void {
  raised.clear();
}

/**
 * Raise *body* once Steam can show it (what that waits for, and for how long
 * at most: `steamReadyForToasts.ts`); *what* names it in the warning logged
 * where Steam was not ready in time.
 */
export async function toastWhenSteamIsReady(body: string, what: string): Promise<void> {
  const readiness = await waitUntilSteamCanShowToasts();
  if (!readiness.inTime) {
    logWarn(
      `Steam was not ready for a toast after ${TOAST_READINESS_DEADLINE_MS / 1000} s (still waiting for ${readiness.unmet.join(", ")}); raising ${what} anyway`,
    );
  }
  showToast(body);
}

/**
 * Raise the toast *key* names, unless this context raised it already, then
 * *acknowledge* it to the backend. An acknowledgement that fails leaves the
 * toast owed, and the next panel load raises it again — a repeat rather than
 * a loss.
 */
export async function raiseFailureToastOnce(
  key: string,
  body: string,
  acknowledge?: () => Promise<unknown>,
): Promise<void> {
  if (raised.has(key)) return;
  raised.add(key);
  await toastWhenSteamIsReady(body, "the failed update's toast");
  await acknowledge?.();
}

/** The toast for an update that did not go through and left *stillOn* in place: a record, or a stopped installer. */
export function stillOnToast(attempted: string, stillOn: string): string {
  return `Update to ${attempted} failed. You are still on ${stillOn}. Settings › Updates shows why.`;
}

/** The toast for an attempt of this backend's that failed, or `null` for one whose toast its record raises. */
export function attemptFailureToast({ failure, version }: UpdateInstallAttempt): string | null {
  if (failure === null || failure === "new_version_does_not_start") return null;
  if (failure === "game_started" || failure === "running_apps_unknown") {
    const reason = failure === "game_started" ? "A game was started." : INSTALL_FAILURE_NOTES[failure];
    return `Update to ${version} was cancelled. ${reason} Nothing was changed.`;
  }
  return `Update to ${version} failed. ${INSTALL_FAILURE_NOTES[failure]}`;
}

/** Raise the toast for a pushed frame that turned failed; every other frame raises nothing. */
export function toastFailedAttempt(frame: UpdateInstallAttempt): Promise<void> {
  const body = frame.step === "failed" ? attemptFailureToast(frame) : null;
  return body === null ? Promise.resolve() : toastWhenSteamIsReady(body, "the failed update's toast");
}
