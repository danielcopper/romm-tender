/**
 * Module-level store for the pre-rename install standing beside this one.
 *
 * Mirrors playtimeScopeStore: a backend-state-driven notice surfaced as a QAM
 * banner (no toast, no per-game card). Unlike its siblings the backend persists
 * nothing — the answer is read off the filesystem on every call, because the
 * condition ends when the older plugin folder does. So there is no dismiss, on
 * either side: the notice stands until a future version moves the data over and
 * removes the folder.
 *
 * Updated by:
 *   - plugin load init in index.tsx (fetchLegacyInstallState)
 *   - the card's Dismiss (dismissLegacyInstall), after the backend persisted it
 *
 * Read by:
 *   - components/LegacyInstallBanner.tsx through {@link useLegacyInstallState},
 *     in `LegacyInstallNotice` — the element Main and both full-page states
 *     render. It decides whether the banner appears at all and joins
 *     `legacyDataPresent` with the panel's own ROM count to decide whether the
 *     banner also says this install starts empty
 *
 * Every write installs a NEW state object and notifies. That is what lets
 * {@link getLegacyInstallState} serve as a `useSyncExternalStore` snapshot —
 * React compares snapshots by identity, so a getter handing back a fresh object
 * per call would re-render forever, and an in-place write would leave a
 * subscriber unable to tell that the notice moved.
 */

import { useSyncExternalStore } from "react";
import { dismissLegacyInstallNotice, getLegacyInstallNotice } from "../api/backend";

export interface LegacyInstallState {
  pending: boolean;
  legacyDataPresent: boolean;
  /**
   * The user has answered the card's removable statement and is keeping the
   * older install. Persisted by the backend as user intent, so it survives a
   * restart — a card that came back at every Steam start is exactly the
   * standing warning the Dismiss exists to prevent.
   */
  dismissed: boolean;
}

let _state: LegacyInstallState = { pending: false, legacyDataPresent: false, dismissed: false };
let _listeners: Array<() => void> = [];

export function setLegacyInstallState(state: LegacyInstallState): void {
  _state = state;
  _listeners.forEach((fn) => fn());
}

export function getLegacyInstallState(): LegacyInstallState {
  return _state;
}

export function onLegacyInstallChange(fn: () => void): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}

/** Subscribe to the legacy-install notice from a component. Re-renders the
 *  caller whenever it changes and drops its subscription on unmount. */
export function useLegacyInstallState(): LegacyInstallState {
  return useSyncExternalStore(onLegacyInstallChange, getLegacyInstallState);
}

/**
 * Fetch the backend notice and update the store. Returns the resolved state so
 * callers can react without re-reading the store.
 */
export async function fetchLegacyInstallState(): Promise<LegacyInstallState> {
  const notice = await getLegacyInstallNotice();
  const next: LegacyInstallState = {
    pending: notice.pending,
    legacyDataPresent: notice.legacy_data_present,
    dismissed: notice.dismissed,
  };
  setLegacyInstallState(next);
  return next;
}

/**
 * Answer the card for good: persist the dismissal, then take it down here.
 *
 * The store is written only after the backend has accepted the write, so a
 * dismissal that did not persist leaves the card up rather than hiding it until
 * the next start and bringing it back — which reads as the plugin forgetting.
 */
export async function dismissLegacyInstall(): Promise<void> {
  await dismissLegacyInstallNotice();
  setLegacyInstallState({ ..._state, dismissed: true });
}
