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

/**
 * Ask every entry *entries* holds that can be asked at start-up — the dev
 * build's only where *desktopSurface* says the build carries it.
 */
export function checkSteamInternals(
  entries: readonly SteamInternal[],
  { desktopSurface, read = readGlobalPath }: { readonly desktopSurface: boolean; readonly read?: ReadPath },
): InternalsReport {
  const groups = new Map<string, { segments: readonly string[]; call: boolean; entries: SteamInternal[] }>();
  for (const entry of entries) {
    if (typeof entry.asked !== "string") continue;
    if (entry.devBuildOnly === true && !desktopSurface) continue;
    const segments = staticPart(entry.path);
    const key = segments.join(".");
    const group = groups.get(key) ?? { segments, call: false, entries: [] };
    group.call ||= entry.asked === "call";
    group.entries.push(entry);
    groups.set(key, group);
  }

  const missed: MissedName[] = [];
  for (const [name, group] of groups) {
    const found = read(group.segments);
    const why = !found.there ? "absent" : group.call && typeof found.value !== "function" ? "not a function" : null;
    if (why === null) continue;
    const absenceCost =
      COST_ORDER.find((cost) => group.entries.some((entry) => entry.absenceCost === cost)) ?? "diagnostic";
    const lost = new Set<SteamFeature>(group.entries.flatMap((entry) => entry.features));
    const features = (Object.keys(STEAM_FEATURES) as SteamFeature[]).filter((feature) => lost.has(feature));
    missed.push({ name, absenceCost, features, why });
  }
  return { missed, asked: groups.size };
}
