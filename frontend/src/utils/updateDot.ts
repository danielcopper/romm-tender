/**
 * The dots that mark the way to a newer release — on Tender's Quick Access
 * glyph, on Main's Settings button and on Updates in the Settings list — and
 * what makes that release count as seen.
 *
 * Every dot shows while {@link updateDotVersion} names a release, so the three
 * cannot disagree. A dot that goes because its release was seen grows and fades
 * out once; a dot that goes for any other reason — a Dismiss, an install, a
 * failed update taking the card's place — simply goes, and nothing about a dot
 * moves at rest. The rules and why: `docs/architecture/qam-panel.md`, "Notices
 * and homes".
 */

import { useEffect, useState, type CSSProperties } from "react";
import { logError } from "../api/backend";
import { useOwningQamTabActive } from "./owningQamTab";
import { useQuickAccessVisible } from "./quickAccessVisible";
import { useStoppedUpdateAttempt, type StoppedUpdateAttempt } from "./stoppedUpdateStore";
import { availableCardVersion, updateDotVersion } from "./updateAvailableView";
import { markReleaseSeen, useUpdateNoticeState, type UpdateNoticeState } from "./updateNoticeStore";
import { useUpdateOutcomeState, type UpdateOutcomeState } from "./updateOutcomeStore";

/** How long Settings › Updates has to be shown without a break before its release counts as seen. */
export const SEEN_AFTER_MS = 1000;

/** How long a dot whose release was seen takes to grow and fade out. */
export const DOT_FADE_MS = 450;

/**
 * Where a fading dot ends: grown and gone, by transform and opacity alone. The
 * transition is set here, on the dot only for the fade, so a dot at rest
 * carries none.
 */
export const DOT_FADE_STYLE: CSSProperties = {
  transform: "scale(2.2)",
  opacity: 0,
  transition: `transform ${DOT_FADE_MS}ms ease-out, opacity ${DOT_FADE_MS}ms ease-out`,
};

/** A dot's state: not drawn, drawn at rest, or playing its one fade. */
export type UpdateDotPhase = "none" | "shown" | "fading";

interface DotAnswer {
  /** The release the dots mark, or `null`. */
  dot: string | null;
  /** The release the card on Main names, or `null`. */
  card: string | null;
}

/**
 * Both answers over the three stores. The Quick Access strip has no error
 * boundary — a throw there takes Steam's whole Quick Access menu down — so a
 * store state they cannot be worked out from is no dot and no card rather than
 * a throw.
 */
function dotAnswer(
  notice: UpdateNoticeState,
  outcome: UpdateOutcomeState,
  stopped: StoppedUpdateAttempt | null,
): DotAnswer {
  try {
    return { dot: updateDotVersion(notice, outcome, stopped), card: availableCardVersion(notice, outcome, stopped) };
  } catch {
    return { dot: null, card: null };
  }
}

function useDotAnswer(): DotAnswer {
  return dotAnswer(useUpdateNoticeState(), useUpdateOutcomeState(), useStoppedUpdateAttempt());
}

/**
 * The phase of one dot, from a component that draws one.
 *
 * The fade is told apart from every other way a dot goes by what stays: a dot
 * whose release the card on Main still names went because that release was
 * seen, since seen is the one thing that takes the dots and leaves the card.
 * The phase changes in the same render the stores do, so a fading dot is the
 * element that was shown a moment ago, and a CSS transition on it has a start
 * to run from; it is taken out {@link DOT_FADE_MS} later.
 */
export function useUpdateDot(): UpdateDotPhase {
  const { dot, card } = useDotAnswer();
  const [shownFor, setShownFor] = useState(dot);
  const [fading, setFading] = useState(false);
  if (dot !== shownFor) {
    setShownFor(dot);
    setFading(shownFor !== null && dot === null && card === shownFor);
  }
  useEffect(() => {
    if (!fading) return;
    const timer = setTimeout(() => setFading(false), DOT_FADE_MS);
    return () => clearTimeout(timer);
  }, [fading]);
  if (dot !== null) return "shown";
  return fading ? "fading" : "none";
}

/**
 * Record the release the dots mark as seen once Settings › Updates has been on
 * screen for {@link SEEN_AFTER_MS} without a break — *onUpdates*, the section
 * shown, with the Quick Access menu open on Tender's tab. Going elsewhere,
 * closing the menu or choosing another of its tabs before then cancels it, so
 * moving through the list past Updates does not count, and coming back starts
 * the wait over. A record the backend refused leaves the dots standing until
 * Updates is shown again.
 */
export function useSeenAfterDwell(onUpdates: boolean): void {
  const { dot } = useDotAnswer();
  const qamVisible = useQuickAccessVisible();
  const tabActive = useOwningQamTabActive();
  const shown = onUpdates && qamVisible && tabActive;
  useEffect(() => {
    if (!shown || dot === null) return;
    const timer = setTimeout(() => {
      markReleaseSeen(dot).catch((e) => logError(`Failed to record that ${dot} was seen: ${e}`));
    }, SEEN_AFTER_MS);
    return () => clearTimeout(timer);
  }, [shown, dot]);
}
