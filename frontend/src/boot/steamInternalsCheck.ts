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
  /** The group of alternatives the part's entries belong to, if any. */
  readonly oneOf: string | undefined;
  call: boolean;
  readonly entries: SteamInternal[];
}

/** A group of alternatives as the check settles it. */
interface Alternatives {
  /** An alternative that is there, or one nothing can ask at start-up, with how it stands. */
  standing: string | undefined;
  readonly missed: { readonly name: string; readonly why: string; readonly entries: readonly SteamInternal[] }[];
}

/** One static part's answer: why it missed, or `null` where it is there. */
function askPart(part: StaticPart, read: ReadPath): string | null {
  const found = read(part.segments);
  if (!found.there) return "absent";
  return part.call && typeof found.value !== "function" ? "not a function" : null;
}

/**
 * A group's misses (#2509 D13): none where nothing in it missed; each missing
 * member under its own name, logged but raising no notice, where an alternative
 * stands; and the group once, naming every alternative, where none does.
 */
function settle(group: Alternatives): MissedName[] {
  if (group.missed.length === 0) return [];
  const { standing } = group;
  if (standing !== undefined) {
    return group.missed.map((member) => ({
      name: member.name,
      ...costOf(member.entries),
      why: `${member.why}; ${standing}`,
      noticed: false,
    }));
  }
  return [
    {
      name: group.missed.map((member) => member.name).join(" or "),
      ...costOf(group.missed.flatMap((member) => member.entries)),
      why: [...new Set(group.missed.map((member) => member.why))].join(", "),
    },
  ];
}

/** The static parts the check asks, and why each group's unaskable alternative stands in. */
interface Asked {
  readonly parts: ReadonlyMap<string, StaticPart>;
  readonly unaskedAlternatives: ReadonlyMap<string, string>;
}

/**
 * Sort *entries* into the static parts the check asks, keyed by group and
 * static part so an entry outside a group is never pulled into one, and note
 * the groups an alternative that cannot be asked stands in for.
 */
function collectParts(entries: readonly SteamInternal[], desktopSurface: boolean): Asked {
  const parts = new Map<string, StaticPart>();
  const unaskedAlternatives = new Map<string, string>();
  for (const entry of entries) {
    if (entry.devBuildOnly === true && !desktopSurface) continue;
    if (typeof entry.asked !== "string") {
      if (entry.oneOf !== undefined && !unaskedAlternatives.has(entry.oneOf)) {
        unaskedAlternatives.set(entry.oneOf, `its alternative ${entry.path} cannot be asked at start-up`);
      }
      continue;
    }
    const segments = staticPart(entry.path);
    const key = `${entry.oneOf ?? ""}\u0000${segments.join(".")}`;
    const part = parts.get(key) ?? { segments, oneOf: entry.oneOf, call: false, entries: [] };
    part.call ||= entry.asked === "call";
    part.entries.push(entry);
    parts.set(key, part);
  }
  return { parts, unaskedAlternatives };
}

/** Record one grouped part's answer on its group. */
function answerInGroup(group: Alternatives, part: StaticPart, why: string | null): void {
  const name = part.segments.join(".");
  if (why === null) group.standing ??= `its alternative ${name} is there`;
  else group.missed.push({ name, why, entries: part.entries });
}

/**
 * Ask every part, in the order the list first names it: an ungrouped part
 * that missed in its own place, a group of alternatives in its first member's,
 * to be settled once every part is asked.
 */
function askParts({ parts, unaskedAlternatives }: Asked, read: ReadPath): (MissedName | Alternatives)[] {
  const order: (MissedName | Alternatives)[] = [];
  const alternatives = new Map<string, Alternatives>();
  for (const part of parts.values()) {
    const why = askPart(part, read);
    if (part.oneOf === undefined) {
      if (why !== null) order.push({ name: part.segments.join("."), ...costOf(part.entries), why });
      continue;
    }
    let group = alternatives.get(part.oneOf);
    if (group === undefined) {
      group = { standing: unaskedAlternatives.get(part.oneOf), missed: [] };
      alternatives.set(part.oneOf, group);
      order.push(group);
    }
    answerInGroup(group, part, why);
  }
  return order;
}

/**
 * Ask every entry *entries* holds that can be asked at start-up — the dev
 * build's only where *desktopSurface* says the build carries it.
 *
 * Entries that are one of a group of alternatives (`oneOf`) are settled by
 * {@link settle}; an entry outside a group is never pulled into one, even on a
 * static part a group's entry shares.
 */
export function checkSteamInternals(
  entries: readonly SteamInternal[],
  { desktopSurface, read = readGlobalPath }: { readonly desktopSurface: boolean; readonly read?: ReadPath },
): InternalsReport {
  const asked = collectParts(entries, desktopSurface);
  const missed = askParts(asked, read).flatMap((item): MissedName[] => ("missed" in item ? settle(item) : [item]));
  return { missed, asked: asked.parts.size };
}
