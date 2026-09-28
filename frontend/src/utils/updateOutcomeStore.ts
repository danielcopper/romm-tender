/**
 * Module-level store for what the last update did, as the backend found it.
 *
 * Updated by:
 *   - panel load in index.tsx (fetchUpdateOutcome), detached — which is also
 *     where an update that went through is announced, once
 *   - the rolled-back card's Dismiss (dismissUpdateFailureRecord), after the
 *     backend persisted it
 *
 * Read by:
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
  acknowledgeUpdateAnnouncement,
  dismissUpdateFailure,
  getUpdateOutcome,
  type UpdateFailure,
  type UpdateOutcome,
} from "../api/backend";
import { showToast } from "./toast";

/** An update the installer rolled back, in this store's spelling. */
export interface RolledBackUpdate {
  attemptedVersion: string;
  restoredVersion: string;
  /** The record's own ISO-8601 UTC text — what tells one record from the next. */
  rolledBackAt: string;
}

export interface UpdateOutcomeState {
  /** The installer's record of a rolled-back update. `null` where none stands, or before the backend answered. */
  failure: RolledBackUpdate | null;
  /** The user waved away the card for this exact record. */
  failureDismissed: boolean;
}

const INITIAL: UpdateOutcomeState = { failure: null, failureDismissed: false };

/**
 * The line under a rolled-back update's sentence, on the card and in its home.
 * Why it names those two places: docs/architecture/qam-panel.md, "Notices and
 * homes".
 */
export const UPDATE_FAILURE_REASON =
  "Tender's log, backend.log, says why — or the journal (journalctl --user -u romm-tender), if the new version failed before it could write to the log.";

let _state: UpdateOutcomeState = INITIAL;
let _listeners: Array<() => void> = [];

/**
 * Ordering fence for the read: it takes the number before its `await` and
 * writes nothing if the number moved meanwhile. Dismiss moves it, so a read that
 * was in flight when Dismiss was pressed cannot put the card back up. Dismiss
 * itself is not fenced — once the backend persisted it, the record is dismissed
 * whatever was read around it.
 */
let _seq = 0;

export function setUpdateOutcomeState(state: UpdateOutcomeState): void {
  _state = state;
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
  };
}

function stateFromOutcome(outcome: UpdateOutcome): UpdateOutcomeState {
  return { failure: failureFromWire(outcome.failure), failureDismissed: outcome.failure_dismissed };
}

/** The one sentence a rolled-back update is stated in, wherever it is stated. */
export function updateFailureSentence(failure: RolledBackUpdate): string {
  return `Update to ${failure.attemptedVersion} failed — you are still on ${failure.restoredVersion}.`;
}

/** Whether the rolled-back card is up: a record stands and was not dismissed. */
export function failureCardShows(state: UpdateOutcomeState): boolean {
  return state.failure !== null && !state.failureDismissed;
}

/**
 * Whether the rolled-back record takes the place of the "is available" card for
 * *latestVersion*: true exactly when a record stands and names that version as
 * the one it tried, whether or not its card was dismissed.
 */
export function failureTakesThePlaceOf(latestVersion: string | null, state: UpdateOutcomeState): boolean {
  return state.failure !== null && state.failure.attemptedVersion === latestVersion;
}

/**
 * Ask the backend what the last update did, fill the store, and announce an
 * update that went through.
 *
 * The announcement is one toast, and it is acknowledged only after it was
 * raised: the backend owes it once per process, so a panel reloaded by a Steam
 * restart does not raise it a second time. An acknowledgement that fails leaves
 * it owed, and the next panel load says it again — a repeat rather than a loss.
 */
export async function fetchUpdateOutcome(): Promise<void> {
  const seq = ++_seq;
  const outcome = await getUpdateOutcome();
  if (seq === _seq) setUpdateOutcomeState(stateFromOutcome(outcome));
  if (outcome.announce_version !== null) {
    showToast(`Tender updated to ${outcome.announce_version}`);
    await acknowledgeUpdateAnnouncement();
  }
}

/**
 * Wave the rolled-back card away for one record, then take it down here — only
 * once the backend answered that it persisted the dismissal. A refused or
 * failed write rejects and leaves the card up.
 */
export async function dismissUpdateFailureRecord(rolledBackAt: string): Promise<void> {
  ++_seq;
  const write = await dismissUpdateFailure(rolledBackAt);
  if (!write.success) throw new Error(`${write.reason}: ${write.message}`);
  setUpdateOutcomeState({ ..._state, failureDismissed: true });
}
