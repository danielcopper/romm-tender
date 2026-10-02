/**
 * The toast that a newer Tender release is out: its words, and when it is raised.
 *
 * It follows the "is available" card (`updateAvailableView.ts`) and the
 * backend's `toastOwed`, which holds it to once per version across starts, to
 * the daily check's switch, and off a release Check now found or the user has
 * already seen in Settings › Updates. Beyond those it
 * waits — for the reads made at panel load, while an install attempt is under
 * way or pressed for, and while Steam's notification lookups are missing — and
 * is asked again whenever a store it reads changes, so a pushed `update_notice`
 * or the end of an attempt raises it without a reload. Once Steam can show it,
 * it is asked once more, so a change during that wait is not overtaken.
 * The rules and why: `docs/architecture/qam-panel.md`, "Notices and homes".
 */

import { acknowledgeUpdateAvailableToast, logError, logWarn } from "../api/backend";
import { detach } from "./detach";
import { raiseUpdateToastOnce } from "./failedUpdateToast";
import { notificationsUnavailable } from "./notificationsHealth";
import { getStoppedUpdateAttempt } from "./stoppedUpdateStore";
import { availableCardVersion } from "./updateAvailableView";
import { installUnderWay, onUpdateInstallAttemptChange } from "./updateInstallStore";
import { getUpdateNoticeState, onUpdateNoticeChange } from "./updateNoticeStore";
import { getUpdateOutcomeState, onUpdateOutcomeChange } from "./updateOutcomeStore";

/** The toast's words for *version*. */
export function updateAvailableToast(version: string): string {
  return `Tender ${version} is available. Settings › Updates to install it.`;
}

/** The watch running in this JavaScript context, which a later {@link watchUpdateAvailableToast} replaces. */
let stopWatching: () => void = () => undefined;

/**
 * The release a toast is owed for right now, or `null`. A store state it
 * cannot be worked out from is `null` and logged rather than thrown: it runs
 * inside the stores' listener loops, where a throw would stop the card's and
 * the dot's listeners after it.
 */
function owedVersion(): string | null {
  try {
    if (notificationsUnavailable()) return null;
    const notice = getUpdateNoticeState();
    // `toastOwed` is the backend's answer as last read; the switch may have
    // gone off since, and with it off this program says nothing by itself.
    if (!notice.toastOwed || !notice.enabled) return null;
    const version = availableCardVersion(notice, getUpdateOutcomeState(), getStoppedUpdateAttempt());
    if (version === null || installUnderWay()) return null;
    return version;
  } catch (e) {
    logError(`Failed to work out whether a newer release's toast is owed: ${e}`);
    return null;
  }
}

function raiseIfOwed(): void {
  const version = owedVersion();
  if (version === null) return;
  raiseUpdateToastOnce(
    `available ${version}`,
    updateAvailableToast(version),
    "the release's toast",
    () => acknowledgeUpdateAvailableToast(version),
    () => owedVersion() === version,
  ).catch((e) => logError(`Failed to raise the toast for ${version}: ${e}`));
}

/**
 * Raise the toast where it is owed, once the load-time reads have settled, and
 * again whenever the notice, what the last update did, a stopped attempt or
 * the install attempt changes. *noticeRead* only has to settle: a release read
 * that failed leaves no toast owed. Each of *mustSucceed* answers whether it
 * did — what the last update did, a stopped attempt, an attempt under way —
 * and where one did not, this context raises nothing: a failure record or an
 * attempt it could not read might be the one that holds the toast back, which
 * then stays owed for the next panel load. Waiting for them all is what keeps
 * a failure record for the same version that answers last from being
 * overtaken. A second call ends the first watch rather than adding a second
 * one beside it. Returns the unsubscribe.
 */
export function watchUpdateAvailableToast(noticeRead: Promise<unknown>, mustSucceed: Promise<boolean>[]): () => void {
  stopWatching();
  let settled = false;
  let watching = true;
  const ask = () => {
    if (watching && settled) raiseIfOwed();
  };
  const unsubscribes = [onUpdateNoticeChange, onUpdateOutcomeChange, onUpdateInstallAttemptChange].map((on) => on(ask));
  detach(
    Promise.allSettled([noticeRead, ...mustSucceed]).then(([, ...reads]) => {
      if (reads.every((read) => read.status === "fulfilled" && read.value)) {
        settled = true;
        ask();
      } else if (watching) {
        logWarn("The toast that a newer release is out is held until the next panel load: a read it waits for failed");
      }
    }),
  );
  stopWatching = () => {
    watching = false;
    unsubscribes.forEach((unsubscribe) => unsubscribe());
  };
  return stopWatching;
}
