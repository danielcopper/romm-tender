/**
 * How an apply run that ended at stage `error` is worded — one sentence for the
 * toast `index.tsx` raises and the Sync page's status line alike, so the two
 * never say the same failure two ways.
 */

const SYNC_FAILED = "Sync failed";

/**
 * The frame's message behind "Sync failed — ", never the prefix twice. A failure
 * inside the run is already worded that way; one before its work queue was built
 * carries the bare `classify_error` message
 * (`services/library/sync_orchestrator.py`, `_do_sync_per_unit`).
 */
export function syncFailedMessage(message: string | undefined): string {
  if (!message) return `${SYNC_FAILED}.`;
  if (message.startsWith(SYNC_FAILED)) return message;
  return `${SYNC_FAILED} — ${message}`;
}
