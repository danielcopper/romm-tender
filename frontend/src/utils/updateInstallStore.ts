/**
 * Module-level store for the latest install attempt the backend reported, and
 * when this panel first saw its installer started.
 *
 * Updated by:
 *   - the `update_install_progress` listener in index.tsx, one frame per step
 *     and a throttled one per stretch of downloaded bytes
 *   - a press of Install (bigpicture/settings/useUpdateInstall.ts), which
 *     clears the frame an earlier attempt left
 *   - the install's reads (useUpdateInstall.ts), which note an installer they
 *     find started
 *
 * Read by:
 *   - bigpicture/settings/useUpdateInstall.ts, beside the state it reads
 *   - utils/connectionProbe.ts, which does not call a backend the installer
 *     is restarting failed until {@link INSTALLER_OVERDUE_MS} has passed
 *
 * Holds only what the event carries and that one moment: whether an install
 * is offered and what it waits for is a read (`getUpdateInstallState`), not a
 * push.
 *
 * Every write installs a NEW value and notifies, which is what lets
 * {@link getUpdateInstallAttempt} serve as a `useSyncExternalStore` snapshot.
 */

import { useSyncExternalStore } from "react";
import type { UpdateInstallAttempt } from "../api/backend";
import { INSTALLER_OVERDUE_MS } from "./updateInstallView";

let _attempt: UpdateInstallAttempt | null = null;
let _listeners: Array<() => void> = [];
// The wall-clock millisecond this panel first saw the installer started, in
// either a pushed frame or a read; `null` until then and again after a press.
let _installerSeenAt: number | null = null;

export function setUpdateInstallAttempt(attempt: UpdateInstallAttempt | null): void {
  _attempt = attempt;
  if (attempt === null) _installerSeenAt = null;
  else noteInstaller(attempt);
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

/** Note the moment the installer is first seen started, from whichever of a frame or a read shows it first. */
export function noteInstaller(attempt: UpdateInstallAttempt | null, now: number = Date.now()): void {
  if (attempt?.step === "installer_started" && _installerSeenAt === null) _installerSeenAt = now;
}

/** When this panel first saw the installer started, or `null`. */
export function installerSeenAt(): number | null {
  return _installerSeenAt;
}

/**
 * Whether the installer was seen started within the last
 * {@link INSTALLER_OVERDUE_MS} — the stretch in which a backend that does not
 * answer is the installer's restart rather than a failure.
 */
export function installerRestarting(now: number = Date.now()): boolean {
  return _installerSeenAt !== null && now - _installerSeenAt < INSTALLER_OVERDUE_MS;
}
