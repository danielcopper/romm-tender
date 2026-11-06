/**
 * The start-up check over the Steam internals: is each one the panel reaches
 * still there, and still a function where the code calls it?
 *
 * Each entry of `steamInternals.ts` is asked by its STATIC PART, the path up to
 * its first `()`, `[]` or `(param)`: that is what can be looked up before any
 * call is made, and a member of what a call answers cannot be looked at until
 * the call is (#2509 D7, D8). Each static part is asked once, and a miss is
 * reported once, with the highest cost and every loss of the entries resting on
 * it. What it cannot see, a change in what an internal does, stays a device
 * test.
 *
 * When it asks, and why the panel mounts whatever it answers:
 * `docs/architecture/frontend-bundles.md`, "Every Steam internal Tender
 * reaches".
 */

import type { MissedName } from "./startupMiss";
import { STEAM_FEATURES, type SteamFeature, type SteamInternal } from "./steamInternals";
import type { AbsenceCost } from "./steamModules";

/** What a lookup of one static part found. */
export interface Found {
  /** The last segment is there, every segment before it holding the next. */
  readonly there: boolean;
  readonly value: unknown;
}

/** Look one static part up, segment by segment. */
export type ReadPath = (segments: readonly string[]) => Found;

const ABSENT: Found = { there: false, value: undefined };

/**
 * Look *segments* up from the global scope. A segment is there when the value
 * before it holds it and it is not `undefined`; a getter that throws, or a
 * value that can hold nothing (`null`, a number), makes it absent — `in`
 * throws on such a value, and the throw is the answer.
 */
export const readGlobalPath: ReadPath = (segments) => {
  try {
    let value: unknown = globalThis;
    for (const segment of segments) {
      if (!(segment in (value as object))) return ABSENT;
      value = (value as Record<string, unknown>)[segment];
      if (value === undefined) return ABSENT;
    }
    return { there: true, value };
  } catch {
    return ABSENT;
  }
};

/** The segments of *path* up to its first `()`, `[]` or `(param)`. */
export function staticPart(path: string): readonly string[] {
  const end = path.search(/[([]/);
  return (end === -1 ? path : path.slice(0, end)).split(".");
}

const COST_ORDER: readonly AbsenceCost[] = ["feature", "appearance", "diagnostic"];

/** What the check found. */
export interface InternalsReport {
  /** Each static part that missed, in the order the list first names it. */
  readonly missed: readonly MissedName[];
  /** How many static parts were asked. */
  readonly asked: number;
}

/** The highest cost and every loss of *entries*, in the table's order. */
function costOf(entries: readonly SteamInternal[]): Pick<MissedName, "absenceCost" | "features"> {
  const absenceCost = COST_ORDER.find((cost) => entries.some((entry) => entry.absenceCost === cost)) ?? "diagnostic";
  const lost = new Set<SteamFeature>(entries.flatMap((entry) => entry.features));
  return {
    absenceCost,
    features: (Object.keys(STEAM_FEATURES) as SteamFeature[]).filter((feature) => lost.has(feature)),
  };
}

interface StaticPart {
  readonly segments: readonly string[];
  call: boolean;
  readonly entries: SteamInternal[];
}

/** A group of alternatives as the check settles it. */
interface Alternatives {
  there: boolean;
  readonly names: string[];
  readonly whys: string[];
  readonly entries: SteamInternal[];
}

/** One static part's answer: why it missed, or `null` where it is there. */
function askPart(part: StaticPart, read: ReadPath): string | null {
  const found = read(part.segments);
  if (!found.there) return "absent";
  return part.call && typeof found.value !== "function" ? "not a function" : null;
}

/**
 * Ask every entry *entries* holds that can be asked at start-up — the dev
 * build's only where *desktopSurface* says the build carries it.
 *
 * Entries that are one of a group of alternatives (`oneOf`) miss only together:
 * the group is reported once, naming every alternative, where none of them is
 * there and none is an alternative nothing can ask at start-up.
 */
export function checkSteamInternals(
  entries: readonly SteamInternal[],
  { desktopSurface, read = readGlobalPath }: { readonly desktopSurface: boolean; readonly read?: ReadPath },
): InternalsReport {
  const parts = new Map<string, StaticPart>();
  const unaskedAlternatives = new Set<string>();
  for (const entry of entries) {
    if (entry.devBuildOnly === true && !desktopSurface) continue;
    if (typeof entry.asked !== "string") {
      if (entry.oneOf !== undefined) unaskedAlternatives.add(entry.oneOf);
      continue;
    }
    const segments = staticPart(entry.path);
    const key = segments.join(".");
    const part = parts.get(key) ?? { segments, call: false, entries: [] };
    part.call ||= entry.asked === "call";
    part.entries.push(entry);
    parts.set(key, part);
  }

  // Each miss in the order the list first names it; a group of alternatives
  // holds its place by its first member and is settled once every part is asked.
  const order: (MissedName | Alternatives)[] = [];
  const alternatives = new Map<string, Alternatives>();
  for (const [name, part] of parts) {
    const why = askPart(part, read);
    const oneOf = part.entries.find((entry) => entry.oneOf !== undefined)?.oneOf;
    if (oneOf === undefined) {
      if (why !== null) order.push({ name, ...costOf(part.entries), why });
      continue;
    }
    let group = alternatives.get(oneOf);
    if (group === undefined) {
      group = { there: unaskedAlternatives.has(oneOf), names: [], whys: [], entries: [] };
      alternatives.set(oneOf, group);
      order.push(group);
    }
    if (why === null) group.there = true;
    else {
      group.names.push(name);
      group.whys.push(why);
      group.entries.push(...part.entries);
    }
  }
  const reported = order.flatMap((item): MissedName[] => {
    if (!("there" in item)) return [item];
    if (item.there) return [];
    return [{ name: item.names.join(" or "), ...costOf(item.entries), why: [...new Set(item.whys)].join(", ") }];
  });
  return { missed: reported, asked: parts.size };
}
