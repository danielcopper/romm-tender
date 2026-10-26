/**
 * Module-level migration state store.
 *
 * Updated by:
 *   - index.tsx: a pending getMigrationStatus answer at panel load; on a
 *     `retrodeck_path_changed` event, not pending where the event says the
 *     migration was cleared, otherwise a fresh getMigrationStatus answer
 *   - refreshMigrationState return value in MainPage, useMigrationRefresh and
 *     launchInterceptor
 *   - sessionManager, from the session-finalize result's `migration.retrodeck`
 *   - clearMigration() from MigrationBlockedPage, on a successful migration and
 *     on a successful dismiss
 *
 * Read by:
 *   - MainPage.tsx, RomMGameInfoPanel.tsx, RomMPlaySection.tsx and the desktop
 *     surface's GameView.tsx, all through {@link useMigrationStatus}
 *   - launchGateOps.ts, which reads the current status through
 *     {@link getMigrationState} outside of any render
 *
 * Every write installs a NEW status object and notifies. That is what lets
 * {@link getMigrationState} serve as a `useSyncExternalStore` snapshot — React
 * compares snapshots by identity, so a getter handing back a fresh object per
 * call would re-render forever, and an in-place write would leave a subscriber
 * unable to tell that the status moved.
 */

import { useSyncExternalStore } from "react";
import type { MigrationStatus } from "../types";

let _migration: MigrationStatus = { pending: false };
let _listeners: Array<() => void> = [];

export function setMigrationStatus(status: MigrationStatus): void {
  _migration = status;
  _listeners.forEach((fn) => fn());
}

export function getMigrationState(): MigrationStatus {
  return _migration;
}

export function clearMigration(): void {
  _migration = { pending: false };
  _listeners.forEach((fn) => fn());
}

export function onMigrationChange(fn: () => void): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}

/** Subscribe to the RetroDECK migration status from a component. Re-renders the
 *  caller whenever the status changes and drops its subscription on unmount. */
export function useMigrationStatus(): MigrationStatus {
  return useSyncExternalStore(onMigrationChange, getMigrationState);
}
