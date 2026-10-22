/**
 * What a BIOS press on a game's page did to the rows of its BIOS tab: a link it
 * could not place, a file it moved out of the store before the download, and
 * the file "Replace…" moved aside. Kept for the page that made the press and
 * forgotten when that page is left; never recorded, and never a toast
 * (`docs/architecture/qam-panel.md`, the game page's BIOS tab).
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
 * Replaces what an earlier press on the page said, as the latest attempt is
 * the one a row shows.
 */
export function noteBiosPress(result: {
  link_failures?: readonly BiosLinkFailure[];
  backups?: readonly string[];
}): void {
  const attempts: Record<string, RowAttempt> = {};
  for (const failure of result.link_failures ?? []) {
    attempts[`place:${failure.place}`] = { linkFailure: failure.reason };
  }
  for (const path of result.backups ?? []) {
    attempts[`file:${path.slice(path.lastIndexOf("/") + 1)}`] = { movedAside: path };
  }
  publish(attempts);
}

/** "Replace…" at *place* moved the old file to *backupPath*. */
export function noteReplaced(place: string, backupPath: string): void {
  publish({ ...held, [`place:${place}`]: { ...held[`place:${place}`], replacedTo: backupPath } });
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
