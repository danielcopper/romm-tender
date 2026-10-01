/**
 * The words for a one-of group's regions and for a file's place in it.
 *
 * A one-of group is ONE requirement: a launch opens exactly one of its files,
 * and which one is decided by the console region of the disc. The backend
 * judges the group (`backend/domain/firmware_groups.py`); what is worded here is
 * the region vocabulary it arrives in and the membership a row carries. The
 * group's own sentence is a BIOS state and is worded with the others, in
 * `utils/biosSummary.ts`.
 *
 * Nothing here is about one console. A region the resolver names that this
 * module has no name for is printed in its own spelling, upper-cased, so a group
 * for a system nobody has worded yet still reads as regions rather than as
 * nothing.
 */

import type { OneOfMembership } from "../types";

/** A console region's two names: the full one a sentence states, and the short
 *  one a status note and the game page have room for. */
interface RegionNames {
  full: string;
  short: string;
}

// The resolver's region words, as it spells them. Three, because those are the
// regions it states today; any other arrives through the fallback below.
const REGION_NAMES: Readonly<Record<string, RegionNames>> = {
  "ntsc-j": { full: "Japan (NTSC-J)", short: "Japan" },
  "ntsc-u": { full: "North America (NTSC-U)", short: "North America" },
  pal: { full: "Europe (PAL)", short: "Europe" },
};

const ONE_OF_THESE = "one of these";
const EVERY_REGION = "every region";
const IN_PLACE = "✓ in place";
const MISSING = "✗ missing";

/**
 * Every fixed phrase this module words — what its drift lock searches the
 * components for, built from the same constants the words are. The membership
 * is searched with its separator, because the bare words are a substring of
 * prose that has nothing to do with a group ("None of these versions"); "every
 * region" is ordinary English and is not searched on its own for the same
 * reason.
 */
export const BIOS_GROUP_PHRASES: readonly string[] = [
  ...Object.values(REGION_NAMES).map((names) => names.full),
  `${ONE_OF_THESE} · `,
];

function namesOf(region: string): RegionNames {
  const known = REGION_NAMES[region];
  if (known) return known;
  const spelled = region.toUpperCase();
  return { full: spelled, short: spelled };
}

/** "A", "A and B", "A, B and C" — the order is the caller's. */
function joined(names: readonly string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The regions, by their full names, as one phrase — "Japan (NTSC-J) and Europe (PAL)". */
export function regionsFull(regions: readonly string[]): string {
  return joined(regions.map((region) => namesOf(region).full));
}

/** The regions, by their short names, as one phrase — "Japan and Europe". */
export function regionsShort(regions: readonly string[]): string {
  return joined(regions.map((region) => namesOf(region).short));
}

/**
 * A file's place in a group, in words — "one of these · Japan (NTSC-J)", or
 * "one of these · every region" for a file that serves them all.
 */
export function oneOfWords(membership: OneOfMembership): string {
  const regions = membership.every_region ? EVERY_REGION : regionsFull(membership.regions);
  return `${ONE_OF_THESE} · ${regions}`;
}

/**
 * The line under a row that is an option of the launching emulator's group —
 * its place in the group, then whether it is in place: "one of these · North
 * America (NTSC-U) · ✓ in place". *inPlace* is the row's own verdict; `null`
 * says nothing about it, because the row's mark already says nothing settled.
 */
export function oneOfRowLine(membership: OneOfMembership, inPlace: boolean | null): string {
  const words = oneOfWords(membership);
  if (inPlace === null) return words;
  return `${words} · ${inPlace ? IN_PLACE : MISSING}`;
}
