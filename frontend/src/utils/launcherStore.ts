/**
 * Module-level store for what the launcher relocation left standing.
 *
 * `relocated` is true once a pass has pointed every shortcut the backend named
 * at the launcher's home — the one fact that says no shortcut of ours still
 * names a plugin folder. Only the frontend can answer it: the backend knows
 * which shortcuts need the write, never whether the write happened.
 *
 * It starts false and is never set back. False therefore means "not established
 * this session" — the pass has not finished, or the backend said nothing may be
 * rewritten yet — and any reader must be written for that reading: false is not
 * evidence that a shortcut still points at the old path.
 *
 * Updated by:
 *   - plugin load init in index.tsx (relocateShortcutsToLauncher)
 *
 * Read by:
 *   - nothing, today. The card that read it through {@link useLauncherRelocated}
 *     asked whether the pre-rename plugin folder was safe to remove, and that
 *     question went with the plugin loader. The pass itself still runs and still
 *     records its answer here, because whether the shortcuts point at the
 *     launcher is the same question under any host.
 *
 * Every write installs a NEW state object and notifies. That is what lets
 * {@link getLauncherState} serve as a `useSyncExternalStore` snapshot — React
 * compares snapshots by identity, so a getter handing back a fresh object per
 * call would re-render forever.
 */

import { useSyncExternalStore } from "react";

export interface LauncherState {
  relocated: boolean;
}

let _state: LauncherState = { relocated: false };
let _listeners: Array<() => void> = [];

export function setLauncherRelocated(relocated: boolean): void {
  _state = { relocated };
  _listeners.forEach((fn) => fn());
}

/** Test seam: drop the answer, as a fresh plugin load has it. */
export function resetLauncherStoreForTests(): void {
  setLauncherRelocated(false);
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

/** Subscribe to the relocation answer from a component. */
export function useLauncherRelocated(): boolean {
  return useSyncExternalStore(onLauncherChange, getLauncherState).relocated;
}
