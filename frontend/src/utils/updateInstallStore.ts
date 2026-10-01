/**
 * Module-level store for the latest install attempt the backend reported, and
 * when this panel first saw it under way and its installer started.
 *
 * Updated by:
 *   - the `update_install_progress` listener in index.tsx, one frame per step
 *     and a throttled one per stretch of downloaded bytes
 *   - a press of Install (bigpicture/settings/useUpdateInstall.ts), which
 *     clears the frame an earlier attempt left and stands for the attempt
 *     until its first frame or its refusal ({@link notePress}, {@link endPress})
 *   - the install's reads (useUpdateInstall.ts), which note an attempt and an
 *     installer they find
 *   - panel load in index.tsx, which seeds an attempt under way from the
 *     install's read where no frame came first ({@link seedUpdateInstallAttempt})
 *
 * Read by:
 *   - bigpicture/settings/useUpdateInstall.ts, beside the state it reads
 *   - bigpicture/settings/UpdateInstallRows.tsx, for the install's clock and
 *     the step an installer that stopped is marked at
 *   - utils/connectionProbe.ts, which does not call a backend the installer
 *     is restarting failed until {@link INSTALLER_OVERDUE_MS} has passed
 *   - utils/updateAvailableToast.ts, which holds the "is available" toast
 *     while an attempt is under way or a press stands for one
 *     ({@link installUnderWay})
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
// The wall-clock millisecond this panel first saw the attempt under way, and
// its installer started, in either a pushed frame or a read; `null` until then
// and again after a press.
let _attemptSeenAt: number | null = null;
let _installerSeenAt: number | null = null;
// A press of Install whose attempt no frame has shown yet.
let _pressed = false;

export function setUpdateInstallAttempt(attempt: UpdateInstallAttempt | null): void {
  _attempt = attempt;
  if (attempt === null) _attemptSeenAt = _installerSeenAt = null;
  else {
    _pressed = false;
    noteAttempt(attempt);
  }
  _listeners.forEach((fn) => fn());
}

/**
 * A press of Install: drop the frame an earlier attempt left, and stand for
 * the new attempt until its first frame arrives or {@link endPress} says it
 * did not start.
 */
export function notePress(): void {
  _pressed = true;
  setUpdateInstallAttempt(null);
}

/** The press did not start an attempt: it was refused, or the call failed. */
export function endPress(): void {
  if (!_pressed) return;
  _pressed = false;
  _listeners.forEach((fn) => fn());
}

/**
 * Take *attempt*, the install's read at panel load, where nothing came first:
 * an attempt under way when the JavaScript context was replaced is otherwise
 * unknown here until its next frame. An ended attempt is left out — no frame
 * of this context's reported it, and the install's own read states it.
 */
export function seedUpdateInstallAttempt(attempt: UpdateInstallAttempt | null): void {
  if (_attempt !== null || _pressed || attempt === null || attempt.step === "failed") return;
  setUpdateInstallAttempt(attempt);
}

/** Whether an attempt is under way, or a press stands for one no frame has shown yet. */
export function installUnderWay(): boolean {
  return _pressed || (_attempt !== null && _attempt.step !== "failed");
}

/** Test seam: forget the attempt and any press, as a fresh JavaScript context has them. */
export function resetUpdateInstallStoreForTests(): void {
  _pressed = false;
  setUpdateInstallAttempt(null);
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

/** Note the moments the attempt is first seen under way and its installer started, from whichever of a frame or a read shows each first. */
export function noteAttempt(attempt: UpdateInstallAttempt | null, now: number = Date.now()): void {
  if (attempt === null || attempt.step === "failed") return;
  _attemptSeenAt ??= now;
  if (attempt.step === "installer_started") _installerSeenAt ??= now;
}

/** When this panel first saw the attempt under way, or `null`: what the install's clock counts from. */
export function attemptSeenAt(): number | null {
  return _attemptSeenAt;
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
