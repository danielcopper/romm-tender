/**
 * Can Tender fetch this firmware row?
 *
 * A row is fetchable where a RomM library file fills it (`library`), RomM
 * states a checksum the download can be checked against, this platform's
 * firmware store holds no copy yet (`store_file`), nothing that is not
 * Tender's lies at its place (`link: "occupied"` — that place offers
 * "Replace…" or nothing), and the resolver could look at its place. A place
 * Tender's link into another platform's store folder already serves is still
 * fetchable: the download lands in this platform's own folder. Every library
 * row of the platform is fetchable on the same terms, also one no emulator
 * asks for, which lands in the store and is linked once an emulator does.
 *
 * A row from a payload that states no `library` is read by the rule before the
 * store: the library holds it, it is not at its destination, it is no folder,
 * and its place could be looked at.
 *
 * The download affordances of the BIOS table both a platform's page and a
 * game's BIOS tab draw (`bigpicture/bios/BiosFiles.tsx`) are built off it — a
 * row it offers a button for is exactly a row this answers `true` for. A second
 * copy of the expression would let the two drift.
 *
 * This is the fetchability axis alone: readiness is not an input here in any
 * of its shapes, and this answer is an input to no count.
 */

import type { FirmwareDeclaredKind, LibraryMatch, PlaceLink, PlaceRefusal } from "../types";

/** The fields the answer is read off — the shape both row types share. */
export interface FetchableRow {
  on_server?: boolean;
  downloaded: boolean;
  declared_kind?: FirmwareDeclaredKind;
  caveats?: readonly string[];
  library?: LibraryMatch | null;
  store_file?: string | null;
  link?: PlaceLink | null;
  place_refusal?: PlaceRefusal | null;
}

/** The resolver's code for a place it could not look at — permissions, or
 *  failing storage. One spelling for every reader of it in the panel. */
export const PATH_INACCESSIBLE = "firmware-path-inaccessible";

/** Whether Tender can download this row from the user's RomM library. */
export function isFetchable(file: FetchableRow): boolean {
  const unreadable = (file.caveats ?? []).includes(PATH_INACCESSIBLE) || file.place_refusal?.reason === "unreadable";
  if (file.library === undefined) {
    return file.on_server === true && !file.downloaded && file.declared_kind !== "directory" && !unreadable;
  }
  return (
    file.library !== null && !file.library.no_checksum && !file.store_file && file.link !== "occupied" && !unreadable
  );
}

/** The name a download of *file* is asked for by: the library file's name in RomM, which may not be the row's. */
export function downloadName(file: { file_name: string; library?: LibraryMatch | null }): string {
  return file.library?.file_name ?? file.file_name;
}

/**
 * Does "Download required" fetch this row? The backend's one rule, carried on
 * the row (`fetch_for_required`) so the count on the button and the download it
 * starts cannot disagree. A payload from before the field existed answers with
 * `required_by_active`, which is what the rule was before a one-of group joined
 * it.
 */
export function fetchedAsRequired(file: { fetch_for_required?: boolean; required_by_active?: boolean }): boolean {
  return file.fetch_for_required ?? file.required_by_active === true;
}
