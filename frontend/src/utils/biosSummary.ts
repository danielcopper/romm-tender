/**
 * The one place a BIOS state is put into words.
 *
 * Seven states and a one-of group's, and before this module each surface worded
 * the seven for itself: the game page's BIOS tab, the platform pane, and the
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
 * was scoped to, carried on the payload as `active_core_label`. On the game
 * page the same emulator is named two inches away, under the pane's Emulator
 * heading, so a sentence about "the launching emulator" says less than everything
 * around it. Where the payload carries no label the pick could not be made or has
 * no name of its own, and the sentences fall back to naming the role rather than
 * inventing one.
 *
 * **The colour is never decided here.** It is the backend's level — the
 * resolver's verdict for the launching emulator — and this module
 * only says what that verdict means. **The order of the conditions is
 * load-bearing**: what keeps the verdict from being read at all comes first (an
 * emulator nobody read, a BIOS nobody chose in its settings, a required
 * declaration the resolver would not follow), then the console's own demand
 * where none of the images the emulator knows is in place (#2190 D13), then a
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
  FirmwareWanted,
  OneOfGroupVerdict,
  RefusedDeclaration,
} from "../types/firmware";
import { foundByContent } from "./biosFileNote";
import { joined } from "./formatters";
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
  file_name?: string;
  caveats?: string[];
}

// The role a sentence names where the pick carries no label. Two spellings of
// one fallback, because two of the seven sentences open with the emulator and
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
// The emulator's name sits between the count and this, so the searchable run
// starts at `requires` — and it has to, because a bare "could not be checked" is
// also the platform pane's mark for a row whose own verdict was withheld, and a
// lock searching for that would fire on a string this module does not own.
const REQUIRES_UNCHECKED = "requires could not be checked";
const IMAGE_UNSETTLED_HEAD = "Whether the BIOS image";
const NOTHING_ESTABLISHED_HEAD = "Nothing could be established about what";
const NOTHING_ESTABLISHED_TAIL = "needs";
const REQUIRES_ARE_IN_PLACE = "requires are in place";
// The same tail for a requirement of exactly one file. It is a separate run
// rather than a built one because the verb moves with the count, and a
// sentence assembled out of "are"/"is" would be searchable as neither.
const REQUIRES_IS_IN_PLACE = "requires is in place";
const REQUIRES_IS_NOT_IN_PLACE = "requires is not in place";
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
  REQUIRES_UNCHECKED,
  IMAGE_UNSETTLED_HEAD,
  NOTHING_ESTABLISHED_HEAD,
  REQUIRES_ARE_IN_PLACE,
  REQUIRES_IS_IN_PLACE,
  REQUIRES_IS_NOT_IN_PLACE,
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
  const emulator = source.active_core_label ?? null;
  const named = emulator ?? ROLE_MID;
  const leading = emulator ?? ROLE_LEADING;
  const withheld = source.required_withheld ?? 0;
  const requiredRows = rows.filter((row) => row.required_by_active);
  const requiredCount = source.required_count ?? requiredRows.length;
  const requiredDone = source.required_downloaded ?? requiredRows.filter((row) => row.downloaded).length;
  const groups = source.one_of_groups ?? [];
  const plain = plainRequirements(groups, requiredCount, requiredDone, withheld);

  // 1. The emulator nobody read: nothing that follows can be about it.
  if (source.emulator_read === false) return nothingEstablished(named);

  // 2. What keeps the verdict from being read at all, in the resolver's words.
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
    return { status: STATUS_READINESS_UNKNOWN, sentence: refusedSentence(leading, refused, othersRefused.length) };
  }

  // 3. The console's own demand, with none of the images the emulator knows in place.
  if (level === "missing" && source.console_needs_image === true && plain.missing === 0) {
    const images = rows.filter(
      (row) => row.used_by_active === true && (row.wanted === "needed" || row.wanted === "optional"),
    );
    if (!images.some((row) => row.satisfied === true)) {
      return {
        status: STATUS_NEEDS_IMAGE,
        sentence: consoleSentence(consoleName ?? THIS_SYSTEM, named, images.length),
      };
    }
  }

  // 4. A one-of group the launching emulator states.
  const group = groupToWord(groups);
  if (group) {
    const regions = regionCount(group);
    if (group.state === "unmet" || (plain.missing === 0 && plain.withheld === 0)) {
      const own = coveredButUnstated(group) ? `${regionTally(group)} · ${STATUS_REGIONS_NOT_CHECKED}` : regions;
      const ratio = plain.count > 0 ? `${plain.done} / ${plain.count} required · ${own}` : own;
      return groupSummary(group, named, leading, ratio, rows);
    }
    if (plain.missing > 0) {
      return requiredFilesSummary(named, rows, plain.done, plain.count, level, ` · ${regions}`);
    }
    return declinedSummary(named, plain.withheld);
  }

  // 5. The level declined.
  if (level === "unknown") return declinedSummary(named, withheld);

  // 6. The emulator's own required files, held or short.
  if (requiredCount > 0) return requiredFilesSummary(named, rows, requiredDone, requiredCount, level);

  // 7. A finished answer, and the set it was taken over is named: a bare
  //    "Nothing required" was read as the CONSOLE needing no BIOS.
  return { status: STATUS_NOTHING_REQUIRED, sentence: `${leading} ${MARKS_NONE_REQUIRED}` };
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
function refusedSentence(leading: string, first: RefusedDeclaration, others: number): string {
  const reason = REFUSAL_REASONS[first.reason] ?? `could not be followed (${first.reason})`;
  const more = others > 0 ? ` (and ${others} more)` : "";
  return `${leading} requires ${first.declared}, which ${reason}${more} — ${REFUSED_TAIL}`;
}

/** The resolver's statements that two images tie, each as a clause. */
function tiedNotes(source: BiosSummarySource): string[] {
  const named = source.active_core_label ?? ROLE_MID;
  return (source.emulator_notes ?? [])
    .filter((note) => note.kind === "images-tied")
    .map((note) => {
      const count = note.tied !== null ? spelled(note.tied) : "several";
      const where = note.regions.length > 0 ? `for ${regionNames(note.regions)} ` : "";
      return `${where}${count} images are in place, and which one ${named} uses ${TIED_TAIL}`;
    });
}

function nothingEstablished(named: string): BiosSummary {
  return {
    status: STATUS_REQUIREMENT_UNKNOWN,
    sentence: `${NOTHING_ESTABLISHED_HEAD} ${named} ${NOTHING_ESTABLISHED_TAIL}`,
  };
}

// The order a group is picked to be worded in, where an emulator states more
// than one: the one the level rests on first — nothing in place is `missing`,
// an unchecked one declines, a partial one is amber.
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
function plainRequirements(
  groups: readonly OneOfGroupVerdict[],
  requiredCount: number,
  requiredDone: number,
  withheld: number,
): { count: number; done: number; missing: number; withheld: number } {
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
 * The launching emulator's groups as the game page lists them under its
 * headline, each headed by the emulator's name — or by its role, where the pick
 * carries no label, in the same spelling the sentences use.
 */
export function groupBlocks(source: BiosSummarySource): GroupBlock[] {
  const leading = source.active_core_label ?? ROLE_LEADING;
  return (source.one_of_groups ?? []).map((group) => groupBlock(group, leading));
}

/**
 * What a declined level declined over: a required row the resolver could not
 * judge, which names a file count because the file list is where each row's own
 * note explains itself — or nothing the payload can point at, which names only
 * the emulator.
 */
function declinedSummary(named: string, withheld: number): BiosSummary {
  if (withheld > 0) {
    const files = withheld === 1 ? "One file" : `${withheld} files`;
    return {
      status: STATUS_READINESS_UNKNOWN,
      sentence: `${files} ${named} ${REQUIRES_UNCHECKED}`,
    };
  }
  return nothingEstablished(named);
}

/**
 * States 5-6: the emulator's own required files, held or short.
 *
 * They are one function because they are one answer read two ways: both state
 * the same ratio, and which sentence stands is the level's verdict over exactly
 * the comparison that ratio is. Splitting them would put the ratio in two
 * places and invite a second readiness rule beside the one the level already
 * made. *groupTail* is a one-of group's region count, appended to the status
 * where the counts passed in are the plain files beside that group.
 */
function requiredFilesSummary(
  named: string,
  rows: readonly BiosSummaryRow[],
  requiredDone: number,
  requiredCount: number,
  level: BiosLevel | null,
  groupTail = "",
): BiosSummary {
  const ratio = `${requiredDone} / ${requiredCount} required${groupTail}`;
  // "Ready" is the level, which decides it on exactly this comparison — the
  // fallback is that same comparison, not a second rule.
  const ready = level === null ? requiredDone >= requiredCount : level === "ok";
  // A requirement of ONE file is worded on its own, because the two count
  // sentences read "All 1 files" and "0 of 1 files" there, and both halves are
  // reachable rather than theoretical: DuckStation requires exactly one image
  // on a stock RetroDECK (`scph1001.bin`), so a PlayStation reads state 5 while
  // that file is in place and state 6 while it is not.
  const one = requiredCount === 1;
  if (ready) {
    // The optional set of the emulator the sentence names (`used_by_active`),
    // never of another emulator, whose files are on its own tab.
    const optionalMissing = rows.filter(
      (row) =>
        row.used_by_active === true &&
        (row.wanted === "needed" || row.wanted === "optional") &&
        !row.required_by_active &&
        !row.downloaded,
    ).length;
    const tail = optionalMissing > 0 ? ` (${optionalMissing} ${OPTIONAL_MISSING_TAIL})` : "";
    const held = one
      ? `The one file ${named} ${REQUIRES_IS_IN_PLACE}`
      : `All ${requiredCount} files ${named} ${REQUIRES_ARE_IN_PLACE}`;
    return { status: ratio, sentence: `${held}${tail}` };
  }
  return {
    status: ratio,
    sentence: one
      ? `The one file ${named} ${REQUIRES_IS_NOT_IN_PLACE}`
      : `${requiredDone} of ${requiredCount} files ${named} ${REQUIRES_ARE_IN_PLACE}`,
  };
}
