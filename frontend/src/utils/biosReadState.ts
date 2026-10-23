/**
 * Where a BIOS answer stands on a page that reads one: the platform page for a
 * platform, a game's BIOS tab for its ROM. Both pages word the three states, and
 * both derive them here, so the two cannot come to tell them apart differently.
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
