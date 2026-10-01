/**
 * The toast that a newer Tender release is out: its words, and when it is raised.
 *
 * It follows the "is available" card (`updateAvailableView.ts`) and the
 * backend's `toastOwed`, which holds it to once per version across starts, to
 * the daily check's switch, and off a release Check now found. Beyond those it
 * waits — for the three reads made at panel load, and while an install attempt
 * is under way — and is asked again whenever a store it reads changes, so a
 * pushed `update_notice` or the end of an attempt raises it without a reload.
 * The rules and why: `docs/architecture/qam-panel.md`, "Notices and homes".
 */

import { acknowledgeUpdateAvailableToast, logError } from "../api/backend";
import { detach } from "./detach";
import { raiseUpdateToastOnce } from "./failedUpdateToast";
import { getStoppedUpdateAttempt } from "./stoppedUpdateStore";
import { availableCardVersion } from "./updateAvailableView";
import { getUpdateInstallAttempt, onUpdateInstallAttemptChange } from "./updateInstallStore";
import { getUpdateNoticeState, onUpdateNoticeChange } from "./updateNoticeStore";
import { getUpdateOutcomeState, onUpdateOutcomeChange } from "./updateOutcomeStore";

/** The toast's words for *version*. */
export function updateAvailableToast(version: string): string {
  return `Tender ${version} is available. Settings › Updates to install it.`;
}

/** The watch running in this JavaScript context, which a later {@link watchUpdateAvailableToast} replaces. */
let stopWatching: () => void = () => undefined;

function raiseIfOwed(): void {
  const notice = getUpdateNoticeState();
  // `toastOwed` is the backend's answer as last read; the switch may have gone
  // off since, and with it off this program says nothing by itself.
  if (!notice.toastOwed || !notice.enabled) return;
  const version = availableCardVersion(notice, getUpdateOutcomeState(), getStoppedUpdateAttempt());
  if (version === null) return;
  const attempt = getUpdateInstallAttempt();
  if (attempt !== null && attempt.step !== "failed") return;
  raiseUpdateToastOnce(`available ${version}`, updateAvailableToast(version), "the release's toast", () =>
    acknowledgeUpdateAvailableToast(version),
  ).catch((e) => logError(`Failed to raise the toast for ${version}: ${e}`));
}

/**
 * Raise the toast where it is owed, once *loadReads* have all settled and
 * again whenever the notice, what the last update did, a stopped attempt or
 * the install attempt changes. Waiting for all three reads is what keeps a
 * failure record for the same version that answers last from being overtaken;
 * a read that failed counts as settled, since the card then shows from what
 * the stores hold and the toast follows the card. A second call ends the first
 * watch rather than adding a second one beside it. Returns the unsubscribe.
 */
export function watchUpdateAvailableToast(loadReads: Promise<unknown>[]): () => void {
  stopWatching();
  let settled = false;
  let watching = true;
  const ask = () => {
    if (watching && settled) raiseIfOwed();
  };
  const unsubscribes = [onUpdateNoticeChange, onUpdateOutcomeChange, onUpdateInstallAttemptChange].map((on) => on(ask));
  detach(
    Promise.allSettled(loadReads).then(() => {
      settled = true;
      ask();
    }),
  );
  stopWatching = () => {
    watching = false;
    unsubscribes.forEach((unsubscribe) => unsubscribe());
  };
  return stopWatching;
}
