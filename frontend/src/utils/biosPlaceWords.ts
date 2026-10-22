/**
 * What a BIOS row says about Tender's own part in it: how its library file
 * fills it, what lies at its place, why no link may go there, and what the last
 * attempt on this page did — one line each, under the row, on both surfaces.
 *
 * The verdict and its causes are `biosFileNote`'s; these lines are about the
 * firmware store and the links into the emulator sources, which the backend
 * states as fields (`library`, `link`, `place_refusal`) and this module only
 * words. What happened on this page — a link that could not be placed, a file
 * replaced, a download that arrived damaged — comes from the latest result and
 * is kept by the page, never recorded (`docs/architecture/qam-panel.md`,
 * Library, "BIOS files").
 */

import type { BiosFileStatus, BiosKeptLink, StoreOnly } from "../types";

/** The fields the lines are read off — both surfaces' row shapes carry them. */
export type PlaceRow = Pick<
  BiosFileStatus,
  "library" | "link" | "linked_for" | "place_refusal" | "satisfied" | "local_path" | "store_file" | "declared_by"
>;

/** A row's download that arrived damaged, in the words the backend refuses it with. */
export const DOWNLOAD_DAMAGED = "The download was damaged. Try again.";

/** What this page did to the row in its latest attempt, which the page keeps until it is left. */
export interface RowAttempt {
  /** Why a link could not be placed at the row's place. */
  linkFailure?: string;
  /** Where the file a "Replace…" moved aside went. */
  replacedTo?: string;
  /** Whether the row's download arrived damaged. */
  damaged?: boolean;
}

/**
 * The lines under one requirement row. *emulator* names the emulator the row
 * is shown for, which the missing-folder line asks the reader to start;
 * *platformName* names a platform by its slug, where the surface knows the
 * name, for the line about another platform's store copy.
 */
export function biosPlaceLines(
  row: PlaceRow,
  emulator: string | null,
  attempt: RowAttempt = {},
  platformName: (slug: string) => string = (slug) => slug,
): string[] {
  const lines: string[] = [];
  const library = row.library ?? null;
  if (library?.no_checksum) {
    lines.push(`RomM states no checksum for ${library.file_name}, so Tender cannot check a download.`);
  }
  if (library?.matched === "archive_name") lines.push("Matched by name: the archive's contents cannot be compared");
  if (library?.matched === "no_checksum_name") lines.push("Matched by name: this file has no known checksum");
  const refusal = row.place_refusal ?? null;
  if (refusal?.reason === "folder_missing") {
    lines.push(`${refusal.folder} does not exist yet. Start ${emulator ?? "the emulator"} once, then try again.`);
  }
  if (refusal?.reason === "root_assumed") {
    lines.push(`RetroDECK's settings could not be read, so Tender places nothing in ${refusal.folder}.`);
  }
  lines.push(...linkLines(row, platformName));
  if (attempt.linkFailure) lines.push(`Could not place a link at ${row.local_path}: ${attempt.linkFailure}`);
  if (attempt.damaged) lines.push(DOWNLOAD_DAMAGED);
  if (attempt.replacedTo) lines.push(`Replaced. The old file is in ${attempt.replacedTo}.`);
  return lines;
}

function linkLines(row: PlaceRow, platformName: (slug: string) => string): string[] {
  switch (row.link) {
    case "not_linked":
      return ["In Tender's firmware store, not linked here yet"];
    case "linked_elsewhere":
      return [`Linked to Tender's copy for ${platformName(row.linked_for ?? "")}`];
    case "occupied":
      if (row.satisfied === false) return ["Another file lies here, and the emulator does not accept it"];
      return row.satisfied === null ? ["Another file lies here; it could not be checked"] : [];
    default:
      return [];
  }
}

/** What the resolver's identification said about a library file, as the page holds it. */
export type Identification = "checking" | "unknown" | readonly string[];

/**
 * The lines under one row of the library's own section: what the file's
 * content is, where the identification has answered, and that its store copy
 * is used by no emulator.
 */
export function libraryRowLines(row: PlaceRow, identification: Identification | undefined): string[] {
  const lines: string[] = [];
  if (identification === "checking") lines.push("Checking…");
  else if (Array.isArray(identification)) {
    lines.push(identification.length > 0 ? `Recognised as ${identification.join(", ")}` : "Not recognised");
  }
  if (row.store_file) lines.push("In Tender's firmware store, used by no emulator");
  return lines;
}

/** The one sentence above the table where a download lands in the store and is linked nowhere. */
export function storeOnlySentence(
  storeOnly: StoreOnly | null | undefined,
  emulator: string | null,
  platform: string,
): string | null {
  if (storeOnly === "unsupported") {
    return `${emulator ?? "This emulator"} is not supported yet. Downloads are kept in Tender's firmware store and linked once it is.`;
  }
  if (storeOnly === "no_source") {
    return `No emulator source is set up for ${platform}. Downloads are kept in Tender's firmware store and linked once one is.`;
  }
  return null;
}

/**
 * The delete's lines for the links it left, one per folder: a link under a
 * root RetroDECK's settings did not name stays until they can be read. A
 * folder the answer does not know is the one the link lies in.
 */
export function keptLinkLines(kept: readonly BiosKeptLink[] | undefined): string[] {
  const folders = new Set<string>();
  for (const entry of kept ?? []) {
    if (entry.kind !== "link") continue;
    folders.add(entry.folder ?? entry.place.slice(0, Math.max(entry.place.lastIndexOf("/"), 0)));
  }
  return [...folders].map(
    (folder) =>
      `Left a link in ${folder}: RetroDECK's settings could not be read. Tender removes it once they can be read.`,
  );
}

/** The download's lines for each file it found in the store without a record and moved aside before fetching. */
export function backupLines(backups: readonly string[] | undefined): string[] {
  return (backups ?? []).map((path) => {
    const name = path.slice(path.lastIndexOf("/") + 1);
    return `A different ${name} was already in Tender's firmware store. It was moved to ${path}.`;
  });
}

/** The status line after a download or a link update, where something stayed in the way; `null` where nothing did. */
export function placedSentence(result: { placed?: number; left_in_place?: number }): string | null {
  const left = result.left_in_place ?? 0;
  if (left === 0) return null;
  return `${result.placed ?? 0} placed, ${left} left in place: another file lies there`;
}
