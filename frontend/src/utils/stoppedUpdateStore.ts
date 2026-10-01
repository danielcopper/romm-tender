/**
 * Module-level store for an update attempt whose installer stopped without
 * updating, as the backend found it at start.
 *
 * Updated by:
 *   - panel load in index.tsx (fetchStoppedUpdateAttempt), detached, which
 *     raises the attempt's toast where the backend still owes it
 *   - the `update_attempt_stopped` listener in index.tsx
 *     (takePushedStoppedAttempt), for a judgement the backend could make only
 *     once the installer's unit had ended, after the panel had loaded — which
 *     raises the toast the same way
 *   - the card's Dismiss (dismissStoppedUpdateCard), after the backend removed
 *     its record
 *   - a press of Install the backend accepted (endStoppedAttempt,
 *     bigpicture/settings/useUpdateInstall.ts): the new attempt ended the
 *     record, and the card goes with it
 *
 * Read by:
 *   - bigpicture/UpdateStoppedNotice.tsx, the card on Main
 *   - utils/updateAvailableView.ts, whose "is available" card gives way to
 *     that card for the version it tried — and with it the dot on Tender's
 *     Quick Access glyph and the "is available" toast
 *
 * Settings › Updates states it through the install's own read — the failed
 * attempt and Try again — so it is not read there.
 *
 * Subscribers are `updateOutcomeStore.ts`'s own, since both hold what the last
 * update did. Every write installs a NEW value and notifies, which is what lets
 * {@link getStoppedUpdateAttempt} serve as a `useSyncExternalStore` snapshot.
 */

import { useSyncExternalStore } from "react";
import {
  acknowledgeStoppedUpdateAttemptToast,
  dismissStoppedUpdateAttempt,
  getStoppedUpdateAttempt as readStoppedUpdateAttempt,
  type StoppedUpdateAttemptWire,
} from "../api/backend";
import { logToastFailure, raiseFailureToastOnce, stillOnToast } from "./failedUpdateToast";
import { notifyUpdateOutcome, onUpdateOutcomeChange } from "./updateOutcomeStore";

/** An attempt whose installer stopped without updating, in this store's spelling. */
export interface StoppedUpdateAttempt {
  attemptedVersion: string;
  fromVersion: string;
}

let _attempt: StoppedUpdateAttempt | null = null;

/**
 * Ordering fence for the read, as in `updateOutcomeStore.ts`: Dismiss, a push
 * and an accepted press move it, so a read in flight before any of them cannot
 * put back what it replaced.
 */
let _seq = 0;

function set(attempt: StoppedUpdateAttempt | null): void {
  _attempt = attempt;
  notifyUpdateOutcome();
}

/** Test seam: drop the answer, as a fresh panel load has it. */
export function resetStoppedUpdateStoreForTests(): void {
  _seq = 0;
  set(null);
}

export function getStoppedUpdateAttempt(): StoppedUpdateAttempt | null {
  return _attempt;
}

/** Subscribe to the stopped attempt from a component. */
export function useStoppedUpdateAttempt(): StoppedUpdateAttempt | null {
  return useSyncExternalStore(onUpdateOutcomeChange, getStoppedUpdateAttempt);
}

function fromWire(wire: StoppedUpdateAttemptWire): StoppedUpdateAttempt {
  return { attemptedVersion: wire.attempted_version, fromVersion: wire.from_version };
}

/** Raise the attempt's toast once where the backend still owes it, and tell the backend it was raised. */
async function toastIfOwed(wire: StoppedUpdateAttemptWire): Promise<void> {
  if (wire.toast_owed)
    await raiseFailureToastOnce(
      `stopped ${wire.started_at}`,
      stillOnToast(wire.attempted_version, wire.from_version),
      () => acknowledgeStoppedUpdateAttemptToast(wire.started_at),
    );
}

/** Ask the backend whether an earlier start's installer stopped without updating, fill the store, and raise its toast. */
export async function fetchStoppedUpdateAttempt(): Promise<void> {
  const seq = ++_seq;
  const answer = await readStoppedUpdateAttempt();
  if (seq !== _seq) return;
  set(answer === null ? null : fromWire(answer));
  if (answer !== null) await toastIfOwed(answer);
}

/** Take the stopped attempt the backend pushed; it outranks a read still in flight. Raises its toast where owed. */
export function takePushedStoppedAttempt(pushed: StoppedUpdateAttemptWire): void {
  ++_seq;
  set(fromWire(pushed));
  toastIfOwed(pushed).catch(logToastFailure);
}

/** A new attempt was accepted: the backend ended the stopped one's record, so the card comes down here too. */
export function endStoppedAttempt(): void {
  ++_seq;
  if (_attempt !== null) set(null);
}

/** Wave the card away, then take it down here — only once the backend answered. A failed call rejects. */
export async function dismissStoppedUpdateCard(): Promise<void> {
  ++_seq;
  await dismissStoppedUpdateAttempt();
  set(null);
}

/** Whether the stopped attempt's card takes the place of the "is available" card for *latestVersion*. */
export function stoppedAttemptTakesThePlaceOf(
  latestVersion: string | null,
  attempt: StoppedUpdateAttempt | null,
): boolean {
  return attempt !== null && attempt.attemptedVersion === latestVersion;
}
