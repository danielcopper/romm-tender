/**
 * Whether Tender can raise a toast at all, and the notice that says so.
 *
 * Steam's toast renderer and its notification store are both searches into
 * Steam's own bundle, and either one missing takes every toast off the air
 * (`utils/steamToaster.tsx`). The panel still works — syncs run, downloads run,
 * every result is on the pages — so the answer is a notice rather than a
 * refusal to mount.
 *
 * The value is written once, from the start-up check's report, and read from
 * render. Nothing subscribes and nothing polls: both names are read off Steam's
 * module registry and a global Steam installs at module scope, so the answer
 * cannot change while the process runs.
 */

let unavailable = false;

/** Record the start-up check's answer. */
export function setNotificationsUnavailable(value: boolean): void {
  unavailable = value;
}

/** Could the start-up check find what a toast is raised through? */
export function notificationsUnavailable(): boolean {
  return unavailable;
}

/**
 * The notice's copy. It names no action because there is none inside the
 * plugin: Steam moved what this version of Tender looks for, so the repair is
 * a newer Tender.
 */
export const NOTIFICATIONS_UNAVAILABLE_NOTICE = {
  title: "Steam notifications unavailable",
  message:
    "Steam has changed, and Tender can't show its notifications right now. Syncs and downloads still work — check " +
    "this panel for their results. Updating Tender should fix it.",
} as const;
