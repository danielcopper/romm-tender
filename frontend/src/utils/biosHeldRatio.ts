/**
 * The library's own ratio — `(d/t files held)` — in the one form both BIOS
 * surfaces state it.
 *
 * What it counts is a THIRD set, which is why it is not part of the sentence it
 * rides behind (`utils/biosSummary.ts`): `server_count` / `local_count` are what
 * the RomM library holds for the platform, where the sentence counts what the
 * launching emulator requires. The two are stated next to each other in every
 * one of the seven states because one number built out of both would be true of
 * neither. Sharing the FORM is not the same move as folding the meanings: what
 * lives here is how the pair is written, and the two sets stay apart.
 *
 * **A library that holds nothing for the platform gets no ratio at all** —
 * `(0/0 files held)` counts a set that does not exist.
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
 *  the answer is built from, so a wording change moves the lock with it. */
export const HELD_RATIO_PHRASE = "files held";

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
