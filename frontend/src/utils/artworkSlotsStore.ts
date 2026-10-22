/**
 * The ARTWORK tab's answer per shortcut, and the SteamGridDB game it names, shared by the tab and the play row: Refresh
 * Artwork is disabled while the ROM has no SteamGridDB game (#2381 D3), which the play row learns from the lighter
 * `getArtworkGame` read every visit makes, and the tab's read refreshes.
 *
 * Held per appId with the ROM it was read for, so an answer about the version a switch replaced is never taken for the
 * one showing. A read replaces the entry whole; a reader that started earlier and lands later is dropped by its ticket.
 */

import { useEffect, useState } from "react";
import {
  getArtworkGame,
  getArtworkSlots,
  debugLog,
  type ArtworkGame,
  type ArtworkSlots,
  type ArtworkTabSlot,
} from "../api/backend";
import { detach } from "./detach";

interface Entry {
  romId: number;
  /** The last answer, or `null` while none has been read or the read failed. */
  slots: ArtworkSlots | null;
  /** The read's failure message, where the last read failed. */
  failure: string | null;
}

const entries = new Map<number, Entry>();
/** Per appId, the ROM last read and whether it has a SteamGridDB game, from either read. */
const games = new Map<number, { romId: number; sgdbId: number | null }>();
const tickets = new Map<number, number>();
const listeners = new Map<number, Set<() => void>>();

function notify(appId: number): void {
  for (const listener of listeners.get(appId) ?? []) listener();
}

/** Whether *answer* is the game read's answer rather than a failure — or nothing, as an unprimed test endpoint answers. */
function isArtworkGame(answer: unknown): answer is ArtworkGame {
  return typeof answer === "object" && answer !== null && "game" in answer && "superseded" in answer;
}

/**
 * Read which SteamGridDB game *romId*'s artwork comes from, as every visit to its page does, and hold whether it has
 * one; answers the read, or `null` where it failed.
 */
export async function loadArtworkGame(appId: number, romId: number): Promise<ArtworkGame | null> {
  const answer: unknown = await getArtworkGame(romId).catch((e: unknown) => {
    detach(debugLog(`The SteamGridDB game of appId ${appId} could not be read: ${e}`));
    return null;
  });
  if (!isArtworkGame(answer)) return null;
  games.set(appId, { romId, sgdbId: answer.game.sgdb_id });
  notify(appId);
  return answer;
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
  if (isArtworkSlots(answer)) games.set(appId, { romId, sgdbId: answer.game.sgdb_id });
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
  const game = games.get(appId);
  return game !== undefined && game.romId === romId && game.sgdbId === null;
}

/** Re-render on every change to what is held for *appId*. */
export function useArtworkSlotsChanges(appId: number): void {
  const [, setVersion] = useState(0);
  useEffect(() => {
    const listener = () => setVersion((v) => v + 1);
    const set = listeners.get(appId) ?? new Set();
    set.add(listener);
    listeners.set(appId, set);
    return () => {
      set.delete(listener);
    };
  }, [appId]);
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

// Steam keeps a slot's URL — and, for the icon, its cache-buster — when an image
// is set, so a thumbnail would keep showing the old image from the renderer's
// cache. Each write the page makes moves this token, which every thumbnail URL
// carries; the session's start keeps one session's tokens from another's.
const sessionStart = Date.now();
const written = new Map<number, number>();

/** Note that an image of shortcut *appId* was just set, so its thumbnails load anew. */
export function noteArtworkWritten(appId: number): void {
  written.set(appId, (written.get(appId) ?? 0) + 1);
  notify(appId);
}

/** The cache-buster the thumbnails of shortcut *appId* carry; it moves with every image the page sets. */
export function artworkThumbToken(appId: number): string {
  return `${sessionStart}.${written.get(appId) ?? 0}`;
}

// How long after an ARTWORK tab press a remount of the game page opens on the
// tab again: Steam re-renders the page when artwork changes, which mounts
// Tender's section afresh, and the write may land after the press returned.
const KEEP_TAB_MS = 15_000;
const keepTabUntil = new Map<number, { until: number; slot: ArtworkTabSlot }>();

/** Have a remount of shortcut *appId*'s game page open on the ARTWORK tab, focused on *slot*, for a while after a press. */
export function keepArtworkTabOpen(appId: number, slot: ArtworkTabSlot): void {
  keepTabUntil.set(appId, { until: Date.now() + KEEP_TAB_MS, slot });
}

/** The slot a remount of shortcut *appId*'s game page opens the ARTWORK tab on; `null` where it opens as usual. */
export function keptArtworkSlot(appId: number): ArtworkTabSlot | null {
  const kept = keepTabUntil.get(appId);
  return kept !== undefined && Date.now() < kept.until ? kept.slot : null;
}

export function _resetArtworkSlotsForTests(): void {
  entries.clear();
  games.clear();
  tickets.clear();
  listeners.clear();
  written.clear();
  keepTabUntil.clear();
}
