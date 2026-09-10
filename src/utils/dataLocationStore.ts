/**
 * Module-level store for where the plugin's own data lives.
 *
 * Mirrors legacyInstallStore: a backend-state-driven notice surfaced as a QAM
 * card. The backend answers off what the START decided rather than off a fresh
 * probe, so the condition cannot change while the plugin runs — the copy it is
 * about happens before the database is opened, on the next start. That is also
 * why there is no dismiss on either side: a "choice" ends when a start has acted
 * on the answer, and a "failed" ends when a start's copy finishes.
 *
 * Updated by:
 *   - plugin load init in index.tsx (fetchDataLocationState)
 *
 * Read by:
 *   - components/DataLocationNotice.tsx through {@link useDataLocationState}
 *
 * Every write installs a NEW state object and notifies. That is what lets
 * {@link getDataLocationState} serve as a `useSyncExternalStore` snapshot —
 * React compares snapshots by identity, so a getter handing back a fresh object
 * per call would re-render forever, and an in-place write would leave a
 * subscriber unable to tell that the notice moved.
 */

import { useSyncExternalStore } from "react";
import { getDataLocationNotice, type DataLocationKind } from "../api/backend";

export interface DataLocationState {
  pending: boolean;
  /** `null` exactly when nothing is pending. */
  kind: DataLocationKind | null;
  /** What went wrong, on a `failed` condition only. */
  message: string | null;
}

const IDLE: DataLocationState = { pending: false, kind: null, message: null };

let _state: DataLocationState = IDLE;
let _listeners: Array<() => void> = [];

export function setDataLocationState(state: DataLocationState): void {
  _state = state;
  _listeners.forEach((fn) => fn());
}

export function getDataLocationState(): DataLocationState {
  return _state;
}

export function onDataLocationChange(fn: () => void): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}

/** Subscribe to the data-location notice from a component. Re-renders the
 *  caller whenever it changes and drops its subscription on unmount. */
export function useDataLocationState(): DataLocationState {
  return useSyncExternalStore(onDataLocationChange, getDataLocationState);
}

/**
 * Fetch the backend notice and update the store. Returns the resolved state so
 * callers can react without re-reading the store.
 */
export async function fetchDataLocationState(): Promise<DataLocationState> {
  const notice = await getDataLocationNotice();
  const next: DataLocationState = {
    pending: notice.pending,
    kind: notice.kind,
    message: notice.message,
  };
  setDataLocationState(next);
  return next;
}
