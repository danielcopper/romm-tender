/**
 * A game page's rules for its BIOS file rows: which rows it lists, and the dot
 * beside each. Why the row rule is the game page's alone is
 * `docs/architecture/invariants.md`'s, in its `on_server` entry.
 */

import type { BiosFileStatus } from "../types";
import { isFetchable } from "./biosFetchable";

/**
 * Is this row's requirement met? `null` where nothing established it.
 *
 * The row's VERDICT — the resolver's per-file answer — not
 * `downloaded`: a wrong image is there and not met, and a declared folder is
 * there on every RetroDECK install whatever it holds. A payload with no verdict
 * at all falls back to `downloaded`.
 *
 * The dot beside a row and the decision to show that row at all read this one
 * function, so a reader cannot be shown a row whose dot answers a different
 * question from the one that kept it.
 */
function rowVerdict(file: BiosFileStatus): boolean | null {
  return file.satisfied === undefined ? file.downloaded : file.satisfied;
}

/**
 * The dot beside one file row: what it means for THIS launch.
 *
 * A missing option of the launching emulator's group is red where a region it
 * serves is one the group's verdict names as missing — on the game page that is
 * the verdict for this game's own regions, so the image of another region stays
 * grey there. For another emulator's group, which was judged for no game, a
 * caller passes no regions, so its missing options stay grey, not established.
 */
export function fileDotColor(file: BiosFileStatus, missingRegions: ReadonlySet<string>): string {
  const verdict = rowVerdict(file);
  // Grey, the colour of "not established": amber means only a one-of
  // group whose images serve some regions.
  if (verdict === null) return "#8f98a0";
  if (verdict) return "#5ba32b";
  if (file.required_by_active) return "#d94126";
  if (file.one_of?.regions.some((region) => missingRegions.has(region))) return "#d94126";
  // Not met and not required here — missing, wrong or refused alike.
  return "#8f98a0";
}

/**
 * Which of the files an emulator asks for the game page puts a row on.
 *
 * The platform page is the management surface and lists every one of them. This
 * is a game's page, and a row earns its place here by saying something about
 * THIS launch or by being something the reader can act on. A Dreamcast page
 * listed eight rows, six of them arcade BIOSes Flycast declares because it also
 * emulates Naomi and AtomisWave — not required, not present, not in the library
 * — and they pushed the two rows that mattered off the top.
 *
 * Six answers keep a row, and the first two are this launch's requirement in
 * its two shapes:
 *
 * - **required for this launch** (`required_by_active`).
 * - **an option of the launching emulator's one-of group** (`one_of`) — one file
 *   of several, each of which meets the group for the regions it serves.
 *   `required_by_active` is false on every such row by construction, because the
 *   group is the requirement and no one option is; dropping these rows leaves
 *   the header stating the group's verdict over a list with none of its files.
 * - **present** — the verdict is met, so the row is the evidence for it.
 * - **fetchable** — `isFetchable`, the same predicate the platform page's
 *   download buttons are built from: a row this page treats as actionable and
 *   that page offers no button for is a dead end pointed at.
 * - **withheld** — nothing could judge the row (`satisfied === null`). An
 *   ignorance is something to say, never something to summarise away.
 * - **replaceable** — the row offers "Replace…" (`replace_offered`). Its verdict
 *   is false and `isFetchable` refuses a place another file occupies, so unless
 *   it is required or an option nothing else keeps it — yet Replace is
 *   something the reader can do on this page.
 *
 * What is left over is declared, not required for this launch, not an option,
 * not met, fetchable from nowhere and not replaceable — nothing a reader of
 * THIS page could do anything with. A caller counts it on one line instead,
 * which names where the rows are.
 *
 * A caller asks it of the shown emulator: another emulator's rows carry that
 * emulator's `required_by_active` and `one_of`, so "this launch" there reads as
 * a launch with that emulator.
 */
export function rowBelongsOnThisPage(file: BiosFileStatus): boolean {
  if (file.required_by_active || file.one_of || file.replace_offered) return true;
  // Present and withheld in one test: `false` is the only verdict that leaves a
  // row with nothing to say here.
  if (rowVerdict(file) !== false) return true;
  return isFetchable(file);
}
