/**
 * Whether Tender can raise a toast at all, and the notice that says so.
 *
 * The value is written once, from the start-up check's report, and read from
 * render. Why nothing subscribes and nothing polls is on the docs page
 * (`docs/architecture/qam-panel.md`, "Notices and homes").
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

/** Put the module back to what a fresh process holds. */
export function resetNotificationsHealthForTests(): void {
  unavailable = false;
}

/**
 * The notice's copy. It names no action because there is none inside the
 * plugin.
 */
export const NOTIFICATIONS_UNAVAILABLE_NOTICE = {
  title: "Tender's notifications unavailable",
  message:
    "Steam has changed, and Tender can't show its notifications right now. Syncs and downloads still work — check " +
    "this panel for their results. An update should fix it.",
} as const;
