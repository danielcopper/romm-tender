/**
 * Module-level store for what the last update did, as the backend found it.
 *
 * Updated by:
 *   - panel load in index.tsx (fetchUpdateOutcome), detached — which is also
 *     where the toast for a version that moved is raised, once, and the toast
 *     for an update that did not go through, where the backend still owes it
 *   - the `update_failure_recorded` listener in index.tsx
 *     (takePushedUpdateFailure), for a refusal by the pre-install check the
 *     backend saw while it ran, which raises that refusal's toast
 *   - the announcement card's Dismiss (dismissUpdateAnnouncementCard), after
 *     the backend recorded it
 *   - the rolled-back card's Dismiss (dismissUpdateFailureRecord), after the
 *     backend persisted it
 *
 * Read by:
 *   - bigpicture/UpdateAnnouncementNotice.tsx, the card on Main for a version
 *     that moved
 *   - bigpicture/UpdateFailureNotice.tsx, the rolled-back card on Main
 *   - bigpicture/UpdateNotice.tsx, which gives way to that card (see
 *     {@link failureTakesThePlaceOf})
 *   - bigpicture/settings/UpdatesSection.tsx through SettingsPage, which states
 *     the same fact whether or not the card was dismissed
 *
 * Every write installs a NEW state object and notifies, which is what lets
 * {@link getUpdateOutcomeState} serve as a `useSyncExternalStore` snapshot.
 */

import { useSyncExternalStore } from "react";
import {
  acknowledgeUpdateFailureToast,
  acknowledgeUpdateToast,
  dismissUpdateAnnouncement,
  dismissUpdateFailure,
  getUpdateOutcome,
  type UpdateDirection,
  type UpdateFailure,
  type UpdateOutcome,
} from "../api/backend";
import { detach } from "./detach";
import { logToastFailure, raiseFailureToastOnce, stillOnToast, toastWhenSteamIsReady } from "./failedUpdateToast";

/** An update the installer rolled back, or its pre-install check refused, in this store's spelling. */
export interface RolledBackUpdate {
  attemptedVersion: string;
  restoredVersion: string;
  /** The record's own ISO-8601 UTC text — what tells one record from the next. */
  rolledBackAt: string;
  kind: UpdateFailure["kind"];
}

/** A version this backend process moved to, and which way it moved. */
export interface UpdateAnnouncement {
  version: string;
  direction: UpdateDirection;
}

export interface UpdateOutcomeState {
  /** The version that moved, until its card was dismissed. `null` where none, or before the backend answered. */
  announcement: UpdateAnnouncement | null;
  /** The installer's record of an update that did not go through. `null` where none stands, or before the backend answered. */
  failure: RolledBackUpdate | null;
  /** The user waved away the card for this exact record. */
  failureDismissed: boolean;
}

const INITIAL: UpdateOutcomeState = { announcement: null, failure: null, failureDismissed: false };

/**
 * The line under a rolled-back update's sentence, on the card and in its home.
 * Why it names those two places: docs/architecture/qam-panel.md, "Notices and
 * homes".
 */
export const UPDATE_FAILURE_REASON =
  "Tender's log, backend.log, says why — or the journal (journalctl --user -u romm-tender), if the new version failed before it could write to the log.";

/** Where the installer's own output is. */
const INSTALLER_OUTPUT =
  "The installer's output says why: journalctl --user -u romm-tender-update, or the terminal it was run in.";

/** The same line for an update the pre-install check refused, which never ran the new version as a service. */
export const UPDATE_CHECK_FAILURE_REASON = `The new version did not start, so nothing was changed. ${INSTALLER_OUTPUT}`;

/** The line for that refusal under Settings › Updates, whose title already says nothing was changed. */
export const UPDATE_CHECK_FAILURE_NOTE = `The new version did not start. ${INSTALLER_OUTPUT}`;

/** The same line for a record of a kind this version does not know: no cause is named, only where it is. */
export const UPDATE_UNKNOWN_FAILURE_REASON = INSTALLER_OUTPUT;

let _state: UpdateOutcomeState = INITIAL;
let _listeners: Array<() => void> = [];

/**
 * Ordering fence for the read: it takes the number before its `await` and
 * writes nothing if the number moved meanwhile. Either Dismiss moves it, so a
 * read that was in flight when Dismiss was pressed cannot put a card back up.
 * Dismiss is fenced by the record instead: once the backend recorded it, the
 * card it was pressed on is dismissed whatever was read around it, and a
 * different record pushed meanwhile keeps its own.
 */
let _seq = 0;

export function setUpdateOutcomeState(state: UpdateOutcomeState): void {
  _state = state;
  notifyUpdateOutcome();
}

/** Tell every subscriber something about the last update changed — `stoppedUpdateStore.ts` shares them. */
export function notifyUpdateOutcome(): void {
  _listeners.forEach((fn) => fn());
}

/** Test seam: drop the answer, as a fresh panel load has it. */
export function resetUpdateOutcomeStoreForTests(): void {
  _seq = 0;
  setUpdateOutcomeState(INITIAL);
}

export function getUpdateOutcomeState(): UpdateOutcomeState {
  return _state;
}

export function onUpdateOutcomeChange(fn: () => void): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}

/** Subscribe to the update outcome from a component. */
export function useUpdateOutcomeState(): UpdateOutcomeState {
  return useSyncExternalStore(onUpdateOutcomeChange, getUpdateOutcomeState);
}

function failureFromWire(failure: UpdateFailure | null): RolledBackUpdate | null {
  if (failure === null) return null;
  return {
    attemptedVersion: failure.attempted_version,
    restoredVersion: failure.restored_version,
    rolledBackAt: failure.rolled_back_at,
    kind: failure.kind,
  };
}

function stateFromOutcome(outcome: UpdateOutcome): UpdateOutcomeState {
  return {
    announcement:
      outcome.announce_version === null
        ? null
        : { version: outcome.announce_version, direction: outcome.announce_direction },
    failure: failureFromWire(outcome.failure),
    failureDismissed: outcome.failure_dismissed,
  };
}

const FAILURE_REASONS: Record<RolledBackUpdate["kind"], string> = {
  rollback: UPDATE_FAILURE_REASON,
  check: UPDATE_CHECK_FAILURE_REASON,
  unknown: UPDATE_UNKNOWN_FAILURE_REASON,
};

/** The line under {@link updateFailureSentence}, for the kind of record it states. */
export function updateFailureReason(failure: RolledBackUpdate): string {
  return FAILURE_REASONS[failure.kind];
}

/** The one sentence an update that did not go through is stated in, whatever the installer's record says of why. */
export function updateFailureSentence(failure: RolledBackUpdate): string {
  return updateDidNotGoThrough(failure.attemptedVersion, failure.restoredVersion);
}

/** An update to *attempted* that did not go through, on *stillOn* — rolled back, refused, or its installer stopped. */
export function updateDidNotGoThrough(attempted: string, stillOn: string): string {
  return `Update to ${attempted} failed — you are still on ${stillOn}.`;
}

/** The toast the update announcement is raised in, for each way the version can have moved. */
function updateAnnouncementToast(version: string, direction: UpdateDirection): string {
  return direction === "updated" ? `Tender updated to ${version}` : `Tender is back on ${version}`;
}

/** The title of the announcement's card on Main, for each way the version can have moved. */
export function updateAnnouncementSentence(announcement: UpdateAnnouncement): string {
  return announcement.direction === "updated"
    ? `Tender was updated to ${announcement.version}.`
    : `Tender is back on ${announcement.version}.`;
}

/** Whether the rolled-back card is up: a record stands and was not dismissed. */
export function failureCardShows(state: UpdateOutcomeState): boolean {
  return state.failure !== null && !state.failureDismissed;
}

/**
 * Whether the installer's record takes the place of the "is available" card for
 * *latestVersion*: true exactly when a record stands and names that version as
 * the one it tried, whether or not its card was dismissed.
 */
export function failureTakesThePlaceOf(latestVersion: string | null, state: UpdateOutcomeState): boolean {
  return state.failure !== null && state.failure.attemptedVersion === latestVersion;
}

/** Raise the toast for the installer's record once, and tell the backend it was raised. Never rejects. */
function toastRecord(failure: UpdateFailure): Promise<void> {
  return raiseFailureToastOnce(
    `record ${failure.rolled_back_at}`,
    stillOnToast(failure.attempted_version, failure.restored_version),
    () => acknowledgeUpdateFailureToast(failure.rolled_back_at),
  ).catch(logToastFailure);
}

/**
 * Ask the backend what the last update did, fill the store — the announcement's
 * card among it — and raise the toast for a version that moved, to a later
 * release or back to an earlier one, and for an update that did not go
 * through, each where the backend still owes it.
 *
 * Each toast waits until Steam can show it (what it waits for, how long at
 * most, and why: `steamReadyForToasts.ts`), and is acknowledged only after it
 * was raised: the backend owes the announcement once per process, so a panel
 * reloaded by a Steam restart shows the card again but does not raise the
 * toast a second time, and the record's toast once for good. An
 * acknowledgement that fails leaves it owed, and the next panel load raises it
 * again — a repeat rather than a loss. A read something overtook — a Dismiss,
 * or a push — raises no record's toast: the push raises its own, and a record
 * still owed after a Dismiss of another card is raised at the next load. The
 * record's toast failing does not keep the announcement's from being raised.
 */
export async function fetchUpdateOutcome(): Promise<void> {
  const seq = ++_seq;
  const outcome = await getUpdateOutcome();
  const current = seq === _seq;
  if (current) setUpdateOutcomeState(stateFromOutcome(outcome));
  if (current && outcome.failure_toast_owed && outcome.failure !== null) await toastRecord(outcome.failure);
  if (outcome.toast_owed) {
    await toastWhenSteamIsReady(
      updateAnnouncementToast(outcome.announce_version, outcome.announce_direction),
      "the update announcement",
    );
    await acknowledgeUpdateToast();
  }
}

/**
 * Wave the announcement's card away, then take it down here — only once the
 * backend answered that it recorded that. A failed call rejects and leaves the
 * card up.
 */
export async function dismissUpdateAnnouncementCard(): Promise<void> {
  ++_seq;
  await dismissUpdateAnnouncement();
  setUpdateOutcomeState({ ..._state, announcement: null });
}

/**
 * Take the record of a refusal by the pre-install check the backend pushed. It
 * outranks a read still in flight, and it is a new record, so its card is up
 * and its toast is owed.
 */
export function takePushedUpdateFailure(pushed: UpdateFailure): void {
  ++_seq;
  setUpdateOutcomeState({ ..._state, failure: failureFromWire(pushed), failureDismissed: false });
  detach(toastRecord(pushed));
}

/**
 * Wave the rolled-back card away for one record, then take it down here — only
 * once the backend answered that it persisted the dismissal, and only while the
 * store still holds that record or none. A refused or failed write rejects and
 * leaves the card up.
 */
export async function dismissUpdateFailureRecord(rolledBackAt: string): Promise<void> {
  ++_seq;
  const write = await dismissUpdateFailure(rolledBackAt);
  if (!write.success) throw new Error(`${write.reason}: ${write.message}`);
  if (_state.failure !== null && _state.failure.rolledBackAt !== rolledBackAt) return;
  setUpdateOutcomeState({ ..._state, failureDismissed: true });
}
