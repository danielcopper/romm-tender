/**
 * The toast for an update that failed: its words, and raising it once — and
 * raising any update toast once, which the "is available" toast
 * (`updateAvailableToast.ts`) does through here too.
 *
 * A failed update's toast is raised by:
 *   - updateOutcomeStore.ts, for the installer's record — at panel load where
 *     the backend still owes it, and for a refusal by the pre-install check
 *     the backend pushed
 *   - stoppedUpdateStore.ts, for an installer an earlier start found stopped,
 *     where the backend still owes it
 *   - index.tsx (toastOwedAttempt), for an attempt of this backend's that
 *     failed, where the backend still owes it — asked at panel load and when a
 *     pushed frame turns failed
 *
 * Once means once: the backend keeps every acknowledgement — across starts for
 * a record and a stopped attempt, for this process for an attempt. Which
 * failure owes a toast, and why the check's refusal is raised off its record
 * rather than off its attempt, is `docs/architecture/qam-panel.md`, "Notices
 * and homes".
 */

import {
  acknowledgeUpdateAttemptToast,
  getUpdateAttemptToast,
  logError,
  logWarn,
  type UpdateAttemptToast,
  type UpdateSettingWrite,
} from "../api/backend";
import { TOAST_READINESS_DEADLINE_MS, waitUntilSteamCanShowToasts } from "./steamReadyForToasts";
import { showToast } from "./toast";
import { INSTALL_FAILURE_REASONS } from "./updateInstallView";

/** What a toast raised in this JavaScript context was for, so a push and a read of the same update raise one. */
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
 * *acknowledge* it to the backend; *what* names it as {@link
 * toastWhenSteamIsReady} takes it. An acknowledgement that fails or is refused
 * leaves the toast owed, and the next panel load raises it again — a repeat
 * rather than a loss; a refusal is logged.
 */
export async function raiseUpdateToastOnce(
  key: string,
  body: string,
  what: string,
  acknowledge?: () => Promise<UpdateSettingWrite | void>,
): Promise<void> {
  if (raised.has(key)) return;
  raised.add(key);
  await toastWhenSteamIsReady(body, what);
  const answer = await acknowledge?.();
  if (answer?.success === false) logWarn(`The toast for ${key} was not acknowledged: ${answer.reason}`);
}

/** {@link raiseUpdateToastOnce} for the toast of an update that failed. */
export function raiseFailureToastOnce(
  key: string,
  body: string,
  acknowledge?: () => Promise<UpdateSettingWrite | void>,
): Promise<void> {
  return raiseUpdateToastOnce(key, body, "the failed update's toast", acknowledge);
}

/** The toast for an update that did not go through and left *stillOn* in place: a record, or a stopped installer. */
export function stillOnToast(attempted: string, stillOn: string): string {
  return `Update to ${attempted} failed. You are still on ${stillOn}. Settings › Updates shows why.`;
}

/**
 * The toast for an attempt of this backend's that failed. A failure a later
 * backend reports and this panel has no line for is named by its title alone.
 */
export function attemptFailureToast({ failure, version }: UpdateAttemptToast): string {
  if (failure === "game_started" || failure === "running_apps_unknown") {
    const reason = failure === "game_started" ? "A game was started." : INSTALL_FAILURE_REASONS[failure];
    return `Update to ${version} was cancelled. ${reason} Nothing was changed.`;
  }
  return `Update to ${version} failed. ${(INSTALL_FAILURE_REASONS as Partial<Record<string, string>>)[failure] ?? ""}`.trimEnd();
}

/**
 * Raise the toast for this backend's failed attempt where the backend still
 * owes it, once, and acknowledge it. Asked at panel load and when a pushed
 * frame turns failed: a frame sent while no panel was loaded took its toast
 * with it.
 */
export async function toastOwedAttempt(): Promise<void> {
  const owed = await getUpdateAttemptToast();
  if (owed)
    await raiseFailureToastOnce(`attempt ${owed.attempt}`, attemptFailureToast(owed), () =>
      acknowledgeUpdateAttemptToast(owed.attempt),
    );
}

/** What a caller that does not wait for a failed update's toast catches it with. */
export const logToastFailure = (e: unknown) => logError(`Failed to raise the failed update's toast: ${e}`);
