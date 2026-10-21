/**
 * The ARTWORK tab's answer per shortcut, shared by the tab and the play row's Refresh Artwork, which is disabled while
 * the ROM has no SteamGridDB game (#2381 D3).
 *
 * Held per appId with the ROM it was read for, so an answer about the version a switch replaced is never taken for the
 * one showing. A read replaces the entry whole; a reader that started earlier and lands later is dropped by its ticket.
 */

import { useEffect, useState } from "react";
import { getArtworkSlots, debugLog, type ArtworkSlots } from "../api/backend";
import { detach } from "./detach";

interface Entry {
  romId: number;
  /** The last answer, or `null` while none has been read or the read failed. */
  slots: ArtworkSlots | null;
  /** The read's failure message, where the last read failed. */
  failure: string | null;
}

const entries = new Map<number, Entry>();
const tickets = new Map<number, number>();
const listeners = new Map<number, Set<() => void>>();

function notify(appId: number): void {
  for (const listener of listeners.get(appId) ?? []) listener();
}

/** Whether *answer* is the tab's answer rather than a failure — or nothing, as a test's unprimed endpoint answers. */
function isArtworkSlots(answer: unknown): answer is ArtworkSlots {
  return typeof answer === "object" && answer !== null && "slots" in answer && "game" in answer;
}

/** Read *romId*'s slots for shortcut *appId* afresh and hold the answer; answers it, or `null` where it failed. */
export async function loadArtworkSlots(appId: number, romId: number): Promise<ArtworkSlots | null> {
  const ticket = (tickets.get(appId) ?? 0) + 1;
  tickets.set(appId, ticket);
  const answer: unknown = await getArtworkSlots(romId, appId).catch((e: unknown) => {
    detach(debugLog(`Artwork slots for appId ${appId} could not be read: ${e}`));
    return null;
  });
  if (tickets.get(appId) !== ticket) return isArtworkSlots(answer) ? answer : null;
  const failure =
    typeof answer === "object" && answer !== null && "message" in answer && typeof answer.message === "string"
      ? answer.message
      : "The artwork slots could not be read";
  entries.set(
    appId,
    isArtworkSlots(answer) ? { romId, slots: answer, failure: null } : { romId, slots: null, failure },
  );
  notify(appId);
  return isArtworkSlots(answer) ? answer : null;
}

/** What is held for shortcut *appId* about ROM *romId*; nothing for another ROM. */
export function heldArtworkSlots(appId: number, romId: number | null): Entry | null {
  const entry = entries.get(appId);
  return entry && entry.romId === romId ? entry : null;
}

/** Whether ROM *romId* of shortcut *appId* is known to have no SteamGridDB game; `false` while that is not known. */
export function isKnownWithoutSgdbGame(appId: number, romId: number | null): boolean {
  const slots = heldArtworkSlots(appId, romId)?.slots;
  return slots !== null && slots !== undefined && slots.game.sgdb_id === null;
}

/**
 * The held entry for *appId* and *romId*, kept current; read on mount when nothing is held for that ROM yet.
 * Answers `null` until a read lands.
 */
export function useArtworkSlots(appId: number, romId: number | null): Entry | null {
  const [, setVersion] = useState(0);
  useEffect(() => {
    const listener = () => setVersion((v) => v + 1);
    const set = listeners.get(appId) ?? new Set();
    set.add(listener);
    listeners.set(appId, set);
    if (romId && !heldArtworkSlots(appId, romId)) detach(loadArtworkSlots(appId, romId));
    return () => {
      set.delete(listener);
    };
  }, [appId, romId]);
  return heldArtworkSlots(appId, romId);
}

export function _resetArtworkSlotsForTests(): void {
  entries.clear();
  tickets.clear();
  listeners.clear();
}
