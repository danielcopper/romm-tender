/**
 * The one place a BIOS state is put into words.
 *
 * Every state and a one-of group's, and before this module each surface worded
 * them for itself: the game page's BIOS tab, the platform pane, and the
 * platform list's tooltip all held their own spelling of them, some naming the
 * launching emulator and some not. Nothing joined them, so the drift was
 * invisible to every test — each surface's own expectations passed while the
 * three said different things about one platform, which is what a reader moving
 * between them saw.
 *
 * **Every claim below is about what the code does today, never about where it is
 * headed**, and that is a rule this file earned the hard way: it named all three
 * surfaces as readers from the first cut, while the list's tooltip was in fact
 * still wording its own — a sentence describing the INTENDED state as the
 * reached one, which then read as evidence that nothing was left to do. The
 * drift lock is the only claim here that checks itself; when the rest of this
 * prose runs ahead of the code, nothing at all says so.
 *
 * So the states are decided here and the sentences are written here, and a
 * surface chooses only WHICH of the two answers it has room for:
 *
 * - `status` is the short coloured note — the platform pane sets it beside
 *   `BIOS FILES`, where the colour comes from the level and this says what the
 *   colour means;
 * - `sentence` is the line itself: what the game page shows, what the pane puts
 *   under its note, and what the platform list's row tooltip carries, since a
 *   tooltip has no heading beside it to give the short note its subject.
 *
 * They are one answer in two lengths and never two answers: a surface showing
 * both shows a heading and its own sentence, never two facts to reconcile.
 *
 * **Every sentence names the emulator**, because every one of these states is
 * about ONE emulator's declaration and nothing else — the pick the whole answer
 * was scoped to, named as its selector entry names it ({@link launchingName}).
 * On the game page the same pick is named two inches away, under the pane's
 * Emulator heading, so a sentence about "the launching emulator" says less than
 * everything around it. The two names can differ: the BIOS part names the
 * emulator, the Emulator heading the launch entry
 * (docs/architecture/core-emulator-selection.md, "The BIOS part names the
 * emulator; the launch places name the entry"). Where the payload carries no label the pick could not
 * be made or has no name of its own, and the sentences fall back to naming the
 * role rather than inventing one.
 *
 * **The colour is never decided here.** It is the backend's level — the
 * resolver's verdict for the launching emulator — and this module
 * only says what that verdict means. **The order of the conditions is
 * load-bearing**: what keeps the verdict from being read at all comes first (an
 * emulator the resolver does not cover, then a BIOS nobody chose in its
 * settings — ahead of the emulator nobody read, since that empty setting is why
 * PCSX2's card reads as not read — then an emulator nobody read, under a
 * verdict nothing established, then a required declaration the resolver would
 * not follow), then the console's own demand where none of the images the
 * emulator knows is in place, with a search that found nothing behind it as a
 * note where both apply,
 * then what a search of the BIOS folder by content found, then a
 * one-of group the launching emulator states, then the level's decline, then
 * the emulator's required files. A group with nothing in place keeps its
 * sentence whatever else is open, because the console does not start either
 * way. Any other group gives way to a plain required file that is open beside
 * it, worded by that file's own state — and both count the plain files alone,
 * never a group as a file. The resolver's notes about the emulator ride behind
 * whichever sentence stands.
 *
 * What this module does NOT hold is the library's own ratio —
 * `(d/t RomM library files)`, which counts a third set and is written next door
 * (`utils/biosHeldRatio.ts`, where why the two are stated side by side rather
 * than folded together lives). Two of the three surfaces append it to every
 * sentence here — the game page and the platform pane — and the list's row
 * tooltip has never carried it.
 */

import type {
  BiosLevel,
  EmulatorAnswer,
  FirmwareChecked,
  FirmwareLocating,
  FirmwareWanted,
  OneOfGroupVerdict,
  RefusedDeclaration,
} from "../types/firmware";
import { foundByContent } from "./biosFileNote";
import { joined, lastPathSegment } from "./formatters";
import { groupBlock, regionNames, servesEveryRegion, type GroupBlock } from "./biosGroup";

/**
 * One state, in the two lengths a surface can have room for. Both are always
 * present: a surface picks, and neither is ever the empty string.
 */
export interface BiosSummary {
  /** The short coloured note — a heading, not a sentence. */
  status: string;
  /** The line that says what the state means. */
  sentence: string;
}

/** The payload fields every state is read off. Both surfaces' own payload types
 *  satisfy it structurally, which is what lets one module serve a `BiosStatus`
 *  and a `FirmwarePlatformExt` without either importing the other. */
export interface BiosSummarySource extends EmulatorAnswer {
  required_count?: number;
  required_downloaded?: number;
  required_withheld?: number;
  one_of_groups?: OneOfGroupVerdict[];
  active_core_label?: string | null;
}

/** The row fields the two count fallbacks and the optional-missing breakdown are
 *  taken over. Both file types carry them. */
export interface BiosSummaryRow {
  wanted?: FirmwareWanted;
  required_by_active?: boolean;
  used_by_active?: boolean;
  downloaded?: boolean;
  satisfied?: boolean | null;
  checked?: FirmwareChecked | null;
  file_name?: string;
  caveats?: string[];
}

/**
 * The launching emulator's name: its selector entry's label, which carries every
 * label the emulator goes by ("LRPS2 / PCSX2"), so the sentences name it as the
 * selector and the rows do. `active_core_label` is the one label that was
 * picked, and stands only where no entry is the launching emulator's.
 */
function launchingName(source: BiosSummarySource): string | null {
  return source.emulator_tabs?.find((tab) => tab.launching)?.label ?? source.active_core_label ?? null;
}

// The role a sentence names where the pick carries no label. Two spellings of
// one fallback, because some sentences open with the emulator and
// the rest name it mid-sentence — and a real label is never recased, since
// `mGBA` is the name its own catalogue spells.
const ROLE_MID = "the launching emulator";
const ROLE_LEADING = "The launching emulator";

// The fixed halves of the sentences. They are constants rather than
// inline literals so `BIOS_SUMMARY_PHRASES` can be built from the same strings
// the sentences are — a drift lock that repeated the list would be checking a
// second copy of it.
const CANNOT_START = "cannot start this system without a BIOS image";
// The console's own demand: the console's name, this, then which of
// the emulator's images is in place.
const CONSOLE_NEEDS_IMAGE = "does not start without a BIOS image";
const NONE_OF_THE = "and none of the";
const KNOWS_IS_IN_PLACE = "knows is in place";
const THE_ONE_IT_KNOWS = "and the one image";
const KNOWS_IS_NOT_IN_PLACE = "knows is not in place";
// What stands in for the console's name where the caller has none to give.
const THIS_SYSTEM = "This system";
// A BIOS nobody chose in the emulator's settings.
const NO_BIOS_CHOSEN_HEAD = "No BIOS is chosen in";
const NO_BIOS_CHOSEN_TAIL = "settings, and no game boots until one is";
// A required declaration the resolver would not follow.
const REFUSED_TAIL = "so whether it is ready could not be established";
// Images that rank alike.
const TIED_TAIL = "is not established";
// How the emulator finds its firmware words what it does with a file:
// one that opens the names it declares "opens by name", one nobody has read the
// code of "lists as required". One that also searches its folder by content
// "requires" them: "opens by name" would be untrue of the search, and what the
// search found is the resolver's to state.
const OPENS_BY_NAME = "opens by name";
const LISTS_AS_REQUIRED = "lists as required";
const VERB_REQUIRES = "requires";
const ARE_IN_PLACE = "are in place";
const IS_IN_PLACE = "is in place";
const IS_NOT_IN_PLACE = "is not in place";
const ARE_NOT_IN_PLACE = "are not in place";
const COULD_NOT_BE_CHECKED_TAIL = "could not be checked";
// How an emulator nobody has read finds its BIOS.
const LOCATING_UNESTABLISHED_TAIL = "finds its BIOS has not been established";
// An emulator that searches its BIOS folder by content.
const RECOGNISES_IN_FOLDER = "in the BIOS folder";
// A settled search that reached no image, in the resolver's own terms: the
// folder, and how many files of a size the emulator accepts it held where the
// search states that count.
const FOUND_NO_IMAGE_IN = "found no BIOS image in";
const OF_A_SIZE = "of a size";
const RECOGNISES_NONE = "accepts, and it recognises none of them";
const RECOGNISES_NOT_IT = "accepts, and it does not recognise it";
const FOLDER_NOT_READ_COMPLETELY = "BIOS folder could not be read completely";
const COULD_NOT_BE_READ = "could not be read";
// An emulator the resolver does not cover.
const NOT_COVERED_HEAD = "Tender cannot tell what";
const NOT_COVERED_TAIL = "needs yet — the emulator's firmware is not covered by the resolver yet";
// The emulator's name and the verb sit between the count and this. A bare
// "could not be checked" is also the platform pane's mark for a row whose own
// verdict was withheld, so the lock searches the runs that start at each verb.
const UNCHECKED_TAIL = "could not be checked";
const IMAGE_UNSETTLED_HEAD = "Whether the BIOS image";
const NOTHING_ESTABLISHED_HEAD = "Nothing could be established about what";
const NOTHING_ESTABLISHED_TAIL = "needs";
const MARKS_NONE_REQUIRED = "marks none of its BIOS files as required";
const OPTIONAL_MISSING_TAIL = "optional missing";
// The one-of group's own sentences. The runs below are the halves no other
// sentence shares; "The BIOS image" opens one of them and is too common a run
// to search for on its own.
const GROUP_IMAGE_HEAD = "The BIOS image";
const GROUP_PARTIAL_HEAD = "has a BIOS image for";
// "for every region" rather than "the image … is in place": a core may open a
// different image for each region (SwanStation, one for Japan and one for USA
// and Europe), so the met state is about the regions and not one file.
const GROUP_MET_TAIL = `${GROUP_PARTIAL_HEAD} every region`;
const GROUP_PARTIAL_TAIL = "discs will not start";
const GROUP_UNCHECKED_TAIL = "needs is in place could not be checked";
const GROUP_GAME_MET = "needs for this game's region";
const GROUP_GAME_UNCHECKED_TAIL = "is in place could not be checked";
const GROUP_GAME_NO_OPTION = "states no BIOS image for this game's region";
const GROUP_GAME_NO_OPTION_TAIL = "so whether it starts could not be established";
const GROUP_GAME_UNMET = "has no BIOS image for this game's region";
const GROUP_GAME_NEEDS = "to start this game";
const GROUP_GAME_STARTS = "starts this game with";
const GROUP_GAME_FOUND = "found in the BIOS folder, it serves every region";
const GROUP_GAME_SERVES = "which serves every region";
const GROUP_ONE_MISSING = "it is missing";
const GROUP_TWO_MISSING = "neither is in place";
const GROUP_MANY_MISSING = "none of them is in place";
const GROUP_NO_IMAGE_FOR = "and there is no BIOS image for";
// The regions a group carries no option for, named after whichever group
// sentence stands wherever no game region narrows the group.
const GROUP_UNSTATED_TAIL = "states no BIOS image, so whether they start could not be established";

const STATUS_NEEDS_IMAGE = "Needs a BIOS image";
const STATUS_NO_BIOS_CHOSEN = "No BIOS chosen";
const STATUS_FOUND_BY_CONTENT = "Found by content";
const STATUS_NO_IMAGE_FOUND = "No BIOS image found";
const STATUS_NONE_RECOGNISED = "No image recognised";
const STATUS_READINESS_UNKNOWN = "Readiness unknown";
const STATUS_REGIONS_NOT_CHECKED = "regions not checked";
const STATUS_REQUIREMENT_UNKNOWN = "Requirement unknown";
const STATUS_NOTHING_REQUIRED = "Nothing required";

/**
 * Every fixed phrase a summary is built from — what the drift lock searches the
 * two surfaces for.
 *
 * It is the same list the sentences above are composed of, not a transcription
 * of them, so a phrase that changes here changes what the lock looks for in the
 * same edit. What it can catch is a summary string written back into a
 * component; what it cannot catch is a component inventing a NEW wording for one
 * of these states, which no string search could see. The interpolated halves are
 * absent by construction — a phrase has to be a fixed run to be searchable at
 * all.
 */
export const BIOS_SUMMARY_PHRASES: readonly string[] = [
  CANNOT_START,
  CONSOLE_NEEDS_IMAGE,
  KNOWS_IS_IN_PLACE,
  KNOWS_IS_NOT_IN_PLACE,
  NO_BIOS_CHOSEN_HEAD,
  NO_BIOS_CHOSEN_TAIL,
  REFUSED_TAIL,
  ...[OPENS_BY_NAME, LISTS_AS_REQUIRED, VERB_REQUIRES].map((verb) => `${verb} ${UNCHECKED_TAIL}`),
  IMAGE_UNSETTLED_HEAD,
  NOTHING_ESTABLISHED_HEAD,
  OPENS_BY_NAME,
  LISTS_AS_REQUIRED,
  LOCATING_UNESTABLISHED_TAIL,
  FOUND_NO_IMAGE_IN,
  RECOGNISES_NONE,
  RECOGNISES_NOT_IT,
  FOLDER_NOT_READ_COMPLETELY,
  NOT_COVERED_HEAD,
  NOT_COVERED_TAIL,
  MARKS_NONE_REQUIRED,
  OPTIONAL_MISSING_TAIL,
  GROUP_MET_TAIL,
  GROUP_PARTIAL_HEAD,
  GROUP_PARTIAL_TAIL,
  GROUP_UNCHECKED_TAIL,
  GROUP_GAME_MET,
  GROUP_GAME_NO_OPTION,
  GROUP_GAME_NO_OPTION_TAIL,
  GROUP_GAME_UNMET,
  GROUP_GAME_NEEDS,
  GROUP_GAME_STARTS,
  GROUP_GAME_FOUND,
  GROUP_GAME_SERVES,
  GROUP_ONE_MISSING,
  GROUP_TWO_MISSING,
  GROUP_MANY_MISSING,
  GROUP_NO_IMAGE_FOR,
  GROUP_UNSTATED_TAIL,
  // The role's two spellings as string literals: a component falling back to
  // the role itself writes one of these, and the role belongs here.
  `"${ROLE_LEADING}"`,
  `"${ROLE_MID}"`,
  STATUS_NEEDS_IMAGE,
  STATUS_NO_BIOS_CHOSEN,
  STATUS_FOUND_BY_CONTENT,
  STATUS_NO_IMAGE_FOUND,
  STATUS_NONE_RECOGNISED,
  STATUS_READINESS_UNKNOWN,
  STATUS_REGIONS_NOT_CHECKED,
  STATUS_REQUIREMENT_UNKNOWN,
  STATUS_NOTHING_REQUIRED,
];

/**
 * Every state, in the order the surfaces read them (see the header).
 *
 * *level* is the backend's own readiness verdict and is taken as an argument
 * rather than off *source*: the game page holds it beside the payload (a
 * requirement nothing could be established for arrives as a placeholder status
 * with the level set separately), so reading it off the payload here would answer
 * a different question on that surface. `null` — a payload from before the field
 * existed — falls back to the count comparison for the one sentence that reads
 * ready or not.
 *
 * *consoleName* is RomM's name for the platform, which every surface already
 * holds, for the console's own demand; without one the sentence names the
 * system as "This system".
 */
export function biosSummary(
  source: BiosSummarySource,
  rows: readonly BiosSummaryRow[],
  level: BiosLevel | null,
  consoleName: string | null = null,
): BiosSummary {
  const words = stateWords(source, rows, level, consoleName);
  const notes = tiedNotes(source);
  return notes.length === 0 ? words : { ...words, sentence: [words.sentence, ...notes].join(" — ") };
}

function stateWords(
  source: BiosSummarySource,
  rows: readonly BiosSummaryRow[],
  level: BiosLevel | null,
  consoleName: string | null,
): BiosSummary {
  const emulator = launchingName(source);
  const named = emulator ?? ROLE_MID;
  const leading = emulator ?? ROLE_LEADING;
  const counts = requiredCounts(source, rows);

  // 1. and 2. The emulator nobody read, and what else keeps the verdict from
  //    being read at all.
  const unread = unreadSummary(source, level, named, leading);
  if (unread) return unread;

  // What a search of the BIOS folder that found nothing states —
  // only where no group speaks instead, and only off the resolver's own
  // statement: never a verdict read as one.
  const nothing = counts.groups.length === 0 ? foundNothingSummary(source, named, leading) : null;

  // 3. The console's own demand, with none of the images the emulator knows in
  //    place — and what the search found after it, where it states that too.
  const demand = consoleDemandSummary(source, rows, level, consoleName ?? THIS_SYSTEM, named, counts.plain, nothing);
  if (demand) return demand;

  // 4. What an emulator that searches its BIOS folder by content found —
  //    stated rather than counted: "1 / 1 required" would be true and useless.
  if (nothing) return nothing;
  const searched = contentSearchSummary(source, rows, level, leading);
  if (searched) return searched;

  // 5. A one-of group the launching emulator states.
  const group = oneOfGroupSummary(source, rows, level, counts.groups, counts.plain, named, leading);
  if (group) return group;

  // 6. The level declined.
  if (level === "unknown") return declinedSummary(named, verbOf(source.locating), counts.withheld);

  // 7. The emulator's own required files, held or short.
  if (counts.count > 0) return requiredFilesSummary(source.locating, named, rows, counts.plain, level);

  // 8. A finished answer, and the set it was taken over is named: a bare
  //    "Nothing required" was read as the CONSOLE needing no BIOS.
  return { status: STATUS_NOTHING_REQUIRED, sentence: `${leading} ${MARKS_NONE_REQUIRED}` };
}

/** The required counts the states read, and the one-of groups they take once each. */
interface RequiredCounts {
  count: number;
  withheld: number;
  groups: readonly OneOfGroupVerdict[];
  plain: PlainRequirements;
}

/** The payload's required counts, each falling back to the rows where the payload states none. */
function requiredCounts(source: BiosSummarySource, rows: readonly BiosSummaryRow[]): RequiredCounts {
  const withheld = source.required_withheld ?? 0;
  const requiredRows = rows.filter((row) => row.required_by_active);
  const count = source.required_count ?? requiredRows.length;
  const done = source.required_downloaded ?? requiredRows.filter((row) => row.downloaded).length;
  const groups = source.one_of_groups ?? [];
  return { count, withheld, groups, plain: plainRequirements(groups, count, done, withheld) };
}

/** What keeps the verdict from being read at all, in the header's order; `null` where nothing does. */
function unreadSummary(
  source: BiosSummarySource,
  level: BiosLevel | null,
  named: string,
  leading: string,
): BiosSummary | null {
  // 1. The emulator nobody read: nothing that follows can be about it. One the
  //    resolver does not cover says so.
  if (source.emulator_declaration === "unsupported") {
    return { status: STATUS_REQUIREMENT_UNKNOWN, sentence: `${NOT_COVERED_HEAD} ${named} ${NOT_COVERED_TAIL}` };
  }
  // A BIOS nobody chose in the emulator's settings is said ahead of "not read":
  // an empty setting is why PCSX2's card establishes nothing, so the reading
  // that counts it as not read is the same answer that names the cause.
  if ((source.emulator_notes ?? []).some((note) => note.kind === "bios-unchosen")) {
    return { status: STATUS_NO_BIOS_CHOSEN, sentence: `${NO_BIOS_CHOSEN_HEAD} ${named}'s ${NO_BIOS_CHOSEN_TAIL}` };
  }
  // Not read says nothing was established, which a verdict the resolver did
  // state contradicts: DuckStation over a folder its search found nothing in
  // declares no file and is read as unread, while its verdict is not met.
  if (source.emulator_read === false && (level === null || level === "unknown")) return nothingEstablished(named);

  // 2. What else keeps the verdict from being read at all, in the resolver's words.
  const [refused, ...othersRefused] = (source.refused ?? []).filter((declaration) => declaration.required);
  if (level === "unknown" && refused !== undefined) {
    return {
      status: STATUS_READINESS_UNKNOWN,
      sentence: refusedSentence(leading, verbOf(source.locating), refused, othersRefused.length),
    };
  }
  return null;
}

/**
 * The console's own demand, with *nothing* — what a search found — after it;
 * `null` where the demand does not stand.
 */
function consoleDemandSummary(
  source: BiosSummarySource,
  rows: readonly BiosSummaryRow[],
  level: BiosLevel | null,
  consoleName: string,
  named: string,
  plain: PlainRequirements,
  nothing: BiosSummary | null,
): BiosSummary | null {
  if (level === "missing" && source.console_needs_image === true && plain.missing === 0) {
    const images = rows.filter(
      (row) => row.used_by_active === true && (row.wanted === "needed" || row.wanted === "optional"),
    );
    if (!images.some((row) => row.satisfied === true)) {
      const sentence = consoleSentence(consoleName, named, images.length);
      return { status: STATUS_NEEDS_IMAGE, sentence: nothing ? `${sentence} — ${nothing.sentence}` : sentence };
    }
  }
  return null;
}

/**
 * The one-of group the launching emulator states, or the plain file it gives
 * way to (see the header); `null` where the emulator states no group.
 */
function oneOfGroupSummary(
  source: BiosSummarySource,
  rows: readonly BiosSummaryRow[],
  level: BiosLevel | null,
  groups: readonly OneOfGroupVerdict[],
  plain: PlainRequirements,
  named: string,
  leading: string,
): BiosSummary | null {
  const group = groupToWord(groups);
  if (!group) return null;
  const regions = regionCount(group);
  if (group.state === "unmet" || (plain.missing === 0 && plain.withheld === 0)) {
    const own = coveredButUnstated(group) ? `${regionTally(group)} · ${STATUS_REGIONS_NOT_CHECKED}` : regions;
    const ratio = plain.count > 0 ? `${plain.done} / ${plain.count} required · ${own}` : own;
    return groupSummary(group, named, leading, ratio, rows);
  }
  if (plain.missing > 0) {
    return requiredFilesSummary(source.locating, named, rows, plain, level, ` · ${regions}`);
  }
  return declinedSummary(named, verbOf(source.locating), plain.withheld);
}

// Small counts are spelled out, as a sentence would — "none of the five".
const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

function spelled(count: number): string {
  return NUMBER_WORDS[count] ?? String(count);
}

/**
 * The console's own demand where none of the emulator's images is in place.
 * An emulator that names no image at all — DuckStation, which
 * searches its folder — gets the demand alone, with no count to word.
 */
function consoleSentence(consoleName: string, named: string, images: number): string {
  const head = `${consoleName} ${CONSOLE_NEEDS_IMAGE}`;
  if (images === 0) return head;
  if (images === 1) return `${head}, ${THE_ONE_IT_KNOWS} ${named} ${KNOWS_IS_NOT_IN_PLACE}`;
  return `${head}, ${NONE_OF_THE} ${spelled(images)} ${named} ${KNOWS_IS_IN_PLACE}`;
}

// The resolver's reasons for refusing a declaration, worded. A reason this list
// does not know is printed as the resolver's own code.
const REFUSAL_REASONS: Readonly<Record<string, string>> = {
  "firmware-path-escapes-root": "lies outside the BIOS folder",
  "firmware-path-unresolvable": "could not be followed to a place on this machine",
  "firmware-path-names-no-file": "names no file",
};

/** A required declaration the resolver would not follow, named with its reason. */
function refusedSentence(leading: string, verb: string, first: RefusedDeclaration, others: number): string {
  const reason = REFUSAL_REASONS[first.reason] ?? `could not be followed (${first.reason})`;
  const more = others > 0 ? ` (and ${others} more)` : "";
  return `${leading} ${verb} ${first.declared}, which ${reason}${more} — ${REFUSED_TAIL}`;
}

/** The resolver's statements that two images tie, each as a clause. */
function tiedNotes(source: BiosSummarySource): string[] {
  const named = launchingName(source) ?? ROLE_MID;
  return (source.emulator_notes ?? [])
    .filter((note) => note.kind === "images-tied")
    .map((note) => {
      const count = note.tied !== null ? spelled(note.tied) : "several";
      const where = note.regions.length > 0 ? `for ${regionNames(note.regions)} ` : "";
      return `${where}${count} images are in place; which one ${named} uses ${TIED_TAIL}`;
    });
}

/** What a by-content search found, where the backend states one and the verdict says which way. */
function contentSearchSummary(
  source: BiosSummarySource,
  rows: readonly BiosSummaryRow[],
  level: BiosLevel | null,
  leading: string,
): BiosSummary | null {
  const required = rows.filter((row) => row.required_by_active);
  switch (source.content_search) {
    case "found": {
      const files = required.filter((row) => row.satisfied === true).map((row) => row.file_name ?? "");
      if (level !== "ok" || files.length === 0) return null;
      return {
        status: STATUS_FOUND_BY_CONTENT,
        sentence: `${leading} recognises ${joined(files)} ${RECOGNISES_IN_FOLDER}`,
      };
    }

    case "incomplete":
      return level === "unknown"
        ? { status: STATUS_READINESS_UNKNOWN, sentence: `${leading}'s ${FOLDER_NOT_READ_COMPLETELY}` }
        : null;
    case "unreadable": {
      if (level !== "unknown") return null;
      const unread = required.find((row) => row.checked === "unread")?.file_name ?? "A BIOS image";
      return { status: STATUS_READINESS_UNKNOWN, sentence: `${unread} ${COULD_NOT_BE_READ}` };
    }
    default:
      return null;
  }
}

/**
 * A settled search of a folder that reached no image to boot, as the resolver
 * states it (`search-found-nothing`: the folder, how many files of a size the
 * emulator accepts it held, and the regions). DuckStation's search states no
 * count and is worded by the folder alone: in the state RetroDECK ships, the
 * regions it carries are every launch's, and naming them would read as a
 * condition.
 */
function foundNothingSummary(source: BiosSummarySource, named: string, leading: string): BiosSummary | null {
  const note = (source.emulator_notes ?? []).find((candidate) => candidate.kind === "search-found-nothing");
  if (note?.dir == null) return null;
  const folder = folderWords(note.dir);
  if (note.candidates == null) {
    return { status: STATUS_NO_IMAGE_FOUND, sentence: `${leading} ${FOUND_NO_IMAGE_IN} ${folder}` };
  }
  if (note.candidates === 0) {
    const regions = note.regions.length > 0 ? ` for a ${regionNames(note.regions)} console` : "";
    return { status: STATUS_NO_IMAGE_FOUND, sentence: `${leading} ${FOUND_NO_IMAGE_IN} ${folder}${regions}` };
  }
  const files = note.candidates === 1 ? "1 file" : `${note.candidates} files`;
  const tail = note.candidates === 1 ? RECOGNISES_NOT_IT : RECOGNISES_NONE;
  return { status: STATUS_NONE_RECOGNISED, sentence: `${folder} holds ${files} ${OF_A_SIZE} ${named} ${tail}` };
}

/** The folder's own name — the last segment of the path the resolver states — with a trailing slash: "bios/". */
function folderWords(dir: string): string {
  const name = lastPathSegment(dir);
  return name ? `${name}/` : "the BIOS folder";
}

function nothingEstablished(named: string): BiosSummary {
  return {
    status: STATUS_REQUIREMENT_UNKNOWN,
    sentence: `${NOTHING_ESTABLISHED_HEAD} ${named} ${NOTHING_ESTABLISHED_TAIL}`,
  };
}

// The order a group is picked to be worded in, where an emulator states more
// than one: the one furthest from met first — nothing in place, then one
// nothing could check, then one partly covered. The colour stays the verdict's.
const GROUP_PRECEDENCE: readonly OneOfGroupVerdict["state"][] = ["unmet", "unknown", "partial", "met"];

/**
 * The required files that are NOT a one-of group: how many there are, how many
 * are in place, how many are missing, and how many nothing could judge.
 *
 * The payload's counts take each group once — `required_downloaded` only where
 * it is met, `required_withheld` where it is unknown — so what is left after
 * taking the groups back out is the plain required files alone. A group is
 * never counted as a file here.
 */
interface PlainRequirements {
  count: number;
  done: number;
  missing: number;
  withheld: number;
}

function plainRequirements(
  groups: readonly OneOfGroupVerdict[],
  requiredCount: number,
  requiredDone: number,
  withheld: number,
): PlainRequirements {
  const count = (state: OneOfGroupVerdict["state"]) => groups.filter((group) => group.state === state).length;
  const plainCount = requiredCount - groups.length;
  const plainDone = requiredDone - count("met");
  const plainWithheld = withheld - count("unknown");
  return {
    count: plainCount,
    done: plainDone,
    missing: plainCount - plainDone - plainWithheld,
    withheld: plainWithheld,
  };
}

/**
 * A group's own count, in regions rather than files: "1 / 3 regions · Japan
 * only". The first number is the regions an option in place serves
 * (`covered`), so a region nobody checked is in the second number and never in
 * the first; the tag names the covered ones where the group is partly met.
 *
 * A group whose verdict nothing could settle (`unknown`) gets no count: "0 / 3
 * regions" is what an unmet group reads, and the two must not look alike. It
 * reaches a status only beside plain files, since alone it words the line as
 * `Readiness unknown`.
 */
function regionCount(group: OneOfGroupVerdict): string {
  if (group.state === "unknown") return STATUS_REGIONS_NOT_CHECKED;
  return group.state === "partial" ? `${regionTally(group)} · ${regionNames(group.covered)} only` : regionTally(group);
}

function regionTally(group: OneOfGroupVerdict): string {
  return `${group.covered.length} / ${group.regions.length} regions`;
}

/** The regions a group carries no option for (`unstated`), empty where a game's region narrows the group. */
function unstatedOf(group: OneOfGroupVerdict): string[] {
  return group.game_regions.length > 0 ? [] : (group.unstated ?? []);
}

/**
 * Is a group open only because it carries no option for some regions — an
 * image in place for others, and every region left unchecked one of those?
 * Then the sentence states what is covered and the count stays a count.
 */
function coveredButUnstated(group: OneOfGroupVerdict): boolean {
  const unstated = unstatedOf(group);
  return (
    group.state === "unknown" &&
    group.covered.length > 0 &&
    group.unchecked.length > 0 &&
    group.unchecked.every((region) => unstated.includes(region))
  );
}

function groupToWord(groups: readonly OneOfGroupVerdict[]): OneOfGroupVerdict | null {
  for (const state of GROUP_PRECEDENCE) {
    const found = groups.find((group) => group.state === state);
    if (found) return found;
  }
  return null;
}

/**
 * State 0: a one-of group, one requirement however many files it lists.
 *
 * Every word comes from the verdict — the emulator's name and the regions — so a
 * group on any console reads the same way. The status is the caller's
 * ({@link regionCount}, behind the plain required files' ratio where there are
 * any). On the game page the verdict was narrowed to the game's own regions
 * (`game_regions`), and the sentence says so. Wherever no game region narrows
 * the group — the platform page, and a game with no clear region — the regions
 * the group carries no option for (`unstated`) are named after whichever
 * sentence stands; a game of such a region is named by its own sentence.
 */
function groupSummary(
  group: OneOfGroupVerdict,
  named: string,
  leading: string,
  ratio: string,
  rows: readonly BiosSummaryRow[],
): BiosSummary {
  const words = groupWords(group, named, leading, ratio, rows);
  const unstated = unstatedOf(group);
  if (unstated.length === 0) return words;
  return {
    ...words,
    sentence: `${words.sentence} — for ${regionNames(unstated)} discs ${named} ${GROUP_UNSTATED_TAIL}`,
  };
}

function groupWords(
  group: OneOfGroupVerdict,
  named: string,
  leading: string,
  ratio: string,
  rows: readonly BiosSummaryRow[],
): BiosSummary {
  const forTheGame = group.game_regions.length > 0;
  if (coveredButUnstated(group)) {
    return { status: ratio, sentence: `${leading} ${GROUP_PARTIAL_HEAD} ${regionNames(group.covered)}` };
  }
  switch (group.state) {
    case "met":
      return {
        status: ratio,
        sentence: forTheGame ? gameCovered(group, named, leading, rows) : `${leading} ${GROUP_MET_TAIL}`,
      };
    case "partial":
      return {
        status: ratio,
        sentence:
          `${leading} ${GROUP_PARTIAL_HEAD} ${regionNames(group.covered)} only — ` +
          `${regionNames(group.missing)} ${GROUP_PARTIAL_TAIL}`,
      };
    case "unmet":
      return { status: ratio, sentence: forTheGame ? gameUncovered(group, leading) : `${leading} ${CANNOT_START}` };
    case "unknown":
      return {
        status: STATUS_READINESS_UNKNOWN,
        sentence: forTheGame
          ? gameUnsettled(group, named, leading)
          : `${IMAGE_UNSETTLED_HEAD} ${named} ${GROUP_UNCHECKED_TAIL}`,
      };
  }
}

/**
 * The game page's sentence where the image for the game's own region settled
 * nothing: an option serves the region and could not be checked, or
 * no option serves it at all — which the resolver leaves outside its verdict,
 * so the page names the region rather than reading the verdict as an answer.
 */
function gameUnsettled(group: OneOfGroupVerdict, named: string, leading: string): string {
  const regions = regionNames(group.unchecked.length > 0 ? group.unchecked : group.game_regions);
  const served = group.options.some((option) => option.regions.some((region) => group.unchecked.includes(region)));
  return served
    ? `${IMAGE_UNSETTLED_HEAD} ${named} ${GROUP_GAME_MET} (${regions}) ${GROUP_GAME_UNCHECKED_TAIL}`
    : `${leading} ${GROUP_GAME_NO_OPTION} (${regions}), ${GROUP_GAME_NO_OPTION_TAIL}`;
}

/**
 * The game page's sentence for a game whose region is covered. Where the image
 * covering it serves every region the group speaks about, the sentence names
 * that image, because it is the one this game starts with whatever its disc
 * says — and says it was found in the BIOS folder only where its row says the
 * reading identified it by its contents (`foundByContent`), which is the folder
 * search. An image every region's own setting names was opened by name.
 */
function gameCovered(
  group: OneOfGroupVerdict,
  named: string,
  leading: string,
  rows: readonly BiosSummaryRow[],
): string {
  const covering = group.options.find(
    (option) => option.satisfied === true && option.regions.some((region) => group.covered.includes(region)),
  );
  if (covering && servesEveryRegion(covering, group)) {
    const row = rows.find((candidate) => candidate.file_name === covering.file_name);
    const how = row && foundByContent(row) ? ` — ${GROUP_GAME_FOUND}` : `, ${GROUP_GAME_SERVES}`;
    return `${leading} ${GROUP_GAME_STARTS} ${covering.file_name}${how}`;
  }
  return `${GROUP_IMAGE_HEAD} ${named} ${GROUP_GAME_MET} (${regionNames(group.covered)}) is in place`;
}

/**
 * The game page's sentence for a game whose region is not covered: the file
 * each of its regions needs, and that it is missing. A game of several regions
 * names the file of each, since any of them would start it, and names only the
 * regions those files serve. A region the resolver listed no option for — it
 * stated only that nothing boots for it — has no file to name, so it gets a
 * clause of its own rather than borrowing another region's file; with no file
 * at all the sentence names the regions alone.
 */
function gameUncovered(group: OneOfGroupVerdict, leading: string): string {
  const serving = group.options.filter((option) => option.regions.some((region) => group.missing.includes(region)));
  const files = [...new Set(serving.map((option) => option.file_name))];
  if (files.length === 0) return `${leading} ${GROUP_GAME_UNMET} (${regionNames(group.missing)})`;
  const named = group.missing.filter((region) => serving.some((option) => option.regions.includes(region)));
  const bare = group.missing.filter((region) => !named.includes(region));
  const state = missingClause(files.length);
  const rest = bare.length > 0 ? `, ${GROUP_NO_IMAGE_FOR} ${regionNames(bare)}` : "";
  return `${leading} needs ${joined(files, "or")} ${GROUP_GAME_NEEDS} (${regionNames(named)}) — ${state}${rest}`;
}

function missingClause(files: number): string {
  if (files === 1) return GROUP_ONE_MISSING;
  return files === 2 ? GROUP_TWO_MISSING : GROUP_MANY_MISSING;
}

/**
 * Is *summary* the answer that no BIOS is chosen in the emulator's settings?
 * The platform page leaves its by-hand note out under it.
 */
export function saysNoBiosChosen(summary: BiosSummary): boolean {
  return summary.status === STATUS_NO_BIOS_CHOSEN;
}

/**
 * The launching emulator's groups as the game page lists them under its
 * headline, each headed by the emulator's name — or by its role, where the pick
 * carries no label, in the same spelling the sentences use. *files* are the
 * rows, whose resolver check words an option that is not met.
 */
export function groupBlocks(
  source: BiosSummarySource,
  files: readonly { file_name: string; checked?: FirmwareChecked | null }[] = [],
): GroupBlock[] {
  const leading = launchingName(source) ?? ROLE_LEADING;
  const checkedOf = (fileName: string) => files.find((row) => row.file_name === fileName)?.checked;
  return (source.one_of_groups ?? []).map((group) => groupBlock(group, leading, checkedOf));
}

/**
 * What a declined level declined over: a required row the resolver could not
 * judge, which names a file count because the file list is where each row's own
 * note explains itself — or nothing the payload can point at, which names only
 * the emulator.
 */
function declinedSummary(named: string, verb: string, withheld: number): BiosSummary {
  if (withheld > 0) {
    const files = withheld === 1 ? "One file" : `${withheld} files`;
    return {
      status: STATUS_READINESS_UNKNOWN,
      sentence: `${files} ${named} ${verb} ${UNCHECKED_TAIL}`,
    };
  }
  return nothingEstablished(named);
}

/**
 * The verb every sentence about the emulator's required files takes, by how it
 * finds its firmware — the count, a file nothing could check, and a refused
 * declaration alike, so one emulator is never worded two ways.
 */
function verbOf(locating: FirmwareLocating | undefined): string {
  if (locating === "unestablished") return LISTS_AS_REQUIRED;
  return locating === "by-name" ? OPENS_BY_NAME : VERB_REQUIRES;
}

/**
 * The emulator's own required files, held or short — one answer read two ways.
 *
 * Both state the same ratio, and which sentence stands is the level's verdict;
 * the ratio never decides it. How the emulator finds its firmware words what it
 * does with them ({@link verbOf}): it "opens by name" the files it
 * declares, "requires" them where it also searches its folder by content, or —
 * where nobody has read how it finds them — "lists as required", with a note
 * saying so. A file shown missing beside one nothing could check
 * names both. *groupTail* is a one-of group's region count, appended
 * to the status where the counts passed in are the plain files beside that
 * group.
 */
function requiredFilesSummary(
  locating: FirmwareLocating | undefined,
  named: string,
  rows: readonly BiosSummaryRow[],
  plain: PlainRequirements,
  level: BiosLevel | null,
  groupTail = "",
): BiosSummary {
  const unestablished = locating === "unestablished";
  const verb = verbOf(locating);
  const note = unestablished ? `. How ${named} ${LOCATING_UNESTABLISHED_TAIL}` : "";
  const withheldTail = plain.withheld > 0 && plain.missing > 0 ? ` · ${plain.withheld} not checked` : "";
  const status = `${plain.done} / ${plain.count} required${withheldTail}${groupTail}`;
  // "Ready" is the level — the fallback for a payload with none is the same
  // comparison, not a second rule.
  const ready = level === null ? plain.done >= plain.count : level === "ok";
  if (ready) {
    // The optional set of the emulator the sentence names (`used_by_active`),
    // never of another emulator, whose files are shown when it is chosen.
    const optionalMissing = rows.filter(
      (row) =>
        row.used_by_active === true &&
        (row.wanted === "needed" || row.wanted === "optional") &&
        !row.required_by_active &&
        !row.downloaded,
    ).length;
    const tail = optionalMissing > 0 ? ` (${optionalMissing} ${OPTIONAL_MISSING_TAIL})` : "";
    // A requirement of ONE file is worded on its own: "All 1 files" is reachable
    // — gpSP lists exactly one, gba_bios.bin.
    const held =
      plain.count === 1
        ? `The one file ${named} ${verb} ${IS_IN_PLACE}`
        : `All ${plain.count} files ${named} ${verb} ${ARE_IN_PLACE}`;
    return { status, sentence: `${held}${tail}${note}` };
  }
  return { status, sentence: `${shortSentence(verb, named, rows, plain, unestablished)}${note}` };
}

/** The sentence over required files the verdict says are not all in place. */
function shortSentence(
  verb: string,
  named: string,
  rows: readonly BiosSummaryRow[],
  plain: PlainRequirements,
  unestablished: boolean,
): string {
  if (plain.withheld > 0 && plain.missing > 0) {
    const missing =
      plain.missing === 1
        ? `One file ${named} ${verb} ${IS_NOT_IN_PLACE}`
        : `${plain.missing} files ${named} ${verb} ${ARE_NOT_IN_PLACE}`;
    const unchecked = plain.withheld === 1 ? "one" : String(plain.withheld);
    return `${missing}, and ${unchecked} ${COULD_NOT_BE_CHECKED_TAIL}`;
  }
  const missingNames = rows
    .filter((row) => row.required_by_active && row.satisfied === false)
    .map((row) => row.file_name ?? "");
  if (unestablished && missingNames.length > 0) {
    const state = missingNames.length === 1 ? IS_NOT_IN_PLACE : ARE_NOT_IN_PLACE;
    return `${joined(missingNames)}, which ${named} ${verb}, ${state}`;
  }
  return plain.count === 1
    ? `The one file ${named} ${verb} ${IS_NOT_IN_PLACE}`
    : `${plain.done} of ${plain.count} files ${named} ${verb} ${ARE_IN_PLACE}`;
}
