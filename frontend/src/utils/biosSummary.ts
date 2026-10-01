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
 * **The order of the conditions is load-bearing and is the order the three
 * surfaces already read in.** A one-of group the launching emulator states is
 * worded first ({@link groupSummary}): it IS the console's demand, said region by
 * region, and where an emulator states one the backend answers `system_image`
 * with the neutral value, so the two never stand together. A group with nothing
 * in place keeps its sentence whatever else is open, because the console does
 * not start either way. Any other group gives way to a plain required file that
 * is open beside it, worded by that file's own state: a missing one by the
 * count, one nothing could judge by the declined rung — and both count the
 * plain files alone, never a group as a file. Where the status beside a group
 * is a count, the group is counted in regions, behind the plain files' ratio
 * where there are any. The console's own demand (`system_image`) is tested
 * next, ahead of the level's decline: it is the one requirement no count can
 * state — the console asks for ONE of the images the emulator declares, and a
 * libretro `.info` can mark a file required or optional and say nothing else —
 * so a count-derived sentence would stand over a system that will not boot.
 * Today the pair never arrives, because the backend lands an established
 * absence on `missing` rather than on `unknown`; the order is a guard rather
 * than a rule about a live case, and it is now a guard in one place rather than
 * three.
 *
 * What this module does NOT hold is the library's own ratio —
 * `(d/t RomM library files)`, which counts a third set and is written next door
 * (`utils/biosHeldRatio.ts`, where why the two are stated side by side rather
 * than folded together lives). Two of the three surfaces append it to every
 * sentence here — the game page and the platform pane — and the list's row
 * tooltip has never carried it.
 */

import type { BiosLevel, FirmwareWanted, OneOfGroupVerdict, SystemImage } from "../types/firmware";
import { foundByContent } from "./biosFileNote";
import { groupBlock, joined, regionNames, servesEveryRegion, type GroupBlock } from "./biosGroup";

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
export interface BiosSummarySource {
  required_count?: number;
  required_downloaded?: number;
  required_withheld?: number;
  system_image?: SystemImage;
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
// The emulator's name sits between the count and this, so the searchable run
// starts at `requires` — and it has to, because a bare "could not be checked" is
// also the platform pane's mark for a row whose own verdict was withheld, and a
// lock searching for that would fire on a string this module does not own.
const REQUIRES_UNCHECKED = "requires could not be checked";
const IMAGE_UNSETTLED_HEAD = "Whether the BIOS image";
const IMAGE_UNSETTLED_TAIL = "needs is in place could not be established";
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
// sentence shares; "The BIOS image" opens two of them and is too common a run
// to search for on its own.
const GROUP_IMAGE_HEAD = "The BIOS image";
const GROUP_MET_TAIL = "needs is in place for every region";
const GROUP_PARTIAL_HEAD = "has a BIOS image for";
const GROUP_PARTIAL_TAIL = "discs will not start";
const GROUP_UNCHECKED_TAIL = "needs is in place could not be checked";
const GROUP_GAME_MET = "needs for this game's region";
const GROUP_GAME_UNMET = "has no BIOS image for this game's region";
const GROUP_GAME_NEEDS = "to start this game";
const GROUP_GAME_STARTS = "starts this game with";
const GROUP_GAME_FOUND = "found in the BIOS folder, it serves every region";
const GROUP_GAME_SERVES = "which serves every region";
const GROUP_ONE_MISSING = "it is missing";
const GROUP_TWO_MISSING = "neither is in place";
const GROUP_MANY_MISSING = "none of them is in place";
const GROUP_NO_IMAGE_FOR = "and there is no BIOS image for";

const STATUS_NEEDS_IMAGE = "Needs a BIOS image";
const STATUS_READINESS_UNKNOWN = "Readiness unknown";
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
  REQUIRES_UNCHECKED,
  IMAGE_UNSETTLED_HEAD,
  IMAGE_UNSETTLED_TAIL,
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
  GROUP_GAME_UNMET,
  GROUP_GAME_NEEDS,
  GROUP_GAME_STARTS,
  GROUP_GAME_FOUND,
  GROUP_GAME_SERVES,
  GROUP_ONE_MISSING,
  GROUP_TWO_MISSING,
  GROUP_MANY_MISSING,
  GROUP_NO_IMAGE_FOR,
  // The role's two spellings as string literals: a component falling back to
  // the role itself writes one of these, and the role belongs here.
  `"${ROLE_LEADING}"`,
  `"${ROLE_MID}"`,
  STATUS_NEEDS_IMAGE,
  STATUS_READINESS_UNKNOWN,
  STATUS_REQUIREMENT_UNKNOWN,
  STATUS_NOTHING_REQUIRED,
];

/**
 * The seven states and a one-of group's, in the order the surfaces read them.
 *
 * **What is decided here is the order of the five rungs**: a one-of group the
 * launching emulator states (unless it is not `unmet` and a plain required file
 * is open beside it), the console's own demand, then the level's decline, then
 * the emulator's required files, then the finished answer. The group's own
 * states are worded next door ({@link groupSummary}), and two of the other rungs
 * hold more than one state and are worded next door too — {@link declinedSummary}
 * holds states 2-4, which are a precedence of their own, and
 * {@link requiredFilesSummary} holds states 5-6, which are one comparison read
 * two ways. So the seven are two orders and not one: the console's demand
 * standing ahead of the decline and the withheld row standing ahead of the
 * unsettled console are different statements, resting on different reasons,
 * and a body holding all seven tests in a row says they are the same kind of
 * thing.
 *
 * *level* is the backend's own readiness verdict and is taken as an argument
 * rather than off *source*: the game page holds it beside the payload (a
 * requirement nothing could be established for arrives as a placeholder status
 * with the level set separately), so reading it off the payload here would answer
 * a different question on that surface. `null` — a payload from before the field
 * existed — falls back to the count comparison the level itself makes.
 */
export function biosSummary(
  source: BiosSummarySource,
  rows: readonly BiosSummaryRow[],
  level: BiosLevel | null,
): BiosSummary {
  const emulator = source.active_core_label ?? null;
  const named = emulator ?? ROLE_MID;
  const leading = emulator ?? ROLE_LEADING;
  const systemImage = source.system_image ?? "not_demanded";
  const withheld = source.required_withheld ?? 0;
  const requiredRows = rows.filter((row) => row.required_by_active);
  const requiredCount = source.required_count ?? requiredRows.length;
  const requiredDone = source.required_downloaded ?? requiredRows.filter((row) => row.downloaded).length;

  // 0. A one-of group the launching emulator states: the console's demand, said
  //    region by region. See the header.
  const groups = source.one_of_groups ?? [];
  const group = groupToWord(groups);
  if (group) {
    const plain = plainRequirements(groups, requiredCount, requiredDone, withheld);
    const regions = regionCount(group);
    if (group.state === "unmet" || (plain.missing === 0 && plain.withheld === 0)) {
      const ratio = plain.count > 0 ? `${plain.done} / ${plain.count} required · ${regions}` : regions;
      return groupSummary(group, named, leading, ratio, rows);
    }
    if (plain.missing > 0) {
      return requiredFilesSummary(named, rows, plain.done, plain.count, level, ` · ${regions}`);
    }
    return declinedSummary(named, plain.withheld, systemImage);
  }

  // 1. The console's own demand, ahead of everything: see the header.
  if (systemImage === "absent") {
    return { status: STATUS_NEEDS_IMAGE, sentence: `${leading} ${CANNOT_START}` };
  }

  // 2 / 3 / 4. The level declined, over one of three gaps.
  if (level === "unknown") {
    return declinedSummary(named, withheld, systemImage);
  }

  // 5 / 6. The emulator's own required files, held or short.
  if (requiredCount > 0) {
    return requiredFilesSummary(named, rows, requiredDone, requiredCount, level);
  }

  // 7. A finished answer, and the set it was taken over is named: a bare
  //    "Nothing required" was read as the CONSOLE needing no BIOS, which is the
  //    axis above and one the catalogue answers `not_demanded` for a console
  //    nobody has looked at as readily as for one shown to start with nothing.
  return { status: STATUS_NOTHING_REQUIRED, sentence: `${leading} ${MARKS_NONE_REQUIRED}` };
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
 */
function regionCount(group: OneOfGroupVerdict): string {
  const counted = `${group.covered.length} / ${group.regions.length} regions`;
  return group.state === "partial" ? `${counted} · ${regionNames(group.covered)} only` : counted;
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
 * (`game_regions`), and the sentence says so.
 */
function groupSummary(
  group: OneOfGroupVerdict,
  named: string,
  leading: string,
  ratio: string,
  rows: readonly BiosSummaryRow[],
): BiosSummary {
  const forTheGame = group.game_regions.length > 0;
  switch (group.state) {
    case "met":
      return {
        status: ratio,
        sentence: forTheGame
          ? gameCovered(group, named, leading, rows)
          : `${GROUP_IMAGE_HEAD} ${named} ${GROUP_MET_TAIL}`,
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
        sentence: `${IMAGE_UNSETTLED_HEAD} ${named} ${GROUP_UNCHECKED_TAIL}`,
      };
  }
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
  const state = files.length === 1 ? GROUP_ONE_MISSING : files.length === 2 ? GROUP_TWO_MISSING : GROUP_MANY_MISSING;
  const rest = bare.length > 0 ? `, ${GROUP_NO_IMAGE_FOR} ${regionNames(bare)}` : "";
  return `${leading} needs ${joined(files, "or")} ${GROUP_GAME_NEEDS} (${regionNames(named)}) — ${state}${rest}`;
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
 * States 2-4: what a declined level declined over.
 *
 * The withheld row comes first because it is the half that can name a file —
 * it states a count, and the file list is where each row's own caveat explains
 * itself. The unsettled console demand names no row, and the last state names
 * nothing but the emulator, so the order runs from the gap that points
 * furthest to the one that points nowhere. The two `Readiness unknown` states
 * fall together above the one that is not about readiness at all.
 */
function declinedSummary(named: string, withheld: number, systemImage: SystemImage): BiosSummary {
  // 2. The requirement IS known and the readiness is not — a required row the
  //    resolver could not judge, a declared folder it could not read. It names
  //    a file count because the file list is where each row's own caveat
  //    explains itself.
  if (withheld > 0) {
    const files = withheld === 1 ? "One file" : `${withheld} files`;
    return {
      status: STATUS_READINESS_UNKNOWN,
      sentence: `${files} ${named} ${REQUIRES_UNCHECKED}`,
    };
  }
  // 3. The same gap one axis over: the console's demand is known and whether
  //    it is met is not.
  if (systemImage === "unsettled") {
    return {
      status: STATUS_READINESS_UNKNOWN,
      sentence: `${IMAGE_UNSETTLED_HEAD} ${named} ${IMAGE_UNSETTLED_TAIL}`,
    };
  }
  // 4. Narrower than the branch reaching it looks: the ONE emulator this
  //    platform launches with could not be asked what it wants. Every other
  //    installed emulator may have answered perfectly well.
  return {
    status: STATUS_REQUIREMENT_UNKNOWN,
    sentence: `${NOTHING_ESTABLISHED_HEAD} ${named} ${NOTHING_ESTABLISHED_TAIL}`,
  };
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
    // The optional set of the emulator the sentence names, not of every
    // installed one: `wanted` says whether ANY emulator requires a file, so a
    // file this one marks optional and another requires reads "needed" there.
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
