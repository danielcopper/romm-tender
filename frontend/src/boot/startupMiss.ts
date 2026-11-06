/**
 * What every start-up check that missed something says: one warning line for
 * the backend log, and the losses the notice on Main names.
 *
 * Both checks — the searches into Steam's module registry (`steamModules.ts`)
 * and the Steam internals (`steamInternalsCheck.ts`) — word a miss here, so a
 * bug report carries either in the same shape: which check it was, then every
 * missing name with its absence cost and what it costs the reader (#2455 D1).
 */

import type { AbsenceCost } from "./steamModules";
import { STEAM_FEATURES, type SteamFeature } from "./steamInternals";

/** One name a check missed. */
export interface MissedName {
  readonly name: string;
  readonly absenceCost: AbsenceCost;
  readonly features: readonly SteamFeature[];
  /** Why it counts as missing, where there is more than one way to be. */
  readonly why?: string;
  /**
   * `false` for a miss that is logged and raises no notice: one alternative of
   * a group missing while another stands in for it (#2509 D13).
   */
  readonly noticed?: false;
}

/** The misses that reach the notice on Main: all but those logged alone. */
export const noticedMisses = (missed: readonly MissedName[]): MissedName[] =>
  missed.filter((name) => name.noticed !== false);

const describeName = (missed: MissedName): string => {
  const labels = missed.features.map((feature) => STEAM_FEATURES[feature]);
  const cost = labels.length > 0 ? `${missed.absenceCost}: ${labels.join(", ")}` : missed.absenceCost;
  return `${missed.name} (${missed.why === undefined ? cost : `${cost}; ${missed.why}`})`;
};

/**
 * The warning line for one check: its name, how many of how many it asked
 * missed, and every missing name with its cost. Empty when nothing missed.
 * *asked* is `null` where a sentence following the line states the count
 * already, so the line does not say it twice.
 */
export function startupMissLine(check: string, missed: readonly MissedName[], asked: number | null): string {
  if (missed.length === 0) return "";
  const count = asked === null ? "" : `${missed.length} of ${asked} `;
  return `start-up check "${check}": ${count}missed — ${missed.map(describeName).join(", ")}.`;
}

/**
 * The losses a miss the panel survives costs the reader, as the notice words
 * them: each once, in {@link STEAM_FEATURES}'s order. Only a `feature` or an
 * `appearance` miss names any, which both lists' tests hold; a `panel` one never
 * reaches the notice — the fallback page stands in the panel's place instead.
 */
export function noticeFeatures(missed: readonly MissedName[]): string[] {
  const lost = new Set<SteamFeature>(missed.flatMap((name) => name.features));
  return (Object.keys(STEAM_FEATURES) as SteamFeature[])
    .filter((feature) => lost.has(feature))
    .map((feature) => STEAM_FEATURES[feature]);
}
