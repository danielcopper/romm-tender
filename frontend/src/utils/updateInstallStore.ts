/**
 * Module-level store for the latest install attempt the backend reported.
 *
 * Updated by:
 *   - the `update_install_progress` listener in index.tsx, one frame per step
 *     and a throttled one per stretch of downloaded bytes
 *   - a press of Install (bigpicture/settings/useUpdateInstall.ts), which
 *     clears the frame an earlier attempt left
 *
 * Read by:
 *   - bigpicture/settings/useUpdateInstall.ts, beside the state it reads
 *
 * Holds only what the event carries: whether an install is offered and what it
 * waits for is a read (`getUpdateInstallState`), not a push.
 *
 * Every write installs a NEW value and notifies, which is what lets
 * {@link getUpdateInstallAttempt} serve as a `useSyncExternalStore` snapshot.
 */

import { useSyncExternalStore } from "react";
import type { UpdateInstallAttempt } from "../api/backend";

let _attempt: UpdateInstallAttempt | null = null;
let _listeners: Array<() => void> = [];

export function setUpdateInstallAttempt(attempt: UpdateInstallAttempt | null): void {
  _attempt = attempt;
  _listeners.forEach((fn) => fn());
}

export function getUpdateInstallAttempt(): UpdateInstallAttempt | null {
  return _attempt;
}

export function onUpdateInstallAttemptChange(fn: () => void): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}

/** Subscribe to the latest attempt from a component. */
export function useUpdateInstallAttempt(): UpdateInstallAttempt | null {
  return useSyncExternalStore(onUpdateInstallAttemptChange, getUpdateInstallAttempt);
}
