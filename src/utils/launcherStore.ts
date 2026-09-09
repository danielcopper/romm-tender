/**
 * Module-level store for what the launcher relocation left standing, and what
 * the user has already answered about it.
 *
 * `relocated` is true once a pass has rewritten every RomM-owned shortcut that
 * did not already carry the launcher's home, which is the one fact that says no
 * shortcut of ours still points into a plugin folder. Only the frontend can
 * answer it: the backend knows where the launcher IS, never where the shortcuts
 * point.
 *
 * It starts false and is never set back. False therefore means "not established
 * this session" — the pass has not finished, could not read Steam's shortcut
 * store, or found no launcher to point at — and every reader is written for that
 * reading, because the panel would otherwise tell a user the pre-rename install
 * is safe to remove on the strength of a scan that never ran.
 *
 * `removalDismissed` is the user's answer to the card that relocation unlocks:
 * keeping the older install is a legitimate choice, and it must not cost a
 * standing warning on Main. It lives here rather than in `legacyInstallStore`
 * because it answers this condition, not that one — and it is deliberately
 * session-scoped, not persisted: the condition it acknowledges ends when the
 * folder does, and a marker on disk would outlive it.
 *
 * Updated by:
 *   - plugin load init in index.tsx (relocateShortcutsToLauncher)
 *   - the legacy-install card's Dismiss (dismissLegacyRemovalNotice)
 *
 * Read by:
 *   - components/LegacyInstallBanner.tsx through {@link useLauncherState},
 *     which decides which of the card's three statements the panel shows
 *
 * Every write installs a NEW state object and notifies. That is what lets
 * {@link getLauncherState} serve as a `useSyncExternalStore` snapshot — React
 * compares snapshots by identity, so a getter handing back a fresh object per
 * call would re-render forever.
 */

import { useSyncExternalStore } from "react";

export interface LauncherState {
  relocated: boolean;
  removalDismissed: boolean;
}

let _state: LauncherState = { relocated: false, removalDismissed: false };
let _listeners: Array<() => void> = [];

function write(next: LauncherState): void {
  _state = next;
  _listeners.forEach((fn) => fn());
}

export function setLauncherRelocated(relocated: boolean): void {
  write({ ..._state, relocated });
}

/** Acknowledge the "you can remove the older install now" card for this session. */
export function dismissLegacyRemovalNotice(): void {
  write({ ..._state, removalDismissed: true });
}

/** Test seam: drop both answers, as a fresh plugin load has them. */
export function resetLauncherStoreForTests(): void {
  write({ relocated: false, removalDismissed: false });
}

export function getLauncherState(): LauncherState {
  return _state;
}

export function onLauncherChange(fn: () => void): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}

/** Subscribe to the relocation state from a component. */
export function useLauncherState(): LauncherState {
  return useSyncExternalStore(onLauncherChange, getLauncherState);
}
