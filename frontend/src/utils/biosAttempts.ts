/**
 * What a BIOS press on a game's page did to the rows of its BIOS tab: a link it
 * could not place, a file it moved out of the store before the download, and
 * the file "Replace…" moved aside or why it could not. A download replaces
 * every link failure and moved file an earlier press left; a "Replace…" that
 * succeeds replaces every link failure, and one that fails only its own row's.
 * What a "Replace…" said on its row stays until the page is left or another
 * "Replace…" on that row says otherwise. Forgotten when the page is left; never
 * recorded, and never a toast (`docs/architecture/qam-panel.md`, the game
 * page's BIOS tab).
 */

import { useSyncExternalStore } from "react";

import type { BiosLinkFailure } from "../types";
import type { RowAttempt } from "./biosPlaceWords";

type Attempts = Readonly<Record<string, RowAttempt>>;

const NONE: Attempts = {};

let held: Attempts = NONE;
const listeners = new Set<() => void>();

function publish(attempts: Attempts): void {
  held = attempts;
  for (const listener of listeners) listener();
}

/**
 * What a download said about the rows: each link it could not place, by its
 * place, and each file it moved out of the store, by the file's name in RomM.
 * Replaces what an earlier download on the page said, as the latest attempt
 * is the one a row shows; what a "Replace…" said stays until the page is left.
 */
export function noteBiosPress(result: {
  link_failures?: readonly BiosLinkFailure[];
  backups?: readonly string[];
}): void {
  const attempts: Record<string, RowAttempt> = {};
  for (const [key, attempt] of Object.entries(held)) {
    const replaced = {
      ...(attempt.replacedTo === undefined ? {} : { replacedTo: attempt.replacedTo }),
      ...(attempt.replaceFailure === undefined ? {} : { replaceFailure: attempt.replaceFailure }),
    };
    if (Object.keys(replaced).length > 0) attempts[key] = replaced;
  }
  for (const failure of result.link_failures ?? []) {
    attempts[`place:${failure.place}`] = { ...attempts[`place:${failure.place}`], linkFailure: failure.reason };
  }
  for (const path of result.backups ?? []) {
    attempts[`file:${path.slice(path.lastIndexOf("/") + 1)}`] = { movedAside: path };
  }
  publish(attempts);
}

/**
 * "Replace…" at *place* moved the old file to *backupPath*, and the links it
 * could not place. Those replace the links an earlier press could not place;
 * what else an earlier press said stays.
 */
export function noteReplaced(place: string, backupPath: string, linkFailures: readonly BiosLinkFailure[]): void {
  const attempts: Record<string, RowAttempt> = {};
  for (const [key, attempt] of Object.entries(held)) {
    const { linkFailure: _replaced, ...rest } = attempt;
    attempts[key] = rest;
  }
  for (const failure of linkFailures) {
    attempts[`place:${failure.place}`] = { ...attempts[`place:${failure.place}`], linkFailure: failure.reason };
  }
  const { replaceFailure: _failed, ...earlier } = attempts[`place:${place}`] ?? {};
  attempts[`place:${place}`] = { ...earlier, replacedTo: backupPath };
  publish(attempts);
}

/** "Replace…" at *place* was refused or failed, for *reason*. */
export function noteReplaceFailed(place: string, reason: string): void {
  const { replacedTo: _replaced, linkFailure: _linkFailed, ...earlier } = held[`place:${place}`] ?? {};
  publish({ ...held, [`place:${place}`]: { ...earlier, replaceFailure: reason } });
}

/** Forget every attempt — the page that made them is left. */
export function forgetBiosPresses(): void {
  publish(NONE);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The attempt a row shows, read off its place and its file's name in RomM. */
export function useBiosPresses(): (place: string | null, fileName: string | null) => RowAttempt {
  const attempts = useSyncExternalStore(subscribe, () => held);
  return (place, fileName) => ({
    ...(fileName !== null ? attempts[`file:${fileName}`] : undefined),
    ...(place !== null ? attempts[`place:${place}`] : undefined),
  });
}
