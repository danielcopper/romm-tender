/**
 * What one BIOS file row says about itself — its note, which rides beside the
 * name, and its description, which goes on the line under it.
 *
 * Two surfaces render a firmware row (the game detail panel's BIOS tab and the
 * Library page's platform detail) and they used to word the same facts
 * separately, so a fact gained on one surface was a fact missing on the other.
 * This is the one place that decides; each surface still frames the result its
 * own way, because the BIOS tab leaves plain absence to its status dot while the
 * platform detail states it in the row's On-disk cell. What it hands over is a
 * sentence and a list of lines ({@link BiosFileWords}), plus the row's
 * description ({@link biosFileDescription}); where those go on the page is each
 * surface's own business, what they SAY is decided only here.
 *
 * **A row is headed by the file the emulator DECLARED on both surfaces, never
 * by its description** — `declared_path`, folder and all, which the platform
 * detail splits into a muted prefix and a name and the BIOS tab prints whole.
 * The description is the packager's prose out of a core's `.info`, deliberately
 * outside the resolver's contract, so it is not a field to render as a row's
 * identity — and it routinely spells the name into its own words besides, which
 * is what {@link biosFileDescription} takes back out.
 *
 * Precedence is deliberate. A file the distribution itself put there is not a
 * gap in the RomM library — it is not the library's file at all, and telling
 * the user it is missing from RomM is true and useless — so that note wins.
 * What the reading established about the row comes next, because a folder's
 * contents and a destination something else occupies are neither "missing" nor
 * "present". Only then does the library note stand, which is honest for every
 * row nothing else was established for.
 *
 * The CAUSE of a verdict comes from the resolver's own caveat codes, because
 * `satisfied` is deliberately the verdict alone and carries none of it. What the
 * verdict does decide is which family of codes can apply — with the declaration
 * kind, since a folder's findings and a destination's are different families —
 * and what to say when no code in that family is recognised, which is the one
 * sentence written off the verdict itself.
 */

import type { BiosFileStatus } from "../types";

/** The subset of a row this note is derived from — both surfaces' row shapes
 *  satisfy it, so neither has to be converted into the other's. */
export type BiosNoteRow = Pick<
  BiosFileStatus,
  "downloaded" | "on_server" | "supplied_by" | "satisfied" | "declared_kind" | "caveats" | "images" | "checked"
>;

/** The subset {@link biosFileDescription} reads — the same both-surfaces rule. */
export type BiosDescriptionRow = Pick<BiosFileStatus, "file_name" | "description" | "declared_kind" | "declaration">;

/**
 * Everything a row says about itself: one sentence, and the lines under it.
 *
 * Both come back together because they are alternatives as often as they are
 * companions — a folder that lists what it holds needs no sentence saying it
 * holds something — and a surface taking one without the other would render a
 * satisfied folder as a bare name. Both surfaces put them under the row they
 * belong to — the BIOS tab in an indented block, the platform detail alongside
 * the note, whose own cells are too narrow to hold either.
 */
export interface BiosFileWords {
  /** The em-dash note after the row's name, or `""` where there is none. */
  note: string;
  /** One line each, rendered under the row. Empty for every row but a folder
   *  whose read identified images, where the list IS the content. */
  lines: string[];
  /**
   * The note is the library one — "not in your RomM library" and its missing
   * variant — rather than anything the reading established.
   *
   * It is the one note that says the same thing on every row it applies to, and
   * on a platform whose library holds little it applies to nearly all of them.
   * A surface that carries that fact some other way — the platform detail marks
   * it in the row's own cell — drops the sentence and keeps the rest; the game
   * page's BIOS tab has room for it and prints it. The flag exists so neither
   * has to re-derive this function's precedence to know which note it got.
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
/** The folder could not be listed in full, or a candidate's bytes would not come
 *  back — a read failure, never a finding about what is in there. */
const READ_INCOMPLETE = ["firmware-scan-incomplete", "firmware-unreadable"];
/** The destination could not be looked at at all — permissions, or failing
 *  storage. Emitted for either declaration kind, and worded only for a file:
 *  a folder's own unreadable-destination row is withheld and says so. */
const PATH_INACCESSIBLE = "firmware-path-inaccessible";

/**
 * What the reading established about *row*, or `""` where it has nothing to add.
 *
 * A satisfied folder lists what it holds, in the resolver's own words — those
 * are the core's own option labels, and the core needs exactly one of them, so
 * they carry no per-image required/optional marking and no mark for which one
 * will be loaded (that is a core option this plugin does not read).
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
 * That asymmetry follows the verdicts rather than breaking from them. An
 * unreadable destination leaves a folder row withheld, where "its contents
 * could not be checked" already says the read is what failed; it leaves a file
 * row UNMET, where the surfaces would otherwise print their own word for
 * absence over a file nobody established the absence of. Both notes are
 * statements about the read, and neither claims the file is or is not there —
 * the verdict beside them says the requirement is not met, which is what
 * follows from a destination the emulator cannot open either.
 */
function verdictNote(row: BiosNoteRow): BiosFileWords {
  const has = (code: string) => (row.caveats ?? []).includes(code);
  if (row.declared_kind !== "directory") return fileAtItsDestination(has, row.checked);
  if (row.satisfied === true) return folderHolding(row.images ?? []);
  if (row.satisfied === false) return folderUnmet(has);
  return folderWithheld(row.satisfied, has);
}

/** One line and nothing under it — every group but a satisfied folder's. */
const said = (note: string): BiosFileWords => ({ note, lines: [], fromLibrary: false });

/**
 * A declared FILE: what the reading found at the place the emulator opens.
 *
 * The shape of the destination comes first — a folder standing where a file is
 * opened says nothing about bytes, because there were none to read. Then what
 * became of the bytes, which is `checked` and is three distinct things the row's
 * verdict cannot tell apart:
 *
 * - **`unrecognised`** — the emulator read the file and no table it keeps knows
 *   these bytes. It WAS checked, which is why it may not be worded as a read
 *   that failed; and it is no failure either — DuckStation boots such an image
 *   and says it is using an unknown BIOS — so the verdict stays withheld and the
 *   note says what was established rather than what was not.
 * - **`unread`** — the bytes were asked for and did not come back. This is the
 *   one the old single sentence was right about, and it is a statement about the
 *   PLUGIN's read rather than about the emulator's: that this process could not
 *   read the file is no evidence the launch cannot.
 * - **`refused`** — the emulator will not open the file at all, on its size,
 *   before reading a byte. It arrives with the verdict already `false`, so the
 *   row is red with or without this note; what the note adds is the reason, and
 *   without it the row says a file that is sitting right there is missing. **It
 *   is not reachable on an unmodified RetroDECK**: the resolver reaches that
 *   size gate only for a file one of DuckStation's per-region BIOS keys NAMES
 *   (`PathNTSCU` / `PathNTSCJ` / `PathPAL`), and RetroDECK sets `SearchDirectory`
 *   alone and leaves all three empty — cited, with the upstream line numbers, on
 *   the DuckStation card in `backend/_vendor/atlas/data/standalone_firmware.json`.
 *   A user who fills one of those keys in reaches it.
 *
 * `verified` and `mismatch` get no note: the first is the ordinary met row and
 * the second is an unmet one whose surfaces already say so. Every other value,
 * and the absent field, leave the note to the library half below.
 */
function fileAtItsDestination(has: (code: string) => boolean, checked: BiosNoteRow["checked"]): BiosFileWords {
  if (has(PATH_OBSTRUCTED)) return said("a folder is here, where the emulator opens a file");
  if (has(PATH_INACCESSIBLE)) return said("its location could not be read");
  if (checked === "unrecognised") return said("the emulator does not recognise this file");
  if (checked === "unread") return said("its bytes could not be read");
  if (checked === "refused") return said("the emulator refuses a file of this size");
  return said("");
}

/**
 * A folder whose read found an image — the one group whose content is a list.
 *
 * The images ARE the sentence here. "holds" above a list of three would be a
 * heading for a list that needs none, and folding them into the row's own name
 * is what pushed the status dot onto a line of its own.
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
 * Both surfaces head the row with the declared file, so the rule for what the
 * description still adds has to be ONE rule — otherwise the same row prints the
 * name twice on one surface and once on the other. Where the result GOES is each
 * surface's own: the game page's BIOS tab sets it beside the name, the platform
 * detail on a muted line under the row, because that name sits in a clipping
 * table cell narrow enough that a label beside it would cut the name itself.
 *
 * **It is not RomM's description** — `_server_files` builds no `description`
 * key at all, and `_wanted_fields` overwrites whatever came in.
 * What arrives is the core's own `firmwareN_desc` out of its `.info` file, or,
 * for a row no placement covers, the file name itself (`build_file_entry`'s
 * `else file_name`). Both spell the name into the words.
 *
 * **Only a `read` declaration's prose is shown at all**, which is the first
 * thing decided here. That prose is a packager's LABEL for the file and says
 * what the row's own name does not — `(PS1 JP BIOS)` on `scph5500.bin`, a
 * region the name never states. A `packaged` row's is a different
 * kind of writing under the same field: atlas explaining the requirement in
 * whole sentences ("a PlayStation BIOS image — the console runs it before any
 * disc, and DuckStation starts nothing without one — found by the search, not
 * named by any setting"), which is an essay on a line sized for a label. Neither
 * surface has room for it: on the platform detail it broke off mid-sentence, on
 * the game page it filled the row. So the register is read off the DECLARATION
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

/**
 * What *row* says about itself — an empty note where its own state is the whole story.
 *
 * The note is `""` for a plain library file, present or missing alike: what the
 * surfaces disagree about is how to say "missing", so that word is left to them
 * (the platform detail marks it in its On-disk cell; the tab's dot carries it).
 */
export function biosFileNote(row: BiosNoteRow): BiosFileWords {
  if (row.supplied_by) return { note: `provided by ${row.supplied_by}`, lines: [], fromLibrary: false };
  const verdict = verdictNote(row);
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
