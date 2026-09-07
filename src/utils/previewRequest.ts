/**
 * The one-shot request Main leaves for the Sync page: "work out a fresh preview
 * as soon as you open".
 *
 * Main's Sync button starts a preview and opens the Sync page, and the page is
 * what issues the call — so the progress, the answer AND a refusal are all
 * reported where the reader is now looking. Main is unmounted a moment after the
 * press, so anything it reported would be reported to nobody: a
 * `@migration_blocked` refusal, a server that went away between the press and
 * the call, and a rejection all land after the page swap.
 *
 * It is taken exactly once, by the first mount that asks, so coming back to the
 * page later — through Back, or through the Last sync row — recomputes nothing.
 * A request nobody took while the page was closed is honoured by the next mount
 * rather than expiring, which from the reader's side is still one press, one
 * preview.
 */

let _requested = false;

/** Ask the next Sync-page mount to compute a preview. */
export function requestPreviewOnOpen(): void {
  _requested = true;
}

/** Whether a request is standing, clearing it either way. */
export function takePreviewRequest(): boolean {
  const requested = _requested;
  _requested = false;
  return requested;
}

/** Reset the module state between tests. Not for production use. */
export function resetPreviewRequestForTests(): void {
  _requested = false;
}
