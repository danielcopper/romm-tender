/**
 * What one BIOS file row says about itself — its marks, legend entries,
 * Contents cell, note, secondary lines, and description — shared across the
 * platform and game BIOS tables (alongside placement/attempt lines in
 * `biosPlaceWords.ts` and need/group wording in `biosGroup.ts`).
 *
 * Plain absence is stated by the On-disk mark ({@link biosDiskMark}, paired
 * with {@link biosLibraryMark} and explained by {@link biosLegendEntries})
 * rather than in prose; the `Contents` cell ({@link contentsWords}) words the
 * byte/folder check, {@link biosFileNote} and {@link biosTableRowLines} produce
 * the row's note and secondary lines, {@link declaredFolder} extracts any
 * declared subdirectory prefix, and {@link biosFileDescription} cleans the
 * packager's `.info` description.
 *
 * **A row is headed by the file the emulator DECLARED, never by its
 * description** — `declared_path`, folder and all, which the table splits into
 * a muted prefix and a name.
 * The description is the packager's prose out of a core's `.info`, deliberately
 * outside the resolver's contract, so it is not a field to render as a row's
 * identity — and it routinely spells the name into its own words besides, which
 * is what {@link biosFileDescription} takes back out.
 *
 * Precedence in {@link biosFileNote} is deliberate. A file the distribution
 * itself put there is not a gap in the RomM library — it is not the library's
 * file at all, and telling the user it is missing from RomM is true and
 * useless — so that note wins. What the reading established about the row comes
 * next, because a folder's contents and a destination something else occupies
 * are neither "missing" nor "present". Only then does the library note stand,
 * which is honest for every row nothing else was established for.
 *
 * The CAUSE of a verdict comes from the resolver's caveat codes and `checked`,
 * because `satisfied` is the verdict alone and carries none of it: `verdictNote`
 * branches on `declared_kind` (`"directory"` reads `satisfied` plus folder
 * caveats; a declared file reads `checked` and file caveats).
 */

import type { BiosFileStatus, FirmwarePlatformExt } from "../types";
import { BIOS_LIBRARY_VIOLET, BIOS_MISSING_RED, BIOS_MUTED_GREY, BIOS_OK_GREEN, BIOS_SPARE_GREEN } from "./biosColor";
import { PATH_INACCESSIBLE } from "./biosFetchable";
import { unmetWord } from "./biosGroup";
import { lastPathSegment } from "./formatters";

/** A row the BIOS table renders: a platform answer's own `files`, a game's, or
 *  another emulator's rows, which carry no server record of their own. */
export type BiosTableRow = FirmwarePlatformExt["files"][number] | BiosFileStatus;

/** One glyph in a BIOS row's On-disk cell, with its colour and tooltip. */
interface BiosMark {
  glyph: string;
  color: string;
  title: string;
}

/** One entry in the mark legend below a BIOS table. */
interface BiosLegendEntry {
  glyph: string;
  color: string;
  text: string;
}

/** The subset of a row this note is derived from — a platform answer's row and
 *  a game's both satisfy it, so neither has to be converted into the other. */
export type BiosNoteRow = Pick<
  BiosFileStatus,
  | "downloaded"
  | "on_server"
  | "supplied_by"
  | "satisfied"
  | "declared_kind"
  | "caveats"
  | "images"
  | "checked"
  | "missing_configured_image"
  | "configured_image_not_offered"
> &
  Partial<Pick<BiosFileStatus, "file_name" | "declared_path" | "local_path">>;

/** The subset {@link biosFileDescription} reads. */
export type BiosDescriptionRow = Pick<
  BiosFileStatus,
  "file_name" | "description" | "declared_kind" | "declaration" | "caveats"
>;

/**
 * Everything a row says about itself: one sentence, and the lines under it.
 *
 * Both come back together because they are alternatives as often as they are
 * companions — a folder that lists what it holds needs no sentence saying it
 * holds something — and a table taking one without the other would render a
 * satisfied folder as a bare name. The table puts them under the row they
 * belong to, beside the note, because its own cells are too narrow to hold
 * either.
 */
export interface BiosFileWords {
  /** The sentence under the row, or `""` where there is none. */
  note: string;
  /** One line each, rendered under the row: the images a folder's read
   *  identified, where the list IS the content, and the neutral lines saying an
   *  emulator's setting names a file the folder does not hold, or one its core
   *  does not offer. */
  lines: string[];
  /**
   * The note is the library one — "not in your RomM library" and its missing
   * variant — rather than anything the reading established.
   *
   * It is the one note that says the same thing on every row it applies to, and
   * on a platform whose library holds little it applies to nearly all of them.
   * The table carries that fact in the row's own cell instead, so it drops the
   * sentence and keeps the rest; the flag exists so it need not re-derive this
   * function's precedence to know which note it got.
   */
  fromLibrary: boolean;
}

/** A directory is at a destination the emulator opens as a file, so nothing
 *  about what belongs there can be established — and it is not an absent file. */
const PATH_OBSTRUCTED = "firmware-path-obstructed";
/** A plain file sits where the core lists a folder: it has no inside, so the
 *  listing the core makes at load reaches nothing however right the file looks. */
const PATH_NOT_A_DIRECTORY = "firmware-path-not-a-directory";
/** The core's own test was made over every candidate and none passes it, or the
 *  folder holds no file of a size the core would even open. */
const HOLDS_NO_IMAGE = ["firmware-directory-holds-no-image", "firmware-directory-holds-no-candidate"];
/** The identity table names the bytes and the core's own header check denies
 *  them: two reads that disagree, and neither is taken over the other. */
const IMAGE_CONTRADICTED = "firmware-image-contradicted";
/** The reading identified the file by its CONTENT — the emulator's own search of
 *  the BIOS folder found it — whether or not its packaged table also files
 *  those bytes. */
const IDENTIFIED_BY_CONTENT = ["firmware-image-identified", "firmware-image-unlisted"];
/** What a file found that way says about itself, in place of the resolver's
 *  own prose about its search. */
const FOUND_IN_THE_BIOS_FOLDER = "found in the BIOS folder";
/** The folder could not be listed in full, or a candidate's bytes would not come
 *  back — a read failure, never a finding about what is in there. */
const READ_INCOMPLETE = ["firmware-scan-incomplete", "firmware-unreadable"];

/**
 * What the reading established about *row*, or `""` where it has nothing to add.
 *
 * A satisfied folder lists what it holds, in the resolver's own words — those
 * are the core's own option labels, and the core needs exactly one of them, so
 * they carry no per-image required/optional marking and no mark for which one
 * will be loaded (that is a core option Tender does not read).
 *
 * There is deliberately no branch for `firmware-search-unverified`, the code for
 * a folder whose candidates were never hashed. The backend asks the folder
 * question with verification on, so the resolver never emits it there; and the
 * unverified machine-wide reading emits it only for a row it leaves open, which
 * is exactly the row whose codes that reading does not carry. So it reaches no
 * row at all. A folder whose contents genuinely went unread lands on the
 * fallback below instead, and there are three ways to get there: the verified
 * read failed, the platform's scope never covered the core so nothing asked, or
 * the folder could not be looked at — the last arriving with
 * `firmware-path-inaccessible`, which is worded only on the file half. The
 * fallback claims nothing either way, which is the honest answer to all three.
 *
 * An unreadable destination leaves the verdict withheld for a folder and a file
 * alike — the resolver answers `satisfied: None` over a path it could not look
 * at. A folder row says "its contents could not be checked"; a file row names
 * the place that could not be looked into (`placeNotLookedAt`). Both notes are
 * statements about the read, and neither claims the file is or is not there.
 */
function verdictNote(row: BiosNoteRow, emulator: string | null): BiosFileWords {
  const has = (code: string) => (row.caveats ?? []).includes(code);
  if (row.declared_kind !== "directory") {
    if (has(PATH_INACCESSIBLE) && !has(PATH_OBSTRUCTED)) return said(placeNotLookedAt(row));
    return fileAtItsDestination(has, row.checked, emulator);
  }
  if (row.satisfied === true) return folderHolding(row.images ?? []);
  if (row.satisfied === false) return folderUnmet(has);
  return folderWithheld(row.satisfied, has);
}

/**
 * A file whose place the resolver could not look at, naming that place.
 *
 * "Tender could not look into bios/kronos/, so whether saturn_bios.bin is there
 * is not known" — a statement about the read, never that the file is missing,
 * and the reason the row offers no download: one there could overwrite
 * a file nobody could see. The place is the declared folder under the BIOS
 * folder's own name, read off `local_path`, which is the declared path joined
 * under that folder.
 */
function placeNotLookedAt(row: BiosNoteRow): string {
  const declared = row.declared_path || row.file_name || "";
  const slash = declared.lastIndexOf("/");
  const folder = declared.slice(0, slash + 1);
  const name = declared.slice(slash + 1);
  const local = row.local_path ?? "";
  const root = declared && local.endsWith(declared) ? local.slice(0, local.length - declared.length) : "";
  const rootName = lastPathSegment(root);
  const place = rootName ? `${rootName}/${folder}` : folder || "the BIOS folder";
  const what = name || "the file";
  return `Tender could not look into ${place}, so whether ${what} is there is not known`;
}

/** One line and nothing under it — every group but a satisfied folder's. */
const said = (note: string): BiosFileWords => ({ note, lines: [], fromLibrary: false });

/**
 * A declared FILE: what the reading found at the place the emulator opens.
 *
 * The shape of the destination comes first — a folder standing where a file is
 * opened says nothing about bytes, because there were none to read. Then what
 * became of the bytes, which is `checked` and is five distinct things the row's
 * verdict cannot tell apart:
 *
 * - **`unrecognised`** — the emulator read the file and no table it keeps knows
 *   these bytes. It WAS checked, which is why it may not be worded as a read
 *   that failed; and it is no failure either — DuckStation boots such an image
 *   and says it is using an unknown BIOS — so the verdict stays withheld and the
 *   note says what was established rather than what was not.
 * - **`unread`** — the bytes were asked for and did not come back. It is a
 *   statement about the BACKEND's read rather than about the emulator's: that
 *   this process could not read the file is no evidence the launch cannot.
 * - **`refused`** — the emulator will not open the file at all, on its size,
 *   before reading a byte. It arrives with the verdict already `false`, so the
 *   row is unmet with or without this note; what the note adds is the reason,
 *   in the Contents column's words, and without it the row says a file that is
 *   sitting right there is missing. The resolver reaches that size gate only
 *   for a file a per-region BIOS setting NAMES — SwanStation's
 *   (`swanstation_BIOS_PathNTSCJ` / `PathNTSCU` / `PathPAL`) or DuckStation's
 *   (`PathNTSCU` / `PathNTSCJ` / `PathPAL`), cited in
 *   `backend/_vendor/atlas/data/core_firmware.json` and
 *   `backend/_vendor/atlas/data/standalone_firmware.json`. Whether it reaches a
 *   ROW is a further question that has not been shown: a row carries its own
 *   emulator's reading of the name, and for SwanStation's default names that
 *   may be its `.info` declaration rather than the one the setting names.
 *
 * - **`mismatch`** — the file is there and is not the image *emulator* expects.
 *   Its verdict is `false`, and without the sentence the row would read as a
 *   file that is missing. The words are the Contents column's, so
 *   both pages say it alike.
 * - **`rejected`** — Tender's own word for a file in a declared folder the
 *   emulator's test read and turned down: a wrong file too, but against no
 *   one pinned image, so it is not an image the emulator accepts rather than
 *   not the one it expects.
 *
 * `verified` gets no note: it is the ordinary met row. Every other value, and
 * the absent field, leave the note to the library half below.
 */
function fileAtItsDestination(
  has: (code: string) => boolean,
  checked: BiosNoteRow["checked"],
  emulator: string | null,
): BiosFileWords {
  if (has(PATH_OBSTRUCTED)) return said("a folder is here, where the emulator opens a file");
  if (checked === "unrecognised") return said("the emulator does not recognise this file");
  if (checked === "unread") return said("its bytes could not be read");
  if (checked === "refused") return said(refusedFile(emulator));
  if (checked === "mismatch") return said(wrongFile(emulator));
  if (checked === "rejected") return said(rejectedFile(emulator));
  return said("");
}

/**
 * A folder whose read found an image — the one group whose content is a list.
 *
 * The images ARE the sentence here. "holds" above a list of three would be a
 * heading for a list that needs none, and folding them into the row's own name
 * would cut that name off in its table cell.
 */
function folderHolding(images: string[]): BiosFileWords {
  return images.length > 0 ? { note: "", lines: [...images], fromLibrary: false } : said("holds a BIOS image");
}

/** A folder shown to hold nothing the core would boot, or none at all. */
function folderUnmet(has: (code: string) => boolean): BiosFileWords {
  if (has(PATH_NOT_A_DIRECTORY)) return said("a file is here, where the emulator opens a folder");
  return said(HOLDS_NO_IMAGE.some(has) ? "holds no BIOS image" : "");
}

/** A folder the read established nothing about, worded off why it could not. */
function folderWithheld(satisfied: boolean | null | undefined, has: (code: string) => boolean): BiosFileWords {
  if (has(IMAGE_CONTRADICTED)) return said("holds an image that could not be confirmed");
  if (READ_INCOMPLETE.some(has)) return said("its contents could not be read in full");
  return said(satisfied === null ? "its contents could not be checked" : "");
}

/**
 * The description on the line under a file's name, with the name itself taken
 * back out.
 *
 * The row is headed by the declared file, so the description must leave out
 * what that name already says. The table sets it on a muted line under the row,
 * because the name sits in a clipping table cell narrow enough that a label
 * beside it would cut the name itself.
 *
 * **It is not RomM's description** — `_server_files` builds no `description`
 * key at all, and `_wanted_fields` overwrites whatever came in.
 * What arrives is the core's own `firmwareN_desc` out of its `.info` file, the
 * description of a declaration atlas packages (which the `packaged` rule below
 * hides), or, for a row no placement covers, the file name itself
 * (`build_file_entry`'s `else file_name`). The first and the last spell the
 * name into the words.
 *
 * **A file the reading identified by its contents is described as "found in
 * the BIOS folder"**, whichever declaration it came from, and that is decided
 * first: the emulator's own search found it, and the resolver's prose for such a
 * file explains the search rather than the file.
 *
 * **Otherwise only a `read` declaration's prose is shown at all**. That prose is a packager's LABEL for the file and says
 * what the row's own name does not — `(PS1 JP BIOS)` on `scph5500.bin`, a
 * region the name never states. A `packaged` row's is a different
 * kind of writing under the same field: atlas explaining the requirement in
 * whole sentences ("a PlayStation BIOS image — the console runs it before any
 * disc, and DuckStation starts nothing without one — found by the search, not
 * named by any setting"), which is an essay on a line sized for a label, and it
 * breaks off mid-sentence there. So the register is read off the DECLARATION
 * and never guessed from the row — an identity ending in `_libretro.so` is a
 * libretro core today and is the resolver's spelling to change, and a row is
 * declared by several emulators while this prose comes from exactly one of them
 * ({@link FirmwareDeclarationState}). A row that states no declaration shows
 * none either: its description is the file name (above), which the rules below
 * take out anyway.
 *
 * Measured over the 292 `.info` files a stock RetroDECK ships — 695 declared
 * firmware entries — the description's relation to the row's own `file_name`
 * (which is `os.path.basename` of the declared path) falls into six shapes:
 *
 * | 245 | 35% | it IS the name — `"macventure.dat"`                          |
 * | 328 | 47% | the name, a space, then prose — `"scph5500.bin (PS1 JP BIOS)"` |
 * | 115 | 17% | the same, but the name carries its directory — `"dc/dc_boot.bin (Dreamcast BIOS)"` |
 * |   5 |  1% | the first token names something else — a folder the file sits in (`"'Databases' folder"`), or a misspelling of it (two upstream typos) |
 * |   1 |  0% | it names the file, but the name has a space in it — `"7800 BIOS (U).rom (7800 BIOS)"` |
 * |   1 |  0% | it names the file in quotes — `"'pcsx2/bios' folder"`, the corpus's only folder declaration |
 *
 * So the rule has two halves: strip the name where the description opens with
 * it verbatim (which is the only way a name containing spaces can be seen), and
 * otherwise strip a first token that names this file — as itself or at the end
 * of a path, with surrounding quotes ignored.
 * Together they fire on 690 of the 695 and on the no-placement case; the
 * remaining five say something real and are printed whole. The name half is
 * anchored at the start rather than searched for anywhere, because a rule that
 * scanned the whole string would cut into prose that merely quotes the name.
 * The prose is kept verbatim, parentheses and all, because it is the packager's
 * own words and re-punctuating it is a second way to be wrong.
 *
 * The counts were taken over the deployed flatpak with
 * `grep -o … | wc -l`-style matching per entry rather than per line: the shapes
 * are counted by classifying every `firmwareN_path` / `firmwareN_desc` pair,
 * which is reproducible by re-running that classification over the same tree.
 */
export function biosFileDescription(file: BiosDescriptionRow): string | null {
  // A declared FOLDER shows none. Its meaning is its verdict and the images
  // listed under it — LRPS2 never reads a file name, so what the row says is
  // "this folder holds something the core will boot", which `✓` and the image
  // lines already say. The corpus's one folder is described as
  // `'pcsx2/bios' folder`, which after the name comes out leaves the bare word
  // "folder": a restatement of `declared_kind`. This is a rule about what a
  // folder ROW shows, not a prediction about what descriptions exist.
  if (file.declared_kind === "directory") return null;
  // A file the reading identified by its contents was found by a search of the
  // BIOS folder, and the resolver's description of such a file explains that
  // search ("named by no option"). Where the file is is what a reader needs.
  if (foundByContent(file)) return FOUND_IN_THE_BIOS_FOLDER;
  if (file.declaration !== "read") return null;
  const description = file.description.trim();
  if (!description) return null;
  // A name with a space in it is not one token, so the token rule cannot see it.
  // Exactly one of the 695 is spelled that way ("7800 BIOS (U).rom"), and it
  // printed the name twice until this line. Anchored at the start rather than
  // searched for anywhere, so prose that merely quotes the name is left alone.
  if (description.startsWith(`${file.file_name} `)) {
    return description.slice(file.file_name.length).trim() || null;
  }
  const [head, ...tail] = description.split(" ");
  // Quotes are stripped before the comparison, because the corpus's one folder
  // declaration is described as `'pcsx2/bios' folder` — a token that names the
  // declaration exactly, which the row's own name line is already showing, and
  // which nothing else would have removed. Comparing the whole declared path as
  // well would change no outcome: `file_name` is its basename, so a token
  // equalling the path always equals the basename after the split too.
  const token = (head ?? "").replace(/^['"]|['"]$/g, "");
  if ((token.split("/").pop() ?? "") !== file.file_name) return description;
  const rest = tail.join(" ").trim();
  return rest || null;
}

/** The fields the Contents column is read off — the platform detail's row shape satisfies it. */
export type ContentsRow = Pick<BiosFileStatus, "declared_kind" | "satisfied" | "images" | "checked">;

/**
 * A BIOS table row's `Contents` cell — what the check found at the row's place,
 * in words.
 *
 * A declared FOLDER answers with what it holds: how many images, none, or that
 * nothing established it. A declared FILE answers off `checked` (the resolver's
 * word for what comparing its bytes established, plus `"rejected"`, Tender's
 * word for a file in a declared folder the emulator read and turned down); the
 * em dash means nothing was asked — the ordinary answer for a file that is not
 * there — and must never come to mean "asked and found nothing". *emulator* is
 * the shown or launching emulator's label, or `null` when unnamed (defaulting
 * to `"the emulator"`).
 *
 * Colour sits on the mark beside this muted cell ({@link biosDiskMark}), and
 * these words explain that mark without contradicting it: a wrong image is a
 * `✗` and "wrong file" — red where the file is required or a one-of group
 * option, the muted grey of a missing optional file otherwise — and one that
 * could not be compared a grey `?` and "not comparable". The word "unknown" is
 * never one of them — it would read as the need word under the file's name
 * (`needWords`).
 */
export function contentsWords(row: ContentsRow, emulator: string | null): string {
  if (row.declared_kind === "directory") {
    if (row.satisfied === true) {
      const held = row.images?.length ?? 0;
      if (held === 0) return "an image";
      return held === 1 ? "1 image" : `${held} images`;
    }
    return row.satisfied === false ? "no image" : CONTENTS_NOT_ESTABLISHED;
  }
  switch (row.checked) {
    case "verified":
      return "right file";
    case "mismatch":
      return wrongFile(emulator);
    case "rejected":
      return rejectedFile(emulator);
    case "refused":
      return refusedFile(emulator);
    case "not-comparable":
      return "not comparable — this kind of file changes with the emulator version";
    case "unrecognised":
      return "not recognised";
    case "unread":
      return "could not be read";
    case "unchecked":
      return "not checked";
    case "unknown":
      return CONTENTS_NOT_ESTABLISHED;
    default:
      return "—";
  }
}

const CONTENTS_NOT_ESTABLISHED = "not established — the resolver could not tell";

/** A file the emulator will not open on its size, in the shared table wording. */
function refusedFile(emulator: string | null): string {
  return `refused — ${emulator ?? "the emulator"} does not accept a file of this size`;
}

/** A wrong image, in the shared table wording — naming the emulator whose image it is not. */
function wrongFile(emulator: string | null): string {
  return `wrong file — not the image ${emulator ?? "the emulator"} expects`;
}

/** A file in a declared folder the emulator's own test read and turned down, in the shared table wording. */
function rejectedFile(emulator: string | null): string {
  return `wrong file — not an image ${emulator ?? "the emulator"} accepts`;
}

/** Did the reading identify *row*'s file by its contents — the emulator's search of the BIOS folder? */
export function foundByContent(row: { caveats?: string[] }): boolean {
  return IDENTIFIED_BY_CONTENT.some((code) => (row.caveats ?? []).includes(code));
}

/**
 * What *row* says about itself — an empty note where its own state is the whole story.
 *
 * The note is `""` for a plain library file, present or missing alike: "missing"
 * is the table's On-disk mark to say, not a word.
 * *emulator* names the emulator the row is shown for — the one a wrong image is
 * checked against, and whose settings name a file that is not there.
 */
export function biosFileNote(row: BiosNoteRow, emulator: string | null = null): BiosFileWords {
  const words = rowWords(row, emulator);
  const stale = row.missing_configured_image;
  const passedOver = row.configured_image_not_offered;
  const settings = [
    ...(stale ? [configuredImageLine(stale, emulator)] : []),
    ...(passedOver ? [notOfferedImageLine(passedOver, emulator)] : []),
  ];
  return settings.length > 0 ? { ...words, lines: [...words.lines, ...settings] } : words;
}

/**
 * The neutral line under a folder whose emulator is set to open a file in it
 * that is not there — "LRPS2's settings name scph10000.bin, which is not here —
 * it uses another BIOS from this folder instead."
 *
 * It changes nothing about the row: the emulator lists the folder instead, so
 * the folder's verdict is what the launch rests on, and the mark and the counts
 * read that verdict alone. The emulator is named as the surface names the
 * emulator it shows, *emulator*: the answer's `emulator_label` is the one label
 * of the entry the reading came from, and RetroDECK gives `pcsx2_libretro.so`
 * two, so the selector's "LRPS2 / PCSX2" would sit above a line naming LRPS2
 * alone. The answer's label stands only where the surface names none. The
 * file's name comes from the answer; a half nothing stated is left out of the
 * sentence rather than guessed.
 */
function configuredImageLine(
  stale: NonNullable<BiosNoteRow["missing_configured_image"]>,
  emulator: string | null,
): string {
  const name = emulator ?? stale.emulator_label;
  const whose = name ? `${name}'s settings` : "The emulator's settings";
  const named = stale.file_name ? `name ${stale.file_name}, which is not here` : "name a BIOS file that is not here";
  return `ℹ ${whose} ${named} — it uses another BIOS from this folder instead.`;
}

/**
 * The neutral line under a folder whose core option names a file the core does
 * not offer — "The core option names scph10000.bin, which LRPS2 does not offer
 * — it starts with a BIOS from this folder."
 *
 * Like {@link configuredImageLine} it changes nothing about the row: the core
 * starts with its default, a BIOS from the folder's own listing, so the folder's
 * verdict is what the launch rests on. The emulator and the file are named as
 * that line names them.
 */
function notOfferedImageLine(
  passedOver: NonNullable<BiosNoteRow["configured_image_not_offered"]>,
  emulator: string | null,
): string {
  const name = emulator ?? passedOver.emulator_label ?? "the emulator";
  const what = passedOver.file_name ? `${passedOver.file_name}, which` : "a BIOS file that";
  return `ℹ The core option names ${what} ${name} does not offer — it starts with a BIOS from this folder.`;
}

/** Everything the row says before the configured-image lines — see {@link biosFileNote}. */
function rowWords(row: BiosNoteRow, emulator: string | null): BiosFileWords {
  if (row.supplied_by) return { note: `provided by ${row.supplied_by}`, lines: [], fromLibrary: false };
  const verdict = verdictNote(row, emulator);
  if (verdict.note || verdict.lines.length > 0) return verdict;
  // No RomM library holds a folder — the emulator lists that name — so the
  // library note would describe a download nobody can make.
  if (row.declared_kind === "directory") return { note: "", lines: [], fromLibrary: false };
  if (row.on_server === false) {
    return {
      note: row.downloaded ? "not in your RomM library" : "missing, not in your RomM library",
      lines: [],
      fromLibrary: true,
    };
  }
  return { note: "", lines: [], fromLibrary: false };
}

/**
 * The second mark: your RomM library does not hold this file.
 *
 * It sits BESIDE the verdict mark and never in place of it — the two are
 * different questions, and a row that is present but unfetchable ("you have it,
 * but you could not fetch it again") is exactly the combination a single
 * channel would lose. Which is why it carries no need axis of its own: the
 * verdict mark beside it already says whether the file is wanted.
 *
 * `⊘` is not one of the verdict shapes and reads as "not available" rather than
 * as a degree of wrong; DejaVu Sans carries U+2298, the same SteamOS fontconfig
 * fallback the `✓`/`✗` glyphs rely on in this table.
 */
const LIBRARY_MARK: BiosMark = { glyph: "⊘", color: BIOS_LIBRARY_VIOLET, title: "not in your RomM library" };

/**
 * What each mark means — the row's own tooltip AND the legend's line for it, one
 * string serving both.
 *
 * They are the mark's IDENTITY, which is what the legend filters and keys on.
 * Glyph plus colour cannot be: a one-of group's options are a state of their
 * own, so red `✗` is two different sentences (a required file that is absent,
 * an option of a group whose region has nothing in place) and so is green `✓`.
 * Keyed on the pair, the legend would show one of each and give the second
 * React's duplicate key; keyed on the sentence, it shows every state present.
 */
// A `✗` says what the file IS where its verdict is not met — missing, the wrong
// file, or refused (`unmetWord`, off the row's `checked`) — so the mark never
// contradicts the Contents words beside it.
const markRequiredUnmet = (word: string) => `required, ${word}`;
const MARK_REQUIRED_HERE = "required, here";
const MARK_HERE = "here, not required";
const markUnmet = (word: string) => `${word}, not required`;
// The `?` glyph's word, true of every way a verdict is withheld — including a
// file the emulator READ and does not recognise. "could not be checked" is
// untrue there: DuckStation reads such an image, boots it, and calls it an
// unknown BIOS. So the mark states only that nothing settled either way, and
// WHY stays in the row's own note (`biosFileNote`).
const MARK_UNCHECKED = "nothing could establish this either way";
const MARK_HERE_NEED_UNKNOWN = "here; nothing could say whether this is wanted";
const MARK_MISSING_NEED_UNKNOWN = "missing; nothing could say whether this is wanted";
const MARK_ONE_OF_THESE_HERE = "one of these, here";
const markOneOfTheseUnmet = (word: string) => `one of these, ${word}`;
const UNMET_WORDS = [unmetWord(null), unmetWord("mismatch"), unmetWord("refused")];

/**
 * The row's verdict, as the payload states it or as its silence leaves it.
 *
 * A folder's verdict is what it HOLDS, never that the folder is there — the
 * register's rule (`docs/architecture/invariants/firmware.md`) — so a payload
 * carrying no verdict for one leaves the row unestablished rather than falling
 * back to presence; only a declared file without `satisfied` falls back to
 * `downloaded`.
 */
function rowVerdict(file: BiosTableRow): boolean | null {
  const isFolder = file.declared_kind === "directory";
  const fallback = isFolder ? null : file.downloaded;
  return file.satisfied !== undefined ? file.satisfied : fallback;
}

function metDiskMark(required: boolean, option: boolean): BiosMark {
  if (option) return { glyph: "✓", color: BIOS_OK_GREEN, title: MARK_ONE_OF_THESE_HERE };
  return {
    glyph: "✓",
    color: required ? BIOS_OK_GREEN : BIOS_SPARE_GREEN,
    title: required ? MARK_REQUIRED_HERE : MARK_HERE,
  };
}

function unmetDiskMark(file: BiosTableRow, required: boolean, option: boolean): BiosMark {
  const word = unmetWord(file.checked);
  if (option) return { glyph: "✗", color: BIOS_MISSING_RED, title: markOneOfTheseUnmet(word) };
  return {
    glyph: "✗",
    color: required ? BIOS_MISSING_RED : BIOS_MUTED_GREY,
    title: required ? markRequiredUnmet(word) : markUnmet(word),
  };
}

/**
 * The first mark in one row's `On disk` cell, as a glyph, colour and tooltip.
 *
 * Two facts, two channels:
 * - **Glyph (verdict):** `✓` met, `✗` not met, `?` unestablished, from
 *   {@link rowVerdict} (`BiosFileEntry.satisfied`, falling back to `downloaded`
 *   only for a non-directory row with `satisfied` absent — never folder
 *   presence, since RetroDECK links `pcsx2/bios` onto the BIOS root).
 * - **Every unestablished verdict or need is a grey `?`:** when `wanted` is
 *   `"unknown"`, the glyph is forced to grey `?` while its tooltip still states
 *   whether the row's verdict is met (`MARK_HERE_NEED_UNKNOWN`) or missing
 *   (`MARK_MISSING_NEED_UNKNOWN`) rather than calling the row not required.
 * - **Colour (need):** keyed on `required_by_active` rather than `wanted`,
 *   matching `biosSummary`'s count (`not_needed` and `optional` share muted
 *   grey when unmet and `BIOS_SPARE_GREEN` when met; amber is never a row mark,
 *   because `partial` is a group-level level). A one-of group option
 *   (`one_of` when `!required_by_active`) is the fifth state: strong green or
 *   red under its own words, checked after the `?` marks.
 */
export function biosDiskMark(file: BiosTableRow): BiosMark {
  const verdict = rowVerdict(file);
  if (verdict === null) return { glyph: "?", color: BIOS_MUTED_GREY, title: MARK_UNCHECKED };
  if (file.wanted === "unknown") {
    return {
      glyph: "?",
      color: BIOS_MUTED_GREY,
      title: verdict ? MARK_HERE_NEED_UNKNOWN : MARK_MISSING_NEED_UNKNOWN,
    };
  }
  const required = Boolean(file.required_by_active);
  const option = Boolean(file.one_of) && !required;
  return verdict ? metDiskMark(required, option) : unmetDiskMark(file, required, option);
}

/**
 * The row's second mark, or `null` where the library holds the file, where the
 * payload is silent about `on_server`, or where the row declares a directory.
 *
 * Appears beside `✓` and `✗` alike (a file downloaded before leaving the RomM
 * library still cannot be fetched again). `on_server === undefined` carries no
 * mark rather than a claim, and declared directories are excluded because no
 * RomM library holds a directory (so backend firmware rows stamp directory
 * entries `on_server: false`).
 */
export function biosLibraryMark(file: BiosTableRow): BiosMark | null {
  return file.on_server === false && file.declared_kind !== "directory" ? LIBRARY_MARK : null;
}

/**
 * The folder the emulator declared this file in, with its trailing slash, or
 * `null` for a file that belongs at the root of the BIOS directory.
 *
 * The row's own name is a basename, so without this the table cannot answer
 * which subdirectory a hand-placed file belongs in (`dc/dc_boot.bin`,
 * `ep128emu/roms/exos21.rom`) when the packager's description omits it.
 */
export function declaredFolder(file: BiosTableRow): string | null {
  const declared = file.declared_path;
  if (!declared?.includes("/")) return null;
  return `${declared.slice(0, declared.lastIndexOf("/"))}/`;
}

/**
 * The legend entries explaining the marks present across *files*.
 *
 * Filters to states present in *files* so absent states take no space, and
 * emits a single `⊘` entry at the end when any row carries
 * {@link biosLibraryMark} (since `⊘` means the same beside every verdict).
 * Ordered with unmet required and unmet group-option marks (`✗`) first, then
 * met required and met group-option marks (`✓`), unestablished marks (`?`),
 * met non-required (`✓` `"here, not required"`), unmet non-required (`✗`), and
 * `⊘` last — keeping each group-option mark beside the ordinary mark that
 * shares its colour.
 */
export function biosLegendEntries(files: readonly BiosTableRow[]): BiosLegendEntry[] {
  const marks = files.map((file) => biosDiskMark(file));
  const shown: BiosLegendEntry[] = [
    ...UNMET_WORDS.map((word) => ({ glyph: "✗", color: BIOS_MISSING_RED, text: markRequiredUnmet(word) })),
    ...UNMET_WORDS.map((word) => ({ glyph: "✗", color: BIOS_MISSING_RED, text: markOneOfTheseUnmet(word) })),
    { glyph: "✓", color: BIOS_OK_GREEN, text: MARK_REQUIRED_HERE },
    { glyph: "✓", color: BIOS_OK_GREEN, text: MARK_ONE_OF_THESE_HERE },
    { glyph: "?", color: BIOS_MUTED_GREY, text: MARK_MISSING_NEED_UNKNOWN },
    { glyph: "?", color: BIOS_MUTED_GREY, text: MARK_HERE_NEED_UNKNOWN },
    { glyph: "?", color: BIOS_MUTED_GREY, text: MARK_UNCHECKED },
    { glyph: "✓", color: BIOS_SPARE_GREEN, text: MARK_HERE },
    ...UNMET_WORDS.map((word) => ({ glyph: "✗", color: BIOS_MUTED_GREY, text: markUnmet(word) })),
  ].filter((entry) => marks.some((mark) => mark.title === entry.text));
  if (files.some((file) => biosLibraryMark(file) !== null)) {
    shown.push({ glyph: LIBRARY_MARK.glyph, color: LIBRARY_MARK.color, text: LIBRARY_MARK.title });
  }
  return shown;
}

/**
 * Every line rendered under a BIOS table row: its note (when neither the
 * library's second mark nor the Contents cell already says it), its image and
 * setting lines, and *placeLines*.
 */
export function biosTableRowLines(
  file: BiosTableRow,
  emulator: string | null,
  placeLines: readonly string[],
): string[] {
  const { note, lines, fromLibrary } = biosFileNote(file, emulator);
  const contents = contentsWords(file, emulator);
  const rowNote = note && note !== contents ? [note] : [];
  return [...(fromLibrary ? [] : [...rowNote, ...lines]), ...placeLines];
}
