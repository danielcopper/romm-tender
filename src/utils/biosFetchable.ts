/**
 * Can this plugin fetch this firmware row?
 *
 * Three clauses, and each excludes a row for its own reason: the RomM library
 * has to hold the file (`on_server`), it must not already be at its destination
 * (`downloaded`), and the declaration must name a FILE. A declared **folder** is
 * out whatever its state — the emulator lists that name, so there is nothing to
 * fetch into it; what would satisfy it is a BIOS image inside the folder, which
 * is a different row. That is also why the backend stamps every folder row
 * `on_server: False` and why the download batch passes such a row over.
 *
 * It lives here because two surfaces ask it for two different purposes and must
 * never disagree. The **platform detail** builds its three download affordances
 * off it — a row it offers a button for is exactly a row this answers `true`
 * for. The **game page's BIOS tab** asks it as one of the answers that earn a
 * row a line at all, because a file no page can fetch, that this launch does not
 * require and that is not there, is nothing that page can act on. A second copy
 * of the expression would let the two drift: the game page would point at a
 * button the platform page does not offer, or leave off a row it does.
 *
 * This is the fetchability axis alone. What each surface DOES with the answer is
 * its own — the game page's rule for keeping a row is four further answers wide
 * and belongs to that page; the platform page has no such rule and must not grow
 * one. Readiness is not an input here in any of its shapes, and this answer is
 * an input to no count: `required_count` is the emulator's demand and
 * `local_count` / `server_count` are the library's inventory, each read
 * elsewhere and each wrong as a substitute for the others.
 */

import type { FirmwareDeclaredKind } from "../types";

/** The fields the answer is read off — the shape both row types share.
 *  `on_server` is optional because the game page's row type leaves it out of an
 *  older payload; absent is not fetchable, which is the safe direction and the
 *  same answer the truthiness test gave before this moved here. */
export interface FetchableRow {
  on_server?: boolean;
  downloaded: boolean;
  declared_kind?: FirmwareDeclaredKind;
}

/** Whether the plugin can download this row from the user's RomM library. */
export function isFetchable(file: FetchableRow): boolean {
  return file.on_server === true && !file.downloaded && file.declared_kind !== "directory";
}
