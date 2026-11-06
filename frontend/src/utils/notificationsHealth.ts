/**
 * Whether Tender can raise a toast at all.
 *
 * The value is written once, from the start-up check's report, before anything
 * mounts, and read by what would raise a toast that cannot be shown. What the
 * reader is told is the notice on Main for every start-up miss
 * (`startupMissNotice.ts`).
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
