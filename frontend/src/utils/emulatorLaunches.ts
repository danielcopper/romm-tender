/**
 * The emulator menu's per-entry answers: whether each entry can start the
 * installed game, asked when the menu opens (#2195 D22, D31).
 *
 * The menu opens at once with every entry choosable, and the rows wait together
 * on a spinner for the one request that answers them all. The answers are bound to the ROM they were asked
 * for: one that lands after the page moved to another version is dropped, and
 * one that lands after the menu closed reaches nothing, because nothing
 * listens any more.
 */

import { getEmulatorLaunches, logError } from "../api/backend";
import type { GameLaunch } from "../types";

/** One menu's answers, read once the one request lands. */
export interface EntryLaunches {
  /** The entry's verdict; `undefined` while the answer is out, `null` where it holds none for the entry. */
  forLabel(label: string): GameLaunch | null | undefined;
  /** Call `listener` when the answer lands; the returned function stops it. */
  subscribe(listener: () => void): () => void;
}

/**
 * Ask, for the ROM `romId`, whether each emulator entry can start it.
 * `stillAbout` says whether the menu is still about that ROM when the answer
 * lands. A read that fails answers nothing for any entry, which ends every
 * spinner without a line.
 */
export function askEntryLaunches(
  romId: number,
  stillAbout: () => boolean,
  read: (romId: number) => Promise<GameLaunch[]> = getEmulatorLaunches,
): EntryLaunches {
  let answers: ReadonlyMap<string, GameLaunch> | null = null;
  const listeners = new Set<() => void>();
  const land = (list: readonly GameLaunch[]): void => {
    if (!stillAbout()) return;
    answers = new Map(list.map((launch) => [launch.entry, launch] as const));
    for (const listener of listeners) listener();
  };
  read(romId)
    .then(land)
    .catch((e: unknown) => {
      logError(`emulator menu: launch answers for rom_id=${romId} failed: ${e}`);
      land([]);
    });
  return {
    forLabel: (label) => (answers === null ? undefined : (answers.get(label) ?? null)),
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
