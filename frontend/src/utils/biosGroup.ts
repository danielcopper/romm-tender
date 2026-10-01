/**
 * The words for a one-of group's regions, a file's place in it, and its block
 * on the game page.
 *
 * A one-of group is ONE requirement: a launch opens exactly one of its files,
 * and which one is decided by the console region of the disc. The backend
 * judges the group (`backend/domain/firmware_groups.py`); what is worded here is
 * the region vocabulary it arrives in, the membership a row carries, and the
 * lines the game page lists under its headline. The group's own sentence is a
 * BIOS state and is worded with the others, in `utils/biosSummary.ts`.
 *
 * The regions are named the way RomM names a game's region and the way the
 * emulators' BIOS descriptions do ("PS1 US BIOS"): USA, Japan, Europe. Nothing
 * here is about one console. A region the resolver names that this module has no
 * name for is printed in its own spelling, upper-cased, so a group for a system
 * nobody has worded yet still reads as regions rather than as nothing.
 */

import type { OneOfGroupVerdict, OneOfMembership } from "../types";

// The resolver's region words, as it spells them. Three, because those are the
// regions it states today; any other arrives through the fallback below.
const REGION_NAMES: Readonly<Record<string, string>> = {
  "ntsc-j": "Japan",
  "ntsc-u": "USA",
  pal: "Europe",
};

const EVERY_REGION = "every region";
const FOR_EVERY_REGION = `for ${EVERY_REGION}`;
const BOOTS_IT_FOR_EVERY_REGION = `boots it ${FOR_EVERY_REGION}`;
const IN_PLACE = "in place";
const MISSING = "missing";
const NOT_CHECKED = "not checked";
const THIS_GAMES_REGION = "← this game's region";
const ONE_IMAGE_PER_DISC_REGION = "one image per disc region";

/**
 * Every fixed phrase this module words that is particular to it — what its
 * drift lock searches the components for, built from the same constants the
 * words are. The region names and "in place" / "missing" are ordinary words
 * other prose uses, so they are not searched.
 */
export const BIOS_GROUP_PHRASES: readonly string[] = [
  BOOTS_IT_FOR_EVERY_REGION,
  THIS_GAMES_REGION,
  ONE_IMAGE_PER_DISC_REGION,
];

function nameOf(region: string): string {
  return REGION_NAMES[region] ?? region.toUpperCase();
}

/** "A", "A and B", "A, B and C" — the order is the caller's. */
export function joined(names: readonly string[], word = "and"): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} ${word} ${names[names.length - 1]}`;
}

/** The regions as one phrase — "Japan and Europe". */
export function regionNames(regions: readonly string[]): string {
  return joined(regions.map(nameOf));
}

/** Does *option* serve every region its group speaks about? */
export function servesEveryRegion(option: { regions: readonly string[] }, group: OneOfGroupVerdict): boolean {
  return group.regions.length > 0 && group.regions.every((region) => option.regions.includes(region));
}

/** The discs a file is for: "for USA discs", or "for every region". */
function forDiscs(membership: OneOfMembership): string {
  return membership.every_region ? FOR_EVERY_REGION : `for ${regionNames(membership.regions)} discs`;
}

/**
 * A file's place in a group, on the line of the emulator whose group it is —
 * "for USA discs", or "boots it for every region" for a file that serves them
 * all. Only this file's own regions, never the group's others.
 */
export function oneOfWords(membership: OneOfMembership): string {
  return membership.every_region ? BOOTS_IT_FOR_EVERY_REGION : forDiscs(membership);
}

/**
 * The line under a platform-table row that is an option of the launching
 * emulator's group — the discs it is for, then whether it is in place: "for USA
 * discs · ✓ in place". *inPlace* is the row's own verdict; `null` says nothing
 * about it, because the row's mark already says nothing settled.
 */
export function oneOfRowLine(membership: OneOfMembership, inPlace: boolean | null): string {
  const words = forDiscs(membership);
  if (inPlace === null) return words;
  return `${words} · ${inPlace ? `✓ ${IN_PLACE}` : `✗ ${MISSING}`}`;
}

/**
 * How one line of the block stands, for the surface to colour: `here` in place,
 * `missing` shown missing for a region the verdict names as missing, `other`
 * missing for a region that is not this game's, `unchecked` nothing settled.
 */
export type GroupLineTone = "here" | "missing" | "other" | "unchecked";

export interface GroupBlockLine {
  text: string;
  tone: GroupLineTone;
}

/** The block the game page lists under its headline: a subheading, then one line per option. */
export interface GroupBlock {
  heading: string;
  lines: GroupBlockLine[];
}

/**
 * The launching emulator's group as the game page lists it, above the file
 * list: the subheading "Beetle PSX · one image per disc region", then one line
 * per option — its regions, its file, whether it is in place — with "← this
 * game's region" on the option of the game's own region. That option comes
 * first, because it is the one this launch opens; the others follow in the
 * order the group states them. A group of one image serving every region is
 * that line alone under the same subheading.
 *
 * *named* is the emulator's label, or the role where the pick has none.
 */
export function groupBlock(group: OneOfGroupVerdict, named: string): GroupBlock {
  const forTheGame = (option: { regions: readonly string[] }) =>
    option.regions.some((region) => group.game_regions.includes(region));
  const options = [
    ...group.options.filter((option) => forTheGame(option)),
    ...group.options.filter((option) => !forTheGame(option)),
  ];
  const lines = options.map((option): GroupBlockLine => {
    const state = option.satisfied === true ? IN_PLACE : option.satisfied === false ? MISSING : NOT_CHECKED;
    const regions = servesEveryRegion(option, group) ? EVERY_REGION : regionNames(option.regions);
    const text = `${regions} · ${option.file_name} · ${state}${forTheGame(option) ? ` ${THIS_GAMES_REGION}` : ""}`;
    return { text, tone: toneOf(option, group) };
  });
  return { heading: `${named} · ${ONE_IMAGE_PER_DISC_REGION}`, lines };
}

function toneOf(
  option: { regions: readonly string[]; satisfied: boolean | null },
  group: OneOfGroupVerdict,
): GroupLineTone {
  if (option.satisfied === true) return "here";
  if (option.satisfied === null) return "unchecked";
  return option.regions.some((region) => group.missing.includes(region)) ? "missing" : "other";
}
