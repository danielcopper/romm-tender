/**
 * The bookkeeping behind the Library page's optimistic writes: which write to a
 * control or a line is the latest, and what a control is known to be stored
 * as. The rule it serves is `docs/architecture/qam-panel.md` § Library, "Only
 * the latest write speaks"; the hooks decide what an answer shows.
 */

import { useState } from "react";

/** Numbers the writes issued to each target, a control or a line. */
export interface WriteSequence {
  /** Number a new write to *target* and return its number. */
  issue: (target: string) => number;
  isLatest: (target: string, seq: number) => boolean;
  /** The number of the latest write issued to *target*, 0 before any. */
  latest: (target: string) => number;
}

export function createWriteSequence(): WriteSequence {
  const latest = new Map<string, number>();
  return {
    issue: (target) => {
      const seq = (latest.get(target) ?? 0) + 1;
      latest.set(target, seq);
      return seq;
    },
    isLatest: (target, seq) => latest.get(target) === seq,
    latest: (target) => latest.get(target) ?? 0,
  };
}

/** One write of one value to one or more controls, from issue to answer. */
export interface ValueWrite {
  /** Whether this write is still the latest issued to *target* — false for a
   *  target it did not write. */
  isLatest: (target: string) => boolean;
  /** The write was stored: every control it wrote is now stored as its value. */
  stored: () => void;
}

/** What each control is known to be stored as, and its writes numbered. */
export interface LatestWrites<V> {
  /** Take what a read found as stored, in place of everything held. Write
   *  numbers are kept, so an answer still out stays ordered against later ones. */
  seed: (entries: Iterable<readonly [string, V]>) => void;
  /** Record *value* as stored for *target*, as a read that no write has
   *  passed found it. */
  confirm: (target: string, value: V) => void;
  /** The value last confirmed as stored for *target*, or `undefined` before
   *  anything was. */
  stored: (target: string) => V | undefined;
  issue: (targets: readonly string[], value: V) => ValueWrite;
  latest: (target: string) => number;
}

export function createLatestWrites<V>(): LatestWrites<V> {
  let confirmed = new Map<string, V>();
  const writes = createWriteSequence();
  return {
    seed: (entries) => {
      confirmed = new Map(entries);
    },
    confirm: (target, value) => {
      confirmed.set(target, value);
    },
    stored: (target) => confirmed.get(target),
    issue: (targets, value) => {
      const seqs = new Map(targets.map((target) => [target, writes.issue(target)]));
      return {
        isLatest: (target) => {
          const seq = seqs.get(target);
          return seq !== undefined && writes.isLatest(target, seq);
        },
        stored: () => {
          for (const target of seqs.keys()) confirmed.set(target, value);
        },
      };
    },
    latest: writes.latest,
  };
}

/** A {@link WriteSequence} kept for the component's lifetime. */
export function useWriteSequence(): WriteSequence {
  return useState(createWriteSequence)[0];
}

/** A {@link LatestWrites} kept for the component's lifetime. */
export function useLatestWrites<V>(): LatestWrites<V> {
  return useState(() => createLatestWrites<V>())[0];
}
