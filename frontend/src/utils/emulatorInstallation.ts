/**
 * The words for an emulator entry whose installation could not be
 * established. The backend sends the note as a bakeable entry's `reason`, one
 * of Tender's own words (`domain/emulator_commands.py`, the `NOT_ESTABLISHED*`
 * constants, held equal to the map below by
 * `tests/domain/test_emulator_commands.py`). Such an entry stays a pick a user
 * may make, never the default by itself, and is never called missing.
 */

import type { EmulatorOption } from "../types";
import { entryForLabel } from "./emulatorEntries";

/** Each not-established reason, and the cause it words — `null` where the backend knows none. */
const CAUSES: ReadonlyMap<string, string | null> = new Map([
  ["not_established", null],
  ["no_find_rule", "ES-DE has no rule for finding it"],
  ["find_rules_unread", "ES-DE's find rules could not be read"],
  ["lookup_unsupported", "Tender cannot follow ES-DE's lookup for it yet"],
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

/** The short note a surface puts beside such an entry, or `null` where `reason` is not a not-established word. */
export function notEstablishedNote(reason: string | null): string | null {
  if (!isNotEstablished(reason)) return null;
  const cause = notEstablishedCause(reason);
  return cause === null ? "installation not established" : `installation not established: ${cause}`;
}

/** The sentence a page shows under the emulator its games launch with, where that emulator's installation could not
 *  be established; `null` otherwise. */
export function activeEmulatorNote(emulators: readonly EmulatorOption[], activeLabel: string | null): string | null {
  const entry = entryForLabel(emulators, activeLabel);
  if (entry === undefined || !isNotEstablished(entry.reason)) return null;
  const cause = notEstablishedCause(entry.reason);
  const why = cause === null ? "" : `: ${cause}`;
  return `Whether ${entry.name} is installed could not be established${why}.`;
}
