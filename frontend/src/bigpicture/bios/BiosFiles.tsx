/**
 * The BIOS files of one platform as a table, and the buttons that act on them —
 * the part a platform's page and a game's BIOS tab both draw: the emulator
 * selector, the shown emulator's files with a button on each row that has one,
 * "Download required for <emulator>", "Download all" and "Delete downloads"
 * under "For all of <platform>", the line those presses leave, the library's
 * files no emulator here asks for, and the legend for the marks.
 *
 * Each page lays the pieces out itself and draws its own heading and sentence
 * above them; {@link useBiosFiles} is what the pieces are drawn from.
 *
 * Every row a reader must reach is a focus stop — a row with nothing to press
 * included — because the page scrolls by moving focus.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Library.
 */

import { useState, type FC, type ReactNode } from "react";
import { ConfirmModal, DialogButton, showModal } from "@decky/ui";
import type { BiosFileStatus, EmulatorTab, FirmwarePlatformExt } from "../../types";
import { downloadName, fetchedAsRequired, isFetchable } from "../../utils/biosFetchable";
import { biosFileDescription, biosFileNote, contentsWords } from "../../utils/biosFileNote";
import { needWords, unmetWord } from "../../utils/biosGroup";
import { DOWNLOAD_DAMAGED, IDENTIFYING, biosPlaceLines, libraryRowLines } from "../../utils/biosPlaceWords";
import { isUnclaimedLibraryRow, selectedTab, tabLabel, tabRows } from "../../utils/biosTabs";
import { useIdentifications } from "../../utils/firmwareIdentifications";
import { Throbber, ThrobberLabel } from "../../shared/Throbber";
import { EmulatorSelector } from "../EmulatorSelector";
import {
  ButtonRow,
  FLAT_BUTTON,
  GREEN,
  GroupStatus,
  MUTED,
  NOTE_FONT,
  PALE_GREEN,
  PANE_GUTTER,
  PANE_TABLE_REGISTER,
  PaneTableHeader,
  PaneTableRow,
  RED,
  ROW_BUTTON,
  SECONDARY_FONT,
  SubHeading,
  VIOLET,
  type ScopedStatus,
  type TableRegister,
} from "../layout/pane";
import { useBiosFocus, type BiosFocus } from "./useBiosFocus";
import type { BiosActions } from "./useBiosActions";

/**
 * The second mark: your RomM library does not hold this file.
 *
 * It sits BESIDE the verdict mark and never in place of it — the two are
 * different questions, and a row that is present but unfetchable ("you have it,
 * but you could not fetch it again") is exactly the combination a single
 * channel would lose. Which is why it also carries no need axis of its own: the
 * mark it stands next to already says whether the file is wanted.
 *
 * `⊘` is not one of the verdict shapes and reads as "not available" rather than
 * as a degree of wrong; DejaVu Sans carries U+2298, the same fontconfig
 * fallback the `✓`/`✗` glyphs already rely on for this table.
 */
const LIBRARY_MARK = { glyph: "⊘", color: VIOLET, title: "not in your RomM library" } as const;

/**
 * What each mark means — the row's own tooltip AND the legend's line for it, one
 * string serving both.
 *
 * They are the mark's IDENTITY, which is what the legend filters and keys on.
 * Glyph plus colour cannot be: since a one-of group's options became a state of
 * their own, red `✗` is two different sentences (a required file that is absent,
 * an option of a group whose region it serves has nothing in place) and so is
 * green `✓`. Keyed on the pair, the legend would have shown one of each and given
 * the second one React's duplicate key; keyed on the sentence, it shows exactly
 * the ones the table is in.
 */
// A `✗` says what the file IS where its verdict is not met — missing, the wrong
// file, or refused (`unmetWord`, off the row's `checked`) — so the mark never
// contradicts the Contents words beside it.
const markRequiredUnmet = (word: string) => `required, ${word}`;
const MARK_REQUIRED_HERE = "required, here";
const MARK_HERE = "here, not required";
const markUnmet = (word: string) => `${word}, not required`;
// The `?` glyph's word, and it has to be true of every way a verdict is
// withheld — which since the `checked` vocabulary arrived includes a file the
// emulator READ and does not recognise. "could not be checked" was untrue of
// exactly that one, and it is the reachable case: DuckStation reads such an
// image, boots it, and calls it an unknown BIOS. So the mark says what the
// verdict is — nothing settled, in either direction — and WHY is the row's own
// note beside the name (`biosFileNote`), which is the one place a cause is
// worded.
const MARK_UNCHECKED = "nothing could establish this either way";
const MARK_HERE_NEED_UNKNOWN = "here; nothing could say whether this is wanted";
const MARK_MISSING_NEED_UNKNOWN = "missing; nothing could say whether this is wanted";
const MARK_ONE_OF_THESE_HERE = "one of these, here";
const markOneOfTheseUnmet = (word: string) => `one of these, ${word}`;
const UNMET_WORDS = [unmetWord(null), unmetWord("mismatch"), unmetWord("refused")];

/** One row of the firmware overview's per-platform file list. Named off the
 *  payload rather than restated, so a field added to it reaches here. */
type FirmwareRow = FirmwarePlatformExt["files"][number];

/** A row the BIOS table renders: a platform answer's own `files`, a game's, or
 *  another emulator's rows, which carry no server record of their own. */
export type BiosTableRow = FirmwareRow | BiosFileStatus;

/**
 * The first mark in one row's `On disk` cell, as a glyph and a colour.
 *
 * Two facts, two channels, so neither has to be read off the other:
 *
 * - **the glyph is the VERDICT** — `✓` met, `✗` not met, `?` nothing could
 *   establish it. It is `BiosFileEntry.satisfied`, **never presence**: for a
 *   declared folder the two come apart completely, since RetroDECK links
 *   LRPS2's `pcsx2/bios` onto the BIOS root, so the folder is always there and
 *   what satisfies the core is a file inside it.
 * - **the colour is the NEED** — strong where the core the platform launches
 *   with requires the file, muted where it does not. Keyed on
 *   `required_by_active`, not `wanted`, because the summary above the table
 *   counts the same way and the two must not disagree about what "required"
 *   means.
 *
 * **Every "not established" is a grey `?`** — a verdict nothing
 * could establish, and a need nothing could establish: a file whose need
 * nobody answered is no file "not required", so it does not wear that grey `✗`.
 * Whether such a file is there is still the row's own verdict, and the mark's
 * title and the legend line keep saying it — a platform nothing could be asked
 * about is made entirely of such rows, and which ones are already there is the
 * one thing the pane must not stop saying.
 *
 * So the states come out as required + met green `✓`, required + unmet red
 * `✗`, not required + met pale green `✓`, not required + unmet grey `✗`, and
 * grey `?` wherever the verdict or the need is not established. Amber is never a
 * mark here: it means only a one-of group whose images serve some regions.
 *
 * `not_needed` and `optional` share the muted branch on purpose: for the core
 * about to launch, a file it does not require is not a gap either way.
 *
 * **An option of the launching emulator's one-of group (`one_of`) is the one need
 * the two channels above cannot carry**, and it is the fifth state rather than a
 * shade of the fourth: the emulator marks no such file required, because the
 * group is the requirement and the file one way to meet it, yet a missing one
 * leaves the regions it serves with nothing to boot. So it is strong like a
 * required row, under its own words, and it is asked after the two `?` marks
 * and before the required/not-required pair. Which region it serves is the need
 * line under the row's name ({@link needWords}).
 *
 * The `Contents` cell words the resolver's check behind the same verdict
 * (`contentsWords`), so the two columns explain each other and cannot
 * contradict each other.
 */
function diskMark(file: BiosTableRow): { glyph: string; color: string; title: string } {
  const verdict = rowVerdict(file);
  if (verdict === null) return { glyph: "?", color: MUTED, title: MARK_UNCHECKED };
  if (file.wanted === "unknown") {
    return { glyph: "?", color: MUTED, title: verdict ? MARK_HERE_NEED_UNKNOWN : MARK_MISSING_NEED_UNKNOWN };
  }

  const required = file.required_by_active;
  const option = Boolean(file.one_of) && !required;
  if (verdict) {
    if (option) return { glyph: "✓", color: GREEN, title: MARK_ONE_OF_THESE_HERE };
    return { glyph: "✓", color: required ? GREEN : PALE_GREEN, title: required ? MARK_REQUIRED_HERE : MARK_HERE };
  }
  const word = unmetWord(file.checked);
  if (option) return { glyph: "✗", color: RED, title: markOneOfTheseUnmet(word) };
  return { glyph: "✗", color: required ? RED : MUTED, title: required ? markRequiredUnmet(word) : markUnmet(word) };
}

/**
 * The row's verdict, as the payload states it or as its silence leaves it.
 *
 * A folder's verdict is what it HOLDS, never that the folder is there — the
 * register's rule — so a payload carrying no verdict for one leaves the row
 * unestablished rather than falling back to presence. A payload from before
 * `satisfied` existed falls back to `downloaded` for a declared file.
 */
function rowVerdict(file: BiosTableRow): boolean | null {
  const declaredFolder = file.declared_kind === "directory";
  const fallback = declaredFolder ? null : file.downloaded;
  return file.satisfied !== undefined ? file.satisfied : fallback;
}

/**
 * The row's second mark, or `null` where the library holds the file.
 *
 * One field, one rule, no conditionality on the verdict: a file downloaded
 * before it left the library is still one that cannot be fetched again, so the
 * mark appears beside a `✓` exactly as it does beside a `✗`.
 *
 * `on_server` absent is not `false` — it is a payload that never spoke about
 * the library — so an unstated row carries no mark rather than a claim.
 *
 * A declared FOLDER is out, and not as a special case: no RomM library holds a
 * folder, so `_overview_row` stamps every folder row `on_server: False`
 * unconditionally and the mark would say "your library does not hold this"
 * about something no library can. That is the sentence `biosFileNote` already
 * refuses to produce for the same reason, and the same reason keeps folder rows
 * out of the download filter and out of the download batch.
 */
function libraryMark(file: BiosTableRow): typeof LIBRARY_MARK | null {
  return file.on_server === false && file.declared_kind !== "directory" ? LIBRARY_MARK : null;
}

// File, On disk, Contents, and the row's own button. Every column but the first
// is sized for the widest thing it can hold, measured on the device at the
// Deck's scale: the `On disk` heading is 38.7px and two marks are ~34px; the
// widest image count ("12 images") is 76.8px against a 46.1px heading, and the
// resolver's longer words for a file's check wrap inside the column rather than
// being cut; the Download button's label is ~55px.
// What that leaves goes to the file name, which is the column with something to
// say. None of them is conditional — a column that appears and disappears per
// platform is worse than a narrow one.
const TABLE_COLUMNS = "1fr 48px 84px 92px";

// A file's row and the column names over the rows are set at the 12px of the
// section heading, two pixels above the NOTE_FONT lines under each name: on the
// Deck one pixel apart is no difference at all, and column names set like the
// notes read as notes rather than as a table head.
const BIOS_TABLE_FONT = "12px";
const BIOS_TABLE_REGISTER: TableRegister = {
  ...PANE_TABLE_REGISTER,
  // Half the pane's row padding: each row already carries its lines under the name.
  rowPadding: `2px ${PANE_GUTTER}`,
  rowFont: BIOS_TABLE_FONT,
  rowLineHeight: 1.25,
  headerFont: BIOS_TABLE_FONT,
};

const BiosTableHeader: FC = () => (
  <PaneTableHeader
    columns={TABLE_COLUMNS}
    cells={["File", "On disk", "Contents", ""]}
    register={BIOS_TABLE_REGISTER}
    testId="bios-table-header"
  />
);

/**
 * What a row says under itself: its note, and the images a folder holds.
 *
 * Full width, because the alternative is a 48px cell wrapping one sentence
 * across three lines — the cost the marks were introduced to stop paying. A
 * note here is also rare in the ordinary case: over the `.info` corpus the rows
 * that carry one are the handful RetroDECK supplies itself and PS2's folder
 * row. That is a statement about a healthy install, not about the vocabulary —
 * `biosFileNote`'s caveat wording appears wherever a destination cannot be
 * read, which no corpus can predict.
 *
 * Each image string is the resolver's verbatim, and `pre-wrap` keeps the column
 * padding PCSX2 puts in its own option labels — that alignment is what makes a
 * line matchable against the emulator's own picker. They sit under the row
 * rather than in the Contents cell because the cell is 84px and one of these
 * labels is not; the cell counts them instead.
 */
const BiosRowLines: FC<{ lines: string[]; busyLine: string | null }> = ({ lines, busyLine }) =>
  lines.length === 0 ? null : (
    <div style={{ display: "flex", flexDirection: "column", marginLeft: "18px" }}>
      {lines.map((line) => (
        <div key={line} style={{ fontSize: NOTE_FONT, lineHeight: 1.2, color: MUTED, whiteSpace: "pre-wrap" }}>
          <ThrobberLabel busy={line === busyLine}>{line}</ThrobberLabel>
        </div>
      ))}
    </div>
  );

/**
 * The folder the emulator declared this file in, with its trailing slash, or
 * `null` for a file that belongs at the root of the BIOS directory.
 *
 * The row's own name is a basename, so without this the pane cannot answer the
 * one question a user placing a file by hand has: which folder. 207 of the 695
 * declarations a stock RetroDECK ships name a subdirectory — `dc/dc_boot.bin`,
 * `ep128emu/roms/exos21.rom` — and their descriptions spell it in only 115 of
 * those, so the description is not a substitute for the declaration.
 */
function declaredFolder(file: BiosTableRow): string | null {
  const declared = file.declared_path;
  if (!declared?.includes("/")) return null;
  return `${declared.slice(0, declared.lastIndexOf("/"))}/`;
}

/**
 * What the marks in `On disk` mean, under the tables that use them.
 *
 * On the platform pane one entry per line, which is what makes a two-mark cell
 * readable and is what keeps the filter doing real work; on a game's BIOS tab
 * (`inline`) the entries stand side by side under the left column's buttons,
 * wrapping where the column runs out. Either way it shows only the entries the
 * tables actually contain, because a line for a state no row is in explains
 * nothing and costs a row, the scarce thing on this pane. Mark 2 is inside
 * that filter as well — a platform whose library holds every file shows no line
 * for it — and gets one line rather than one per pairing, since it means the
 * same beside every verdict. The order is the order a reader cares about: what is wrong first,
 * then the second channel — and each of a group option's two states sits beside
 * the ordinary mark it shares a colour with, since a reader meeting two red `✗`
 * lines is being told what separates them.
 *
 * The entries carry the same strings the rows' tooltips do, and match on them:
 * see the `MARK_*` block for why the glyph and colour cannot be the identity.
 */
const BiosLegend: FC<{ files: readonly BiosTableRow[]; inline?: boolean }> = ({ files, inline = false }) => {
  const marks = files.map((file) => diskMark(file));
  const shown = [
    ...UNMET_WORDS.map((word) => ({ glyph: "✗", color: RED, text: markRequiredUnmet(word) })),
    ...UNMET_WORDS.map((word) => ({ glyph: "✗", color: RED, text: markOneOfTheseUnmet(word) })),
    { glyph: "✓", color: GREEN, text: MARK_REQUIRED_HERE },
    { glyph: "✓", color: GREEN, text: MARK_ONE_OF_THESE_HERE },
    { glyph: "?", color: MUTED, text: MARK_MISSING_NEED_UNKNOWN },
    { glyph: "?", color: MUTED, text: MARK_HERE_NEED_UNKNOWN },
    { glyph: "?", color: MUTED, text: MARK_UNCHECKED },
    { glyph: "✓", color: PALE_GREEN, text: MARK_HERE },
    ...UNMET_WORDS.map((word) => ({ glyph: "✗", color: MUTED, text: markUnmet(word) })),
  ].filter((entry) => marks.some((mark) => mark.title === entry.text));
  if (files.some((file) => libraryMark(file) !== null)) {
    shown.push({ glyph: LIBRARY_MARK.glyph, color: LIBRARY_MARK.color, text: LIBRARY_MARK.title });
  }
  if (shown.length === 0) return null;
  return (
    <div
      data-testid="bios-legend"
      style={{
        display: "flex",
        flexDirection: inline ? "row" : "column",
        flexWrap: "wrap",
        columnGap: "24px",
        padding: "2px 16px 6px",
        fontSize: SECONDARY_FONT,
        lineHeight: 1.3,
      }}
    >
      {shown.map((entry) => (
        <span key={entry.text} style={{ color: MUTED }}>
          <span style={{ color: entry.color }}>{entry.glyph}</span> {entry.text}
        </span>
      ))}
    </div>
  );
};

/** A muted line under a row's name, clipped to one line and set close under it. */
const UNDER_NAME_LINE = {
  marginLeft: "18px",
  fontSize: NOTE_FONT,
  lineHeight: 1.2,
  color: MUTED,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
} as const;
/** The file's name, the first of the File cell's two lines. */
const NAME_LINE = { display: "block", overflow: "hidden", textOverflow: "ellipsis" } as const;
/** The need under the name, inside the cell the name clips in. */
const NEED_LINE = { ...UNDER_NAME_LINE, display: "block" } as const;
/** The Contents cell: muted words that wrap at the column's edge. */
const CONTENTS_CELL = {
  minWidth: 0,
  color: MUTED,
  fontSize: SECONDARY_FONT,
  whiteSpace: "normal",
  overflowWrap: "break-word",
} as const;

/**
 * How a row with nothing to press is made a focus stop.
 *
 * `focusable` is the table row's own `Focusable` stop (`layout/pane.tsx`).
 * `button` wraps the row in a `DialogButton` that does nothing when pressed,
 * for a page injected into Steam's game page, where a `Focusable` around
 * content that is not a control registers with no gamepad stop
 * (`utils/scrollHelpers.ts`).
 */
export type BiosRowStop = "focusable" | "button";

/**
 * The wrapper a `button` stop draws: no button chrome, the row's own layout,
 * and the row's own text colour, which the row's name takes from it. How it
 * looks focused: the `.romm-bios-row-stop.gpfocus` rule in
 * `utils/styleInjector.ts`.
 */
const ROW_STOP_BUTTON = {
  background: "transparent",
  color: "inherit",
  border: "none",
  borderRadius: 0,
  padding: 0,
  margin: 0,
  minWidth: 0,
  width: "100%",
  textAlign: "left",
  cursor: "default",
  display: "block",
} as const;

const BiosFileRow: FC<{
  file: BiosTableRow;
  action: ReactNode;
  emulator: string | null;
  placeLines: string[];
  /** The one of *placeLines* that says work for this row is under way, if any. */
  busyLine?: string | null;
  stop: BiosRowStop;
}> = ({ file, action, emulator, placeLines, busyLine = null, stop }) => {
  const { note, lines, fromLibrary } = biosFileNote(file, emulator);
  // What the resolver's check found at the row's place, in words —
  // `utils/biosFileNote.ts` holds them beside the row's other words.
  const contents = contentsWords(file, emulator);
  const mark = diskMark(file);
  const library = libraryMark(file);
  const description = biosFileDescription(file);
  const folder = declaredFolder(file);
  // The library note is the one sentence the cell's second mark now carries, and
  // on a platform whose library holds little it was the same words under nearly
  // every row. Everything else moves under the row rather than into the cell.
  // A note the Contents cell already says — a wrong image — is not said twice.
  const rowNote = note && note !== contents ? [note] : [];
  const rowLines = [...(fromLibrary ? [] : [...rowNote, ...lines]), ...placeLines];
  const row = (
    // The row is a focus stop only while it has nothing to press: an action cell
    // holds a button that is already a stop, and a second one on the wrapper
    // would put a dead step in front of every one of them. `action` is therefore
    // a NODE that may be null and never a component element — an element is
    // always truthy, which is how this branch went dead once while the comment
    // on it went on explaining it.
    <PaneTableRow
      columns={TABLE_COLUMNS}
      register={BIOS_TABLE_REGISTER}
      testId="bios-file-row"
      focusStop={!action && stop === "focusable"}
      cells={[
        {
          // With the folder in front of it what gets cut is the NAME rather
          // than the description that used to sit here —
          // `scummvm/extra/hadesch_translations.dat` does not fit the platform
          // pane's ~202px name cell in any arrangement. The title is the mouse's way back to it; a reader on
          // the controller has none, and the only real fix is width the list
          // column currently holds.
          //
          // What the shown emulator needs of the file stands under the name in
          // the same cell: the mark's colour alone does not tell a
          // met required file from a met optional one, and a two-line cell is
          // what the row's button is centred against.
          content: (
            <>
              <span data-testid="bios-file-name" style={NAME_LINE}>
                {folder && <span style={{ color: MUTED }}>{folder}</span>}
                {file.file_name}
              </span>
              <span data-testid="bios-need" style={NEED_LINE}>
                {needWords(file)}
              </span>
            </>
          ),
          title: file.declared_path ?? file.file_name,
        },
        {
          // Glyphs rather than a run of text: nothing to ellipsise, and the
          // pair is laid out rather than flowed.
          content: (
            <>
              <span data-testid="disk-mark" style={{ color: mark.color }} title={mark.title}>
                {mark.glyph}
              </span>
              {library && (
                <span data-testid="library-mark" style={{ color: library.color }} title={library.title}>
                  {library.glyph}
                </span>
              )}
            </>
          ),
          style: { display: "flex", gap: "4px", fontSize: "12px", whiteSpace: "nowrap" },
          clip: false,
        },
        // Wrapped, never cut: these words are the explanation of the mark
        // beside them, and a controller has no way to the cell's title.
        { content: contents, clip: false, style: CONTENTS_CELL },
        // The button draws its focus ring outside its own box (the injected
        // sheet's outline), so this cell must not hide its overflow.
        { content: action, clip: false },
      ]}
    >
      {/* The description is a muted line UNDER the row rather than beside the
          name: this name lives in a table cell that clips (~202px on the
          platform pane), and it
          already gives up its folder prefix to the same shortage — hanging the
          label off it would take the NAME off the screen, which is the one
          thing a reader placing a file by hand needs. Under the row there is a
          whole width for it, on one clipped line. */}
      {description && <div style={UNDER_NAME_LINE}>{description}</div>}
      <BiosRowLines lines={rowLines} busyLine={busyLine} />
    </PaneTableRow>
  );
  if (action || stop === "focusable") return row;
  return (
    <DialogButton className="romm-bios-row-stop" style={ROW_STOP_BUTTON} noFocusRing={false}>
      {row}
    </DialogButton>
  );
};

/**
 * What a download button shows instead of its label: a spinner while its own run
 * is going, a red `Failed` for the moment after one fails, or nothing.
 *
 * Both are keyed on the run's slug as well as the button's identity, because
 * `downloadPending` and `downloadFailed` name the button and every pane reads
 * the same state — without the slug a spinner would appear on the pane the
 * reader walked to. That pane shows disabled buttons and the line naming the
 * platform that is busy, which is what it always did.
 */
function downloadState(part: BiosPart, id: string): "spinner" | "failed" | null {
  const { busySlug, slug, actions } = part.input;
  if (busySlug !== slug) return null;
  if (actions.downloadPending === id) return "spinner";
  return actions.downloadFailed === id ? "failed" : null;
}

/**
 * *text* in red while its button can be pressed. A disabled button shows only
 * Steam's own `Disabled` look, so no colour of ours is laid over it.
 */
function redWhileEnabled(text: string, disabled: boolean): ReactNode {
  return disabled ? text : <span style={{ color: RED }}>{text}</span>;
}

/** The label a download button carries right now — its own word unless its run
 *  is speaking; *disabled* is that button's own state. */
function downloadLabel(part: BiosPart, id: string, label: string, size: number, disabled: boolean): ReactNode {
  const showing = downloadState(part, id);
  if (showing === "spinner") return <Throbber size={`${size}px`} />;
  return showing === "failed" ? redWhileEnabled("Failed", disabled) : label;
}

/**
 * A BIOS row's one action: fetch it, replace what lies at its place, or remove
 * the copy Tender holds — or nothing, which is a `null` the row needs in order
 * to make itself a focus stop.
 *
 * The buttons are alternatives in every state the pane can show, so one narrow
 * column carries them. Download wins where a row is both fetchable and
 * deletable — the file the emulator will look for is the one that is missing.
 * "Replace…" is the backend's decision alone (`replace_offered`), and its
 * confirmation names the exact path and where the old file goes.
 *
 * **The delete's condition is `delete_name` and nothing else.** It names
 * Tender's own store file, an older download of the library file that fills
 * the row, or an older download lying at the row's own place, which is the whole
 * authority; `downloaded` is the resolver's
 * presence at the row's place and is equally true of a file the user placed.
 * The removal itself re-reads the records and takes only Tender's own files
 * and links, so this button cannot widen what it removes.
 */
function rowAction(part: BiosPart, file: BiosTableRow, fetchable: Set<string>, key: string): ReactNode | null {
  const { slug, busySlug, serverOffline, actions } = part.input;
  const { focus } = part;
  const busy = busySlug !== null;
  // The pressed row's own button stays enabled while its work runs, and every
  // other one waits greyed — still a focus stop, since Steam's
  // `DialogButton` never disables its element, as its shipped client code
  // shows (`steamui/sp.js`). A press on this one while anything runs does
  // nothing.
  const disabled = busy && focus.pressed !== key;
  if (fetchable.has(file.file_name) && !serverOffline) {
    const name = downloadName(file);
    return (
      <DialogButton
        style={ROW_BUTTON}
        disabled={disabled}
        onClick={() => {
          if (busy) return;
          focus.mark(key);
          actions.downloadOne(slug, name, file.library?.firmware_id ?? null);
        }}
      >
        {downloadLabel(part, name, "Download", 11, disabled)}
      </DialogButton>
    );
  }
  if (file.replace_offered && file.local_path) {
    const place = file.local_path;
    const confirmReplace = () => {
      if (busy) return;
      focus.hold(key);
      showModal(
        <ConfirmModal
          strTitle={`Replace ${file.file_name}?`}
          strDescription={`The file at ${place} goes to ${part.input.answer.backup_root ?? "the backup folder"}, and Tender's copy is linked in its place.`}
          strOKButtonText="Replace"
          strCancelButtonText="Cancel"
          onOK={() => {
            focus.mark(key);
            actions.replaceBios(slug, place);
            focus.refocus(key);
          }}
          onCancel={() => focus.refocus(key)}
        />,
      );
    };
    return (
      <DialogButton style={ROW_BUTTON} disabled={disabled} onClick={confirmReplace}>
        Replace…
      </DialogButton>
    );
  }
  // A row from a payload that states no `delete_name` is deleted by its own name.
  const name = file.delete_name === undefined ? file.file_name : file.delete_name;
  if (name === null || (file.deletable_count ?? 0) === 0) return null;
  const confirm = () => {
    if (busy) return;
    focus.hold(key);
    showModal(
      <ConfirmModal
        strTitle={`Delete ${name}?`}
        strDescription="Tender removes it from its firmware store and every link to it. Files you placed yourself stay."
        strOKButtonText="Delete"
        strCancelButtonText="Cancel"
        onOK={() => {
          focus.mark(key);
          actions.deleteBiosFile(slug, name);
          focus.refocus(key);
        }}
        onCancel={() => focus.refocus(key)}
      />,
    );
  };
  return (
    <DialogButton style={ROW_BUTTON} disabled={disabled} onClick={confirm}>
      {redWhileEnabled("Delete", disabled)}
    </DialogButton>
  );
}

// What `useBiosFocus` knows each focus target of the BIOS part by.
const tableKey = (file: string) => `table:${file}`;
const libraryKey = (file: string) => `library:${file}`;
const BULK_REQUIRED = "bulk:required";
const BULK_ALL = "bulk:all";
const BULK_DELETE = "bulk:delete";

/** A bulk button: as wide as its label, never stretched across the row it stands in. Steam's `.DialogButton` sets
 *  `width: 100%`, which the flex item would otherwise keep. */
const BULK_BUTTON = { ...FLAT_BUTTON, flex: "0 1 auto", width: "auto" } as const;

/** The fields of a platform's or a game's BIOS answer the part is drawn from. */
export interface BiosAnswerFields {
  files?: readonly BiosTableRow[];
  emulator_tabs?: EmulatorTab[];
  server_count?: number;
  local_count?: number;
  deletable_count?: number;
  backup_root?: string;
  active_core_label?: string | null;
}

export interface BiosPartInput {
  /** The platform the files are for, and what its page calls it. */
  slug: string;
  platformName: string;
  answer: BiosAnswerFields;
  /** The page's busy key: every press waits while any action of the page runs. */
  busySlug: string | null;
  /** RomM is unreachable: the downloads are withdrawn, the deletes stand. */
  serverOffline: boolean;
  actions: BiosActions;
  /** What "Download required" asks for on the launching emulator's view: the
   *  platform's own pick (`"pick"`, where the page is the platform's) or the
   *  shown entry's emulator (`"shown"`, where a game may launch with another). */
  launching: "pick" | "shown";
  stop: BiosRowStop;
  /** The name a page knows a platform by, for a link into another platform's store copy. */
  platformNameOf?: (slug: string) => string;
}

export interface BiosPart {
  input: BiosPartInput;
  focus: BiosFocus;
  tabs: EmulatorTab[] | undefined;
  tab: EmulatorTab | null;
  select: (chosen: EmulatorTab) => void;
  shownRows: readonly BiosTableRow[];
  libraryRows: readonly BiosTableRow[];
  identifications: ReturnType<typeof useIdentifications>;
}

/**
 * The state the pieces are drawn from: which emulator the table shows, its
 * rows, the library's own rows and focus. The shown emulator is the launching
 * one until another is chosen — a view, never a change of what launches; the
 * table, its per-row actions and "Download required" follow it, while what is
 * said above the table stays the launching emulator's. The library's files no
 * emulator here declares are no emulator's rows, and stand in a section of
 * their own whichever emulator is shown.
 */
export function useBiosFiles(input: BiosPartInput): BiosPart {
  const [tabKey, setTabKey] = useState<string | null>(null);
  const files = input.answer.files ?? [];
  const tabs = input.answer.emulator_tabs;
  const tab = selectedTab(tabs, tabKey);
  const shownRows = tabRows(files, tabs, tab);
  const focus = useBiosFocus(
    input.busySlug !== null,
    shownRows.map((file) => tableKey(file.file_name)),
  );
  const libraryRows = files.filter(isUnclaimedLibraryRow);
  // What the resolver knows each library file's content as, asked after the
  // page is shown, one file at a time.
  const identifications = useIdentifications(
    libraryRows.flatMap((file) => (file.library?.md5 ? [file.library.md5] : [])),
  );
  return {
    input,
    focus,
    tabs,
    tab,
    select: (chosen) => focus.step(() => setTabKey(chosen.launching ? null : chosen.emulator)),
    shownRows,
    libraryRows,
    identifications,
  };
}

/** The selector over every emulator the answer holds an entry for. */
export const BiosEmulatorSelector: FC<{ part: BiosPart; padding?: string }> = ({ part, padding = "0" }) => (
  <EmulatorSelector tabs={part.tabs} shown={part.tab} onSelect={part.select} padding={padding} />
);

/** The shown emulator's files, one row each, with the row's button where it has one. */
export const BiosShownTable: FC<{ part: BiosPart }> = ({ part }) => {
  const { input, tab, shownRows, focus } = part;
  const shownLabel = tab ? tabLabel(tab) : null;
  const emulator = shownLabel ?? input.answer.active_core_label ?? null;
  const fetchable = new Set(shownRows.filter(isFetchable).map((f) => f.file_name));
  if (shownRows.length === 0) return null;
  return (
    <>
      <BiosTableHeader />
      {shownRows.map((file) => (
        <div key={file.file_name} ref={focus.slot(tableKey(file.file_name))}>
          <BiosFileRow
            file={file}
            // Every emulator's rows offer their download and their delete; a
            // download lands in the store and is linked for every emulator its
            // source does not state is not installed.
            action={rowAction(part, file, fetchable, tableKey(file.file_name))}
            emulator={emulator}
            placeLines={biosPlaceLines(
              file,
              emulator,
              input.actions.attemptFor(input.slug, file.local_path, downloadName(file)),
              input.platformNameOf,
            )}
            stop={input.stop}
          />
        </div>
      ))}
    </>
  );
};

/**
 * "Download required for <emulator>", then under "For all of <platform>"
 * "Download all" and the delete, and the line their presses leave.
 *
 * The download affordances key off what is missing AND fetchable, and read
 * the VERDICT nowhere — not `bios_level`, not `required_withheld`. They are
 * two independent questions: what the RESOLVER could establish is the
 * emulator's demand, what is FETCHABLE is what the RomM library holds, and
 * neither answers the other. A required file the library
 * does not hold leaves the platform not ready and still gives the user
 * nothing to press here; a platform nothing could be read for still has a
 * library behind it, and fetching from it is the one action that moves the
 * platform along at all.
 *
 * `Download required` counts the SHOWN emulator's own rows the backend marks
 * `fetch_for_required` — its required files, and the options of the regions
 * its one-of group does not cover yet, the one rule the download itself
 * applies — and `Download all` stops at the library's own finished ratio. So
 * neither is a readiness gate either.
 *
 * Reading readiness here is what took the buttons off PS2, GameCube and PSP
 * the moment a BIOS answer was scoped to the emulator that actually launches:
 * those launch standalone emulators the resolver holds no card for, so the
 * verdict declines — which says nothing whatever about the files their
 * library offers.
 *
 * Which rows are fetchable at all — the library's side of it, including why a
 * declared folder is out — is `isFetchable`.
 *
 * Every button is always rendered and disables when there is nothing to do: a
 * button that vanishes is a state the reader has to work out, and on PS2 all of
 * them vanished at once. A disabled `DialogButton` is still a focus stop, so the
 * row stays walkable. The one exception is "Download required" where no
 * emulator has an entry: there is no requirement it could fetch, rather than
 * nothing left to do.
 */
export const BiosButtons: FC<{ part: BiosPart; status: ScopedStatus | null }> = ({ part, status }) => {
  const { input, tab, shownRows, focus } = part;
  const { slug, platformName, answer, busySlug, serverOffline, actions } = input;
  const files = answer.files ?? [];
  const busy = busySlug !== null;
  const otherTab = tab !== null && !tab.launching;
  // The emulator a download from this table is for: the shown one's identity on
  // another emulator's view; on the launching one's, the platform's own pick
  // (`null`) where the page is the platform's — which also covers a launching
  // emulator the answer holds no entry for — and the entry's own emulator where
  // the page is a game's.
  const shownEmulator = otherTab || input.launching === "shown" ? (tab?.emulator ?? null) : null;
  const shownLabel = tab ? tabLabel(tab) : null;
  const requiredLabel = shownLabel === null ? null : `Download required for ${shownLabel}`;
  // The library's own progress, read nowhere but `allDone`, which is one of
  // `showAll`'s conditions. Counted over what the LIBRARY holds, not over the
  // rows — the rows include files no library holds, and offering to fetch those
  // is work the user cannot do.
  const total = answer.server_count ?? files.filter((f) => f.on_server).length;
  const done = answer.local_count ?? files.filter((f) => f.on_server && f.downloaded).length;
  const allDone = done === total;
  const requiredMissing = shownRows.filter(isFetchable).filter(fetchedAsRequired).length;
  const showRequired = requiredMissing > 0 && !serverOffline;
  const showAll = !allDone && files.some(isFetchable) && !serverOffline;
  // What the delete would remove — a record count, not a library one. There is
  // no local fallback: the rows say nothing about who downloaded a file, so a
  // payload without the field offers no delete rather than guessing.
  const deletable = answer.deletable_count ?? 0;

  const confirmDeleteBios = () =>
    showModal(
      <ConfirmModal
        strTitle={`Delete Tender's BIOS downloads for ${platformName}?`}
        strDescription={`Tender removes every BIOS file it downloaded for ${platformName} and every link to them. Files you placed yourself stay.`}
        strOKButtonText="Delete"
        strCancelButtonText="Cancel"
        onOK={() => {
          focus.mark(BULK_DELETE);
          actions.deleteBios(slug);
          focus.refocus(BULK_DELETE);
        }}
        onCancel={() => focus.refocus(BULK_DELETE)}
      />,
    );
  /** A bulk button's state: the pressed one stays enabled while its own work runs. */
  const bulkDisabled = (key: string, idle: boolean) => !idle || (busy && focus.pressed !== key);
  const pressBulk = (key: string, work: () => void) => () => {
    if (busy) return;
    focus.mark(key);
    work();
  };

  return (
    <>
      {requiredLabel !== null && (
        <ButtonRow padding="2px 16px 6px">
          <div style={{ display: "contents" }} ref={focus.slot(BULK_REQUIRED)}>
            <DialogButton
              style={BULK_BUTTON}
              disabled={bulkDisabled(BULK_REQUIRED, showRequired)}
              onClick={pressBulk(BULK_REQUIRED, () => actions.downloadRequired(slug, shownEmulator))}
            >
              {downloadLabel(
                part,
                "required",
                `${requiredLabel} (${requiredMissing})`,
                12,
                bulkDisabled(BULK_REQUIRED, showRequired),
              )}
            </DialogButton>
          </div>
        </ButtonRow>
      )}
      {/* The platform-wide pair, apart from the shown emulator's:
          "Download all" fetches every file the library holds for the platform,
          and the delete takes every download of Tender's own for it — its
          number is the backend's `deletable_count`, the records still on disk,
          which is exactly what the delete unlinks. Delete is local-only (no
          server needed). */}
      <SubHeading title={`For all of ${platformName}`} />
      <ButtonRow padding="2px 16px 6px">
        <div style={{ display: "contents" }} ref={focus.slot(BULK_ALL)}>
          <DialogButton
            style={BULK_BUTTON}
            disabled={bulkDisabled(BULK_ALL, showAll)}
            onClick={pressBulk(BULK_ALL, () => actions.downloadAll(slug))}
          >
            {downloadLabel(part, "all", "Download all", 12, bulkDisabled(BULK_ALL, showAll))}
          </DialogButton>
        </div>
        <div style={{ display: "contents" }} ref={focus.slot(BULK_DELETE)}>
          <DialogButton
            style={BULK_BUTTON}
            disabled={bulkDisabled(BULK_DELETE, deletable > 0)}
            onClick={() => {
              if (busy) return;
              focus.hold(BULK_DELETE);
              confirmDeleteBios();
            }}
          >
            {redWhileEnabled(`Delete downloads (${deletable})`, bulkDisabled(BULK_DELETE, deletable > 0))}
          </DialogButton>
        </div>
      </ButtonRow>
      <GroupStatus status={status} forKey={slug} scope="bios" />
    </>
  );
};

/** The library's files no emulator here asks for, each with its own Download. */
export const BiosLibraryTable: FC<{ part: BiosPart }> = ({ part }) => {
  const { input, libraryRows, identifications } = part;
  if (libraryRows.length === 0) return null;
  const libraryFetchable = new Set(libraryRows.filter(isFetchable).map((f) => f.file_name));
  return (
    <>
      <SubHeading title="In your RomM library, asked for by no emulator here" />
      <BiosTableHeader />
      {libraryRows.map((file) => (
        <div key={file.file_name} ref={part.focus.slot(libraryKey(file.file_name))}>
          <BiosFileRow
            file={file}
            action={rowAction(part, file, libraryFetchable, libraryKey(file.file_name))}
            emulator={input.answer.active_core_label ?? null}
            placeLines={[
              ...libraryRowLines(file, file.library ? identifications.answers[file.library.md5] : undefined),
              ...(input.actions.attemptFor(input.slug, null, downloadName(file)).damaged ? [DOWNLOAD_DAMAGED] : []),
            ]}
            busyLine={file.library && identifications.asking === file.library.md5 ? IDENTIFYING : null}
            stop={input.stop}
          />
        </div>
      ))}
    </>
  );
};

/** What the marks mean, over both tables' rows. */
export const BiosPartLegend: FC<{ part: BiosPart; inline?: boolean }> = ({ part, inline = false }) =>
  part.shownRows.length + part.libraryRows.length > 0 ? (
    <BiosLegend files={[...part.shownRows, ...part.libraryRows]} inline={inline} />
  ) : null;
