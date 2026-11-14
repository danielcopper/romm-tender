/**
 * Where a BIOS answer stands on a surface that reads one: a platform pane for a
 * platform, or a game's BIOS section for its ROM. Every surface words the three
 * states and derives them here, so they cannot come to tell them apart
 * differently.
 *
 * `pending` is the state this exists to keep separate: the answer has been asked
 * for and has not arrived, which is NOT "nothing could be established".
 * Rendering the two alike is how a reader takes an answer where none has come
 * yet.
 *
 * - `pending` — asked for, not back yet.
 * - `answered` — the page holds an answer. Every finished read answers, a
 *   reading that did not happen included: its verdict is the one that says
 *   nothing could be established.
 * - `failed` — the read did not come back and there is nothing behind it.
 *
 * An answer already held outranks a failure, so a failed RE-read leaves the
 * state at `answered` and is reported beside it ({@link biosAnswerStale}):
 * taking an answer back because a later read failed is how a page loses an
 * answer it has.
 */
import type { BiosAnswer } from "../api/backend";
import type { BiosLevel, BiosStatus } from "../types";

export type BiosReadState = "pending" | "answered" | "failed";

/** The state of a page holding an answer or not (`held`) whose latest read did or did not fail (`failed`). */
export function biosReadState(held: boolean, failed: boolean): BiosReadState {
  if (held) return "answered";
  return failed ? "failed" : "pending";
}

/** Whether the answer a page holds stands over a read that failed — so it is the state from before whatever changed
 *  it. */
export function biosAnswerStale(held: boolean, failed: boolean): boolean {
  return held && failed;
}

/** The status flags {@link biosFieldsFromCache} overlays onto an answer whose
 *  requirement could not be established (`bios_status_unknown` with the
 *  `"unknown"` level), alongside `files: []` so no file rows render while the
 *  answer's own emulator tabs and delete counts stand. */
const UNKNOWN_REQUIREMENT_STATUS: BiosStatus = { needs_bios: false, bios_status_unknown: true };

/** Project a game-detail BIOS answer — from a cached game detail or a live
 *  `get_bios_status` read, which ship the identical shape — into
 *  `{ biosStatus, biosLevel }`, or `null` when the payload carries no BIOS
 *  answer at all (`BiosAnswer` in `api/backend.ts`). The `"unknown"` level is
 *  an answer and is shown as one.
 *
 *  What a `null` does is each caller's: a live BIOS read marks `readFailed`
 *  and keeps any held status (`panelState.ts::foldBiosRead`,
 *  `EmulationSettings.tsx::foldBiosAnswer`), a core change keeps any shown
 *  status and re-reads (`panelEvents.ts`, `gameDetailStore.ts`), and an initial
 *  open or version switch holds no status for the new ROM until a read answers.
 *
 *  `bios_level` is computed by the backend (`compute_bios_level`) and threaded
 *  straight through, never re-derived. */
export function biosFieldsFromCache(
  cached: BiosAnswer,
): { biosStatus: BiosStatus; biosLevel: BiosLevel | null } | null {
  if (cached.bios_status_unknown) {
    if (cached.bios_level !== "unknown") return null;
    return { biosStatus: { ...cached.bios_status, ...UNKNOWN_REQUIREMENT_STATUS, files: [] }, biosLevel: "unknown" };
  }
  if (!cached.bios_status) return null;
  return {
    biosStatus: { needs_bios: true, ...cached.bios_status },
    biosLevel: cached.bios_level ?? null,
  };
}
