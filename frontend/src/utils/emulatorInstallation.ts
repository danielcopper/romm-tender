/**
 * The words for an emulator entry whose installation could not be
 * established. The backend sends the cause as the entry's `reason`, one of
 * Tender's own words (`domain/emulator_commands.py`, the `NOT_ESTABLISHED*`
 * constants); such an entry is neither offered as working nor called missing.
 */

/** Each not-established reason, and the cause it words — `null` where the backend knows none. */
const CAUSES: ReadonlyMap<string, string | null> = new Map([
  ["not_established", null],
  ["no_find_rule", "ES-DE has no rule for finding it"],
  ["find_rules_unread", "ES-DE's find rules could not be read"],
  ["lookup_unsupported", "this setup's emulator lookup cannot be checked yet"],
  ["path_unseen", "ES-DE looks for it where Tender cannot see"],
]);

/** Whether `reason` says the entry's installation could not be established. */
export function isNotEstablished(reason: string | null): boolean {
  return reason !== null && CAUSES.has(reason);
}

/** Why the installation could not be established, or `null` where the reason names no cause or is not one of these. */
export function notEstablishedCause(reason: string | null): string | null {
  return reason === null ? null : (CAUSES.get(reason) ?? null);
}
