/**
 * What the resolver knows a library file's content as — asked for the rows of
 * the library's own section, after the page is shown, and never as part of it.
 *
 * The question is slow for each file (`identify_firmware`), so it is asked one
 * file at a time, and each row reads "Checking…" until its
 * answer arrives; only the row whose question is out shows the throbber. An answer is kept by MD5 for the page that asked, at most
 * thirty minutes, and forgotten when that page is left; the questions still out
 * then are not asked. The answer only labels a row: it decides no verdict and no
 * badge.
 */

import { useEffect, useState } from "react";

import { identifyLibraryFirmware, logWarn } from "../api/backend";
import { detach } from "./detach";
import type { Identification } from "./biosPlaceWords";

/** How long an answer is kept, at most. */
export const IDENTIFICATION_TTL_MS = 30 * 60 * 1000;

interface Held {
  known: string[] | null;
  at: number;
}

const held = new Map<string, Held>();

/** Forget every answer — the page that asked them is left. */
export function forgetIdentifications(): void {
  held.clear();
}

/** The answer held for *md5* at *now*, or `undefined` where none is, or it is older than the limit. */
function heldAnswer(md5: string, now: number): Identification | undefined {
  const answer = held.get(md5);
  if (answer === undefined || now - answer.at > IDENTIFICATION_TTL_MS) return undefined;
  return answer.known ?? "unknown";
}

export interface Identifications {
  answers: Record<string, Identification>;
  /** The checksum whose question is out right now, or `null` between and after them. */
  asking: string | null;
}

/**
 * The identification of each of *md5s*, asked one at a time after the first
 * render. A checksum not answered yet reads `"checking"`; one nothing could be
 * asked about, `"unknown"`, which a row words as nothing at all.
 */
export function useIdentifications(md5s: readonly string[]): Identifications {
  const key = md5s.join(",");
  const [answers, setAnswers] = useState<Record<string, Identification>>({});
  const [asking, setAsking] = useState<string | null>(null);
  useEffect(() => {
    const visit = { alive: true };
    // Asked after each await: the page may have been left while a question was out.
    const left = () => !visit.alive;
    const wanted = key ? key.split(",") : [];
    const now = Date.now();
    const initial: Record<string, Identification> = {};
    for (const md5 of wanted) initial[md5] = heldAnswer(md5, now) ?? "checking";
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the answers held from before are known at mount and the rest are asked below
    setAnswers(initial);
    const ask = async () => {
      for (const md5 of wanted) {
        if (left()) return;
        if (initial[md5] !== "checking") continue;
        setAsking(md5);
        let known: string[] | null = null;
        try {
          const result = await identifyLibraryFirmware(md5);
          known = result.success ? result.known_as : null;
        } catch (e) {
          logWarn(`Failed to identify a library file: ${e}`);
        }
        if (left()) return;
        held.set(md5, { known, at: Date.now() });
        setAnswers((prev) => ({ ...prev, [md5]: known ?? "unknown" }));
      }
      if (!left()) setAsking(null);
    };
    detach(ask());
    return () => {
      visit.alive = false;
    };
  }, [key]);
  useEffect(() => () => forgetIdentifications(), []);
  return { answers, asking };
}
