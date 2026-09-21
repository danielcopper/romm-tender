/**
 * The library's own ratio — `(d/t RomM library files)` — in the one form both
 * BIOS surfaces state it.
 *
 * **The words name the set**, because the sentence it rides behind
 * (`utils/biosSummary.ts`) counts a different one and the numbers cannot say
 * which is which. What a reader gets out of that, on each of the two surfaces,
 * is `docs/architecture/qam-panel.md` → BIOS files.
 *
 * The pair is the RomM library's inventory for the platform: `server_count` is
 * what the library holds, and `local_count` how many of those the plugin found
 * at their destination. The rest of the tree calls that axis the **held/offered
 * ratio** (`domain/bios_status.py`, `types/firmware.ts`, CONTEXT.md → Library
 * inventory) — the same two numbers under the name the code gives them, which is
 * the name to search for when this wording does not appear.
 *
 * Naming the set is also why the pair need not be folded INTO that sentence. The
 * two are stated next to each other in every one of the seven states because one
 * number built out of both would be true of neither. Sharing the FORM is not the
 * same move as folding the meanings: what lives here is how the pair is written,
 * and the two sets stay apart.
 *
 * **A library that holds nothing for the platform gets no ratio at all** —
 * `(0/0 RomM library files)` counts a set that does not exist.
 *
 * **A payload carrying neither count gets none either.** Those two numbers are
 * the only thing on the wire that states what the library holds. The rows can be
 * counted the same way the backend counts them — the `on_server` rows, and the
 * downloaded ones among them (`services/firmware/status.py::_bios_aggregates`) —
 * and that is the road not taken: it is a second copy of a counting rule living
 * across the wire from the rule it copies, so a surface would state a number
 * nothing told it and would keep stating it after the backend's rule moved.
 * Withholding the line costs a reader one fact; re-deriving it risks stating a
 * different amount from the one the other surface was handed. Neither reading is
 * reachable today and both answer the same nothing: of the payloads these two
 * surfaces read, the only ones carrying no counts are the two `needs_bios: False`
 * exits, and those are taken exactly where there is no row to count either.
 */

/** The payload fields the ratio is read off. The game page's `BiosStatus` and
 *  the platform pane's `FirmwarePlatformExt` both satisfy it structurally, which
 *  is what lets one function serve both without either importing the other. */
export interface HeldRatioSource {
  server_count?: number;
  local_count?: number;
}

/** The fixed run the drift lock searches the two surfaces for — the same string
 *  the answer is built from, so a wording change moves the lock with it.
 *
 *  **The run has to name the SET rather than what is being counted about it**,
 *  because the lock is a substring search over whole component sources
 *  (`test-utils/componentSources.ts`): `present` is a substring of prose those
 *  files write for their own reasons, so a lock over it would fail on components
 *  that never mentioned the ratio. `RomM library files` is a substring of none of
 *  them.
 *
 *  It carries no digit and no parenthesis either. A digit cannot stand in a fixed
 *  run at all — the two numbers are per-platform — so a run that grew one would
 *  match nothing rendered anywhere, a component's own copy included; and the
 *  brackets are the tail's FORM, which the function below owns, so keeping them
 *  out leaves the locked run as words alone. */
export const HELD_RATIO_PHRASE = "RomM library files";

/**
 * The ratio as it is appended to a BIOS sentence, or the empty string.
 *
 * The leading space is part of the answer: the ratio is a tail, and a caller
 * adding the space itself would have to repeat the "or nothing at all" test to
 * know whether to.
 */
export function biosHeldRatio(source: HeldRatioSource): string {
  const total = source.server_count ?? 0;
  const done = source.local_count ?? 0;
  return total > 0 ? ` (${done}/${total} ${HELD_RATIO_PHRASE})` : "";
}
