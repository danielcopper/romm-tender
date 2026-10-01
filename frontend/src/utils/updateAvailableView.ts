/**
 * Whether the "is available" card on Main shows, and for which release.
 *
 * One answer for the three things that follow the card — the card itself
 * (bigpicture/UpdateNotice.tsx), the dot on Tender's Quick Access glyph
 * (qam/TabIcon.tsx) and the "is available" toast (utils/updateAvailableToast.ts)
 * — so none of them can show for a release the others stay silent about.
 */

import { useUpdateNoticeState, type UpdateNoticeState } from "./updateNoticeStore";
import { failureTakesThePlaceOf, useUpdateOutcomeState, type UpdateOutcomeState } from "./updateOutcomeStore";
import {
  stoppedAttemptTakesThePlaceOf,
  useStoppedUpdateAttempt,
  type StoppedUpdateAttempt,
} from "./stoppedUpdateStore";

/** The card's colour, which the dot on the glyph is drawn in too. */
export const UPDATE_AVAILABLE_COLOR = "#3d9df6";

/**
 * The release the card names, or `null` where no card shows: a newer release
 * was found and not dismissed, and neither the installer's record of a failed
 * update to it nor a stopped attempt at it stands — each takes the card's place.
 */
export function availableCardVersion(
  notice: UpdateNoticeState,
  outcome: UpdateOutcomeState,
  stopped: StoppedUpdateAttempt | null,
): string | null {
  // `available` is decided by comparing a version, so it implies one; this is
  // the type narrowing, not a second condition.
  if (!notice.available || notice.latestVersion === null) return null;
  if (failureTakesThePlaceOf(notice.latestVersion, outcome)) return null;
  if (stoppedAttemptTakesThePlaceOf(notice.latestVersion, stopped)) return null;
  return notice.latestVersion;
}

/** {@link availableCardVersion} over the three stores, from a component. */
export function useAvailableCardVersion(): string | null {
  return availableCardVersion(useUpdateNoticeState(), useUpdateOutcomeState(), useStoppedUpdateAttempt());
}
