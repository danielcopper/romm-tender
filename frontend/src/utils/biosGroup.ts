/**
 * The words for a one-of group's regions, a file's place in it, its block on
 * the game page, and the need a row states under its name.
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

import type { FirmwareChecked, FirmwareWanted, OneOfGroupVerdict, OneOfMembership } from "../types";
import { joined } from "./formatters";

// The resolver's region words, as it spells them. Three, because those are the
// regions it states today; any other arrives through the fallback below.
const REGION_NAMES: Readonly<Record<string, string>> = {
  "ntsc-j": "Japan",
  "ntsc-u": "USA",
  pal: "Europe",
};

const EVERY_REGION = "every region";
const FOR_EVERY_REGION = `for ${EVERY_REGION}`;
const IN_PLACE = "in place";
const MISSING = "missing";
const WRONG_FILE = "wrong file";
const REFUSED = "refused";
const NOT_CHECKED = "not checked";
const THIS_GAMES_REGION = "← this game's region";
const ONE_IMAGE_PER_DISC_REGION = "one image per disc region";
const REQUIRED = "required";
const OPTIONAL = "optional";
const NOT_REQUIRED = "not required";
const NEED_NOT_ESTABLISHED = "need not established";
const ONE_OF_THESE = "one of these ·";

/**
 * Every fixed phrase this module words that is particular to it — what its
 * drift lock searches the components for, built from the same constants the
 * words are. The region names and "in place" / "missing" are ordinary words
 * other prose uses, so they are not searched.
 */
export const BIOS_GROUP_PHRASES: readonly string[] = [
  THIS_GAMES_REGION,
  ONE_IMAGE_PER_DISC_REGION,
  NEED_NOT_ESTABLISHED,
  ONE_OF_THESE,
];

function nameOf(region: string): string {
  return REGION_NAMES[region] ?? region.toUpperCase();
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

/** The row fields a need is read off — both pages' row types satisfy it. */
export interface NeedRow {
  wanted: FirmwareWanted;
  required_by_active: boolean;
  one_of?: OneOfMembership | null;
}

/**
 * What the shown emulator needs of a row, for the small line under its name:
 * "required", "optional", "one of these · for USA discs" for an option of its
 * one-of group, and for a library file no emulator declares "not required"
 * where the launching emulator was read and "need not established" where
 * nothing could answer for the file. Both pages word it here for whichever
 * emulator is shown, so a met required file and a met optional file never read
 * alike. Whether the file is in place is never in it: the row's
 * mark or dot, its Contents words and its note say that. The marks' tooltips
 * keep their own words.
 */
export function needWords(row: NeedRow): string {
  if (row.required_by_active) return REQUIRED;
  if (row.one_of) return `${ONE_OF_THESE} ${forDiscs(row.one_of)}`;
  if (row.wanted === "unknown") return NEED_NOT_ESTABLISHED;
  if (row.wanted === "not_needed") return NOT_REQUIRED;
  return OPTIONAL;
}

/**
 * What a file whose requirement is not met IS, by the resolver's check of its
 * bytes: "wrong file" where it is there and is not the image, "refused"
 * where the emulator will not open it, and "missing" only where nothing settled
 * it is there — so a mark never contradicts the Contents words beside it.
 */
export function unmetWord(checked: FirmwareChecked | null | undefined): string {
  if (checked === "mismatch" || checked === "rejected") return WRONG_FILE;
  if (checked === "refused") return REFUSED;
  return MISSING;
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
 * game's region" on the option of the game's own region. The lines keep the
 * order the group states them in, the game's included: the mark alone says
 * which is this game's. A group of one image serving every region is that line
 * alone under the same subheading.
 *
 * *named* is the emulator's label, or the role where the pick has none.
 * *checkedOf* is the resolver's check of an option's file, from its row, which
 * words an image that is not met ({@link unmetWord}).
 */
export function groupBlock(
  group: OneOfGroupVerdict,
  named: string,
  checkedOf: (fileName: string) => FirmwareChecked | null | undefined = () => null,
): GroupBlock {
  const forTheGame = (option: { regions: readonly string[] }) =>
    option.regions.some((region) => group.game_regions.includes(region));
  const lines = group.options.map((option): GroupBlockLine => {
    const regions = servesEveryRegion(option, group) ? EVERY_REGION : regionNames(option.regions);
    const mark = forTheGame(option) ? ` ${THIS_GAMES_REGION}` : "";
    const text = `${regions} · ${option.file_name} · ${stateWord(option.satisfied, checkedOf(option.file_name))}${mark}`;
    return { text, tone: toneOf(option, group) };
  });
  return { heading: `${named} · ${ONE_IMAGE_PER_DISC_REGION}`, lines };
}

function stateWord(satisfied: boolean | null, checked: FirmwareChecked | null | undefined): string {
  if (satisfied === null) return NOT_CHECKED;
  return satisfied ? IN_PLACE : unmetWord(checked);
}

function toneOf(
  option: { regions: readonly string[]; satisfied: boolean | null },
  group: OneOfGroupVerdict,
): GroupLineTone {
  if (option.satisfied === true) return "here";
  if (option.satisfied === null) return "unchecked";
  return option.regions.some((region) => group.missing.includes(region)) ? "missing" : "other";
}
