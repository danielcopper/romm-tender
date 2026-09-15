/**
 * The one place a BIOS state is put into words.
 *
 * Seven states, and before this module each surface worded them for itself: the
 * game page's BIOS tab, the platform pane, and the platform list's tooltip all
 * held their own spelling of the same seven, some naming the launching emulator
 * and some not. Nothing joined them, so the drift was invisible to every test —
 * each surface's own expectations passed while the three said different things
 * about one platform, which is what a reader moving between them saw.
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
 * was scoped to, carried on the payload as `active_core_label`. A sentence about
 * "the launching emulator" sat two inches from an `Active Core: mGBA` row and
 * said less than everything around it. Where the payload carries no label the
 * pick could not be made or has no name of its own, and the sentences fall back
 * to naming the role rather than inventing one.
 *
 * **The order of the conditions is load-bearing and is the order the three
 * surfaces already read in.** The console's own demand (`system_image`) is
 * tested FIRST, ahead of the level's decline: it is the one requirement no count
 * can state — the console asks for ONE of the images the emulator declares, and
 * a libretro `.info` can mark a file required or optional and say nothing else —
 * so a count-derived sentence would stand over a system that will not boot.
 * Today the pair never arrives, because the backend lands an established absence
 * on `missing` rather than on `unknown`; the order is a guard rather than a rule
 * about a live case, and it is now a guard in one place rather than three.
 *
 * What this module does NOT hold is the library's own ratio — `(d/t files held)`.
 * That counts what the RomM library holds for the platform, a third set again,
 * so it is written next door (`utils/biosHeldRatio.ts`) and appended to every
 * one of the seven sentences by the two surfaces that state it at all — the game
 * page and the platform pane, not the list's row tooltip, which has never
 * carried the library's ratio. Two true statements about two sets, where one
 * number built out of both would be true of neither. Sharing its form between
 * those two is a different move from folding it into a sentence — what stays
 * apart is what each of them counts.
 */

import type { BiosLevel, FirmwareWanted, SystemImage } from "../types/firmware";

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
  active_core_label?: string | null;
}

/** The row fields the two count fallbacks and the optional-missing breakdown are
 *  taken over. Both file types carry them. */
export interface BiosSummaryRow {
  wanted?: FirmwareWanted;
  required_by_active?: boolean;
  downloaded?: boolean;
}

// The role a sentence names where the pick carries no label. Two spellings of
// one fallback, because two of the seven sentences open with the emulator and
// the rest name it mid-sentence — and a real label is never recased, since
// `mGBA` is the name its own catalogue spells.
const ROLE_MID = "the launching emulator";
const ROLE_LEADING = "The launching emulator";

// The fixed halves of the seven sentences. They are constants rather than
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
  STATUS_NEEDS_IMAGE,
  STATUS_READINESS_UNKNOWN,
  STATUS_REQUIREMENT_UNKNOWN,
  STATUS_NOTHING_REQUIRED,
];

/**
 * The seven states, in the order the surfaces read them.
 *
 * **What is decided here is the order of the four rungs**: the console's own
 * demand, then the level's decline, then the emulator's required files, then
 * the finished answer. Two of those rungs hold more than one state and are
 * worded next door — {@link declinedSummary} holds states 2-4, which are a
 * precedence of their own, and {@link requiredFilesSummary} holds states 5-6,
 * which are one comparison read two ways. So the seven are two orders and not
 * one: the console's demand standing ahead of the decline and the withheld row
 * standing ahead of the unsettled console are different statements, resting on
 * different reasons, and a body holding all seven tests in a row says they are
 * the same kind of thing.
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
 * made.
 */
function requiredFilesSummary(
  named: string,
  rows: readonly BiosSummaryRow[],
  requiredDone: number,
  requiredCount: number,
  level: BiosLevel | null,
): BiosSummary {
  const ratio = `${requiredDone} / ${requiredCount} required`;
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
    const optionalMissing = rows.filter(
      (row) => row.wanted === "optional" && !row.required_by_active && !row.downloaded,
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
