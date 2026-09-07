/**
 * Two questions about a pending preview: is there anything to apply, and how
 * long is the backend still going to accept it.
 *
 * They live here rather than on the Sync page because Main asks the second one
 * too — its Sync button reads "Review changes" only while the preview is still
 * good, and an expired preview counts as none. The first is the Sync page's
 * alone.
 */

import type { SyncPreview } from "../types";

/** How often a page's expiry countdown re-reads the clock. The readout itself
 *  is minute-coarse; the second-level cadence is what makes the switch to the
 *  expired notice land promptly rather than up to a minute late. */
export const PREVIEW_COUNTDOWN_TICK_MS = 1000;

/**
 * Whether *preview* has anything for Apply Sync to do — the condition the Apply
 * button and the estimate lines hang off. A preview with nothing to apply is
 * still a preview ("Everything is up to date."), just one with no work to
 * approve.
 */
export function previewHasChanges(preview: SyncPreview): boolean {
  const s = preview.summary;
  return (
    s.new_count + s.changed_count + s.remove_count > 0 ||
    !!(s.collection_diff?.added.length || s.collection_diff?.removed.length) ||
    !!s.platform_collection_diff?.has_changes ||
    // Cover-only work (#1386): the refresh pass runs inside the apply, so an
    // empty shortcut delta with pending cover updates must still offer Apply —
    // the old "no changes" short-circuit stranded changed covers forever.
    (s.cover_refresh_count ?? 0) > 0 ||
    // Unstamped platforms (#1416): a late-ack-recovered platform needs a
    // 0-delta apply run to re-stamp itself and heal the lingering
    // "interrupted" status, so offer Apply even when every change count is zero.
    (s.restamp_platform_count ?? 0) > 0
  );
}

/**
 * Seconds left before the backend stops accepting *preview*, measured against
 * *nowMs*, or `null` when the preview carries no deadline (an older backend) —
 * the page then shows no countdown at all. Never negative: past the deadline it
 * is 0, which every reader takes as expired.
 *
 * `nowMs` is passed in rather than read here: the value ticks from an interval
 * into state, so render stays pure.
 */
export function previewSecondsLeft(preview: SyncPreview, nowMs: number): number | null {
  if (preview.expires_at === undefined) return null;
  return Math.max(0, preview.expires_at - nowMs / 1000);
}
