/**
 * What the views make of a stranded panel: the answer the backend gave it, for
 * a component to render, the one notification per answer, and what a press made
 * on a stranded panel says ({@link tellStrandedPress}).
 *
 * The answer itself is the socket's (`api/hostSocket.ts`), which hears it in the
 * code the backend closes a stranded panel's upgrade with, and tells its
 * listeners only when it changes — first set, or a re-check that answered
 * differently. So "one notification when the panel becomes stranded, and one
 * more each time the answer changes" is one notification per change heard.
 *
 * The notification is raised from {@link watchStrandedPanel}, which `index.tsx`
 * starts once per panel load.
 */

import { useEffect, useSyncExternalStore } from "react";

import { onStrandedAnswerChange, recheckStranded, strandedAnswer } from "../api/host";
import { detach } from "./detach";
import { useOwningQamTabActive } from "./owningQamTab";
import { useQuickAccessVisible } from "./quickAccessVisible";
import { STRANDED_PANEL_HEADLINE, strandedPanelDetail, type StrandedAnswer } from "./strandedPanelWording";
import { showToast } from "./toast";

function raise(answer: StrandedAnswer): void {
  showToast(STRANDED_PANEL_HEADLINE, { subtext: strandedPanelDetail(answer) });
}

/**
 * Raise the notification for the answer the panel holds now, if any, and for
 * every change from here on. Answers the unsubscribe.
 */
export function watchStrandedPanel(): () => void {
  const now = strandedAnswer();
  if (now) raise(now);
  return onStrandedAnswerChange(raise);
}

/**
 * Say that a press made while the panel is stranded did nothing, *headline*
 * saying what did not happen and *sentence* being the answer the backend gave
 * the panel. The answer may have changed since, so the panel asks again; a
 * changed answer raises its own notification.
 */
export function tellStrandedPress(headline: string, sentence: string): void {
  showToast(headline, { subtext: sentence });
  detach(recheckStranded());
}

/** The stranded answer from a component, or `null` while the panel is not stranded. Re-renders on a change. */
export function useStrandedAnswer(): StrandedAnswer | null {
  return useSyncExternalStore(onStrandedAnswerChange, strandedAnswer);
}

/**
 * Ask the backend again each time the Quick Access menu is opened on Tender's
 * page. A tab switch back to Main with the menu open is not seen: only the wide
 * pages publish which tab is active. What a stranded panel
 * was told may have changed since — a reload limit that freed up, or a recovery
 * that gave up. Opening is read off the two signals the update dots' dwell uses
 * (`utils/updateDot.ts`), not off a mount: Quick Access keeps the panel mounted
 * while it is closed. Where the panel is not stranded the re-check asks nothing.
 */
export function useRecheckStrandedWhenOpened(): void {
  const qamVisible = useQuickAccessVisible();
  const tabActive = useOwningQamTabActive();
  const opened = qamVisible && tabActive;
  useEffect(() => {
    if (opened) detach(recheckStranded());
  }, [opened]);
}
