/**
 * Everything about one platform, in the Platforms tab's right-hand pane: what
 * it holds, which core launches it, the BIOS files that core wants, and the two
 * ways to take it back out of Steam.
 *
 * The pane scrolls by moving focus, so every row a reader must reach is a focus
 * stop — the BIOS table's rows included, which is why a row with nothing to
 * press still carries an activate handler.
 *
 * The sync toggle is deliberately absent: it lives in the list row, where the
 * focus already is.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Library.
 */

import { useState, type FC, type ReactNode } from "react";
import { ConfirmModal, DialogButton, Focusable, showContextMenu, showModal, Spinner } from "@decky/ui";
import { FaMicrochip } from "react-icons/fa";
import type { BiosFileStatus, FirmwarePlatformExt, SystemCoreInfo } from "../../types";
import { biosColorForLevel } from "../../utils/biosColor";
import { downloadName, fetchedAsRequired, isFetchable } from "../../utils/biosFetchable";
import { DOWNLOAD_DAMAGED, biosPlaceLines, libraryRowLines, storeOnlySentence } from "../../utils/biosPlaceWords";
import { useIdentifications } from "../../utils/firmwareIdentifications";
import { biosFileDescription, biosFileNote, contentsWords } from "../../utils/biosFileNote";
import { needWords, unmetWord } from "../../utils/biosGroup";
import { biosHeldRatio } from "../../utils/biosHeldRatio";
import { biosSummary, saysNoBiosChosen } from "../../utils/biosSummary";
import { isUnclaimedLibraryRow, selectedTab, tabLabel, tabRows } from "../../utils/biosTabs";
import { emulatorClause } from "../../utils/emulatorClause";
import { buildEmulatorMenu } from "../../utils/emulatorMenu";
import { emulatorDataReasonSentence, platformSystemClause } from "../../utils/emulatorSourceWording";
import { getEventTarget } from "../../utils/events";
import { EmulatorSelector } from "../EmulatorSelector";
import { WaitingLine } from "../WaitingLine";
import { entryForLabel } from "../../utils/emulatorEntries";
import { pluralize } from "../../utils/pluralize";
import { SYNC_RUNNING_HINT, useSyncRunning } from "../../utils/syncRunning";
import {
  AMBER,
  BusyElsewhere,
  ButtonRow,
  FLAT_BUTTON,
  GREEN,
  GroupStatus,
  MUTED,
  Muted,
  NOTE_FONT,
  PALE_GREEN,
  PANE_GUTTER,
  PANE_TABLE_REGISTER,
  PaneTableHeader,
  PaneTableRow,
  RED,
  ROW_BUTTON,
  SECONDARY_FONT,
  SectionTitle,
  SubHeading,
  VIOLET,
  type ScopedStatus,
  type TableRegister,
} from "../layout/pane";
import type { CoreAnswer, DetailStatus, PlatformRow, PlatformsPageState, StatusScope } from "./usePlatformsPage";
import { useBiosFocus, type BiosFocus } from "./useBiosFocus";

/** The page's status line as the pane primitives read it: on this page the key
 *  a line is bound to is the platform slug. The scope type travels with it, so
 *  the literal each `GroupStatus` names is checked against the page's groups. */
const scopedStatus = (status: DetailStatus | null): ScopedStatus<StatusScope> | null =>
  status && { key: status.slug, scope: status.scope, text: status.text };

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

/**
 * The core picker's button in the header line — the game page's icon button, at
 * this line's scale.
 *
 * The chrome is written out rather than reusing `.romm-gear-btn`, for a reason
 * that needs no claim about which document the class reaches: that class is
 * 36×36 (`styleInjector.ts`), which is the game page's play row, and this line
 * is 28px tall. What IS shared is what the user asked to be shared — the icon
 * and its two colours.
 */
const CORE_BUTTON = {
  alignSelf: "center",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: "28px",
  minWidth: "28px",
  height: "28px",
  padding: 0,
  borderRadius: "4px",
  flexShrink: 0,
} as const;

/** One row of the firmware overview's per-platform file list. Named off the
 *  payload rather than restated, so a field added to it reaches here. */
type FirmwareRow = FirmwarePlatformExt["files"][number];

/** A row the BIOS table renders: the payload's own `files`, or another
 *  emulator's rows, which carry no server record of their own. */
type BiosTableRow = FirmwareRow | BiosFileStatus;

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
const BiosRowLines: FC<{ lines: string[] }> = ({ lines }) =>
  lines.length === 0 ? null : (
    <div style={{ display: "flex", flexDirection: "column", marginLeft: "18px" }}>
      {lines.map((line) => (
        <div key={line} style={{ fontSize: NOTE_FONT, lineHeight: 1.2, color: MUTED, whiteSpace: "pre-wrap" }}>
          {line}
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
 * What the marks in `On disk` mean, under the table that uses them.
 *
 * One entry per line, which is what makes a two-mark cell readable and is what
 * keeps the filter doing real work: only the entries the table actually
 * contains, because a line for a state no row is in explains nothing and costs
 * a row, the scarce thing on this pane. Mark 2 is inside that filter as well —
 * a platform whose library holds every file shows no line for it — and gets one
 * line rather than one per pairing, since it means the same beside every
 * verdict. The order is the order a reader cares about: what is wrong first,
 * then the second channel — and each of a group option's two states sits beside
 * the ordinary mark it shares a colour with, since a reader meeting two red `✗`
 * lines is being told what separates them.
 *
 * The entries carry the same strings the rows' tooltips do, and match on them:
 * see the `MARK_*` block for why the glyph and colour cannot be the identity.
 */
const BiosLegend: FC<{ files: readonly BiosTableRow[] }> = ({ files }) => {
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
        flexDirection: "column",
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

const BiosFileRow: FC<{ file: BiosTableRow; action: ReactNode; emulator: string | null; placeLines: string[] }> = ({
  file,
  action,
  emulator,
  placeLines,
}) => {
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
  return (
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
      focusStop={!action}
      cells={[
        {
          // With the folder in front of it what gets cut is the NAME rather
          // than the description that used to sit here —
          // `scummvm/extra/hadesch_translations.dat` does not fit 202px in any
          // arrangement. The title is the mouse's way back to it; a reader on
          // the controller has none, and the only real fix is width the list
          // column currently holds.
          //
          // What the shown emulator needs of the file stands under the name in
          // the same cell: the mark's colour alone does not tell a
          // met required file from a met optional one, and a two-line cell is
          // what the row's button is centred against.
          content: (
            <>
              <span style={NAME_LINE}>
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
      {/* The description is a muted line UNDER the row, where the game page's
          BIOS tab puts the same label beside the file's name. That is a column
          width rather than a difference of opinion, and it is why the two are
          not "unified": this name lives in a ~202px table cell that clips, and
          it already gives up its folder prefix to the same shortage — hanging
          the label off it would take the NAME off the screen, which is the one
          thing a reader placing a file by hand needs. Under the row there is a
          whole width for it, on one clipped line. */}
      {description && <div style={UNDER_NAME_LINE}>{description}</div>}
      <BiosRowLines lines={rowLines} />
    </PaneTableRow>
  );
};

/**
 * What the pane can offer for this platform's core: a pick, or a sentence
 * saying why there is nothing to pick.
 *
 * One decision, because the answer is rendered in two places — the header's
 * icon button and the line under it — and splitting it would let the two
 * disagree, which is a sentence saying there is nothing to switch under a
 * button that switches.
 */
/**
 * What the header's core control can do, and what to say about it.
 *
 * `pick` opens the menu. `blocked` renders the SAME chip, disabled, with
 * `reason` in its tooltip — a control that is always there and greyed when
 * there is nothing to press, which is the ruling the Remove group already
 * follows. A sentence costs a row to report that nothing can be done; the
 * tooltip costs none and keeps the header's shape constant across panes.
 *
 * `notice` is for the branches that are not "nothing to switch" but a problem
 * with the platform or the install — a failed read, no emulator list from the
 * answering source, no emulator at all. Those keep their line, because a greyed
 * chip would hide a failure behind a hover the Deck's controller has no way to
 * perform.
 */
/**
 * The header's second line: the facts about the platform, smaller and muted.
 *
 * Each part keeps itself on one line and carries the separator in front of it,
 * so the line wraps only between parts — at the Deck's width a free-flowing
 * line broke "RetroDECK system snes" after its first word — and never cuts one.
 */
const HeaderFacts: FC<{ parts: { text: string; color?: string }[] }> = ({ parts }) => (
  <div
    data-testid="platform-facts"
    style={{
      display: "flex",
      flexWrap: "wrap",
      columnGap: "4px",
      padding: "2px 16px 0",
      fontSize: NOTE_FONT,
      color: MUTED,
    }}
  >
    {parts.map((part, index) => (
      <span key={part.text} style={{ whiteSpace: "nowrap", color: part.color }}>
        {index === 0 ? part.text : `· ${part.text}`}
      </span>
    ))}
  </div>
);

type CoreOffer = { kind: "pick"; core: SystemCoreInfo } | { kind: "blocked"; reason: string; notice?: string };

function coreOffer(core: CoreAnswer): CoreOffer {
  if (core === undefined) return { kind: "blocked", reason: "Reading the emulators for this platform…" };
  if (core === null) {
    const failed = "Could not read the emulators for this platform. Reopen the page to try again.";
    return { kind: "blocked", reason: failed, notice: failed };
  }
  if (!core.emulator_data_available) {
    const absent = emulatorDataReasonSentence(core.emulator_data_reason, core.emulator_source, core.platform_system);
    return { kind: "blocked", reason: absent, notice: absent };
  }
  // An EMPTY menu first, because it is the one case where the fallback fails
  // too: `get_emulator_options` answers `available: true` with no options for a
  // system whose catalogue entry lists no command. RetroDECK's own launch then
  // reads `command[1]` for the system, finds nothing, logs "No valid emulator
  // found for system" and exits 1 (`libexec/run_game.sh`), so the games really
  // do not start.
  if (core.emulators.length === 0) {
    const none = "RetroDECK lists no emulator for this platform, so its games will not launch.";
    return { kind: "blocked", reason: none, notice: none };
  }
  // Then the not-bakeable menu, ahead of the counts because it is not one:
  // `active_core_label` is null when nothing ES-DE lists is BAKEABLE, which says
  // nothing about how many entries there are. A platform whose only entry is a
  // standalone emulator this RetroDECK has not installed lands here with one
  // option, and one with two uninstalled ones lands here with two — where a
  // count-shaped branch would have said "offers one emulator" or nothing at all.
  //
  // What the fallback then DOES splits the state in two, and the payload can
  // tell them apart. `run_game.sh` takes `command[1]` for the system when no
  // alternate emulator is set, and `options` keeps ES-DE's document order, so
  // `emulators[0]` IS that command. If its emulator is the one RetroDECK has
  // not installed, the fallback names a binary that is not there and the games
  // do not start. Every other reason leaves the muted branch, which is where
  // Apple I lands: run through this repo's own `classify_command` over the
  // shipped `es_systems.xml`, its two commands come back
  // `no_rom_target` and `quoting`, both `kind: "standalone"` with a null
  // `core_so`. The first is unbakeable because its whole MAME invocation is one
  // quoted argument, so it does not end in `%ROM%` — a different reason from
  // the second's, and neither is `not_installed`.
  if (core.active_core_label === null) {
    const fallback = core.emulators[0];
    if (fallback?.reason === "not_installed") {
      return {
        kind: "blocked",
        reason: `RetroDECK would launch these with ${fallback.name}, which is not installed — and nothing here can pin a different one.`,
        notice: `RetroDECK would launch these with ${fallback.name}, which is not installed — and nothing here can pin a different one.`,
      };
    }
    return {
      kind: "blocked",
      reason: "None of this platform's emulators can be pinned from here, so RetroDECK picks one when a game launches.",
      notice: "None of this platform's emulators can be pinned from here, so RetroDECK picks one when a game launches.",
    };
  }
  // One count branch, and it is exactly true: an empty menu was answered two
  // branches up and a not-bakeable one the branch after it, so a single option
  // here is a single BAKEABLE one — there really is nothing to switch to.
  if (core.emulators.length === 1) {
    return { kind: "blocked", reason: "This platform offers one emulator, so there is nothing to switch." };
  }
  return { kind: "pick", core };
}

/**
 * The line under the header when there is no core to pick, and the place a
 * refused switch is reported.
 *
 * There is no heading and no button: the button moved into the header line the
 * platform is already named on, which is where the game page keeps its own. The
 * save-compatibility warning moved with it — into the picker, where
 * `buildEmulatorMenu` has always rendered it, so a copy here was the same
 * sentence on the page that opens the menu carrying it.
 */
const CoreNotice: FC<{ row: PlatformRow; state: PlatformsPageState; offer: CoreOffer }> = ({ row, state, offer }) => (
  <>
    {offer.kind === "blocked" && offer.notice !== undefined && <Muted>{offer.notice}</Muted>}
    <GroupStatus status={scopedStatus(state.status)} forKey={row.slug} scope="core" />
  </>
);

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
function downloadState(row: PlatformRow, state: PlatformsPageState, id: string): "spinner" | "failed" | null {
  if (state.busySlug !== row.slug) return null;
  if (state.downloadPending === id) return "spinner";
  return state.downloadFailed === id ? "failed" : null;
}

/**
 * *text* in red while its button can be pressed. A disabled button shows only
 * Steam's own `Disabled` look, so no colour of ours is laid over it.
 */
function redWhileEnabled(text: string, disabled: boolean): ReactNode {
  return disabled ? text : <span style={{ color: RED }}>{text}</span>;
}

/** The removal confirmation's title. The count is interleaved with the
 *  platform's name, so this is the one of the three count labels `pluralize`
 *  cannot spell; an unread count drops the number rather than guessing one. */
function removeShortcutsTitle(row: PlatformRow): string {
  if (row.shortcutCount === null) return `Remove ${row.name} shortcuts?`;
  return `Remove ${row.shortcutCount} ${row.name} shortcut${row.shortcutCount === 1 ? "" : "s"}?`;
}

/**
 * The colour the header's core clause and its chip both take.
 *
 * Three outcomes in priority order, which is what the nested form obscured:
 * a state the games cannot start in is red, an override the user chose is gold,
 * and everything else — including a platform running its default — is muted.
 * Both callers read it from here so the icon and the words beside it cannot
 * disagree.
 */
function coreClauseColour(gamesCannotStart: boolean, isOverride: boolean): string {
  if (gamesCannotStart) return RED;
  return isOverride ? AMBER : MUTED;
}

/** The label a download button carries right now — its own word unless its run
 *  is speaking; *disabled* is that button's own state. */
function downloadLabel(
  row: PlatformRow,
  state: PlatformsPageState,
  id: string,
  label: string,
  size: number,
  disabled: boolean,
): ReactNode {
  const showing = downloadState(row, state, id);
  if (showing === "spinner") return <Spinner width={size} height={size} />;
  return showing === "failed" ? redWhileEnabled("Failed", disabled) : label;
}

/**
 * A BIOS row's one action: fetch it, replace what lies at its place, or remove
 * the copy Tender holds — or nothing, which is a `null` the row needs in order
 * to wrap itself in a `Focusable`.
 *
 * The buttons are alternatives in every state the pane can show, so one narrow
 * column carries them. Download wins where a row is both fetchable and
 * deletable — the file the emulator will look for is the one that is missing.
 * "Replace…" is the backend's decision alone (`replace_offered`), and its
 * confirmation names the exact path and where the old file goes.
 *
 * **The delete's condition is `delete_name` and nothing else.** It names
 * Tender's own store file, an older download of the library file that fills
 * the row, or an older download lying at the row's place, which is the whole
 * authority; `downloaded` is the resolver's
 * presence at the row's place and is equally true of a file the user placed.
 * The removal itself re-reads the records and takes only Tender's own files
 * and links, so this button cannot widen what it removes.
 */
function rowAction(
  row: PlatformRow,
  state: PlatformsPageState,
  file: BiosTableRow,
  fetchable: Set<string>,
  backupRoot: string | null,
  focus: BiosFocus,
  key: string,
): ReactNode | null {
  const busy = state.busySlug !== null;
  // The pressed row's own button stays enabled while its work runs, and every
  // other one waits greyed — still a focus stop, since Steam's
  // `DialogButton` never disables its element, as its shipped client code
  // shows (`steamui/sp.js`). A press on this one while anything runs does
  // nothing.
  const disabled = busy && focus.pressed !== key;
  if (fetchable.has(file.file_name) && !state.serverOffline) {
    const name = downloadName(file);
    return (
      <DialogButton
        style={ROW_BUTTON}
        disabled={disabled}
        onClick={() => {
          if (busy) return;
          focus.mark(key);
          state.downloadOne(row.slug, name, file.library?.firmware_id ?? null);
        }}
      >
        {downloadLabel(row, state, name, "Download", 11, disabled)}
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
          strDescription={`The file at ${place} goes to ${backupRoot ?? "the backup folder"}, and Tender's copy is linked in its place.`}
          strOKButtonText="Replace"
          strCancelButtonText="Cancel"
          onOK={() => {
            focus.mark(key);
            state.replaceBios(row.slug, place);
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
          state.deleteBiosFile(row.slug, name);
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

const BiosSection: FC<{ row: PlatformRow; state: PlatformsPageState; firmware: FirmwarePlatformExt }> = ({
  row,
  state,
  firmware,
}) => {
  const files = firmware.files;
  // Which emulator the table shows: the launching one until another is chosen.
  // What launches, the header and its counts stay the launching emulator's;
  // the table, its per-row actions and "Download required" follow the shown
  // one. The library's files no emulator here declares are no emulator's rows,
  // and stand in a section of their own whichever emulator is shown.
  const [tabKey, setTabKey] = useState<string | null>(null);
  const tabs = firmware.emulator_tabs;
  const tab = selectedTab(tabs, tabKey);
  const shownRows = tabRows(files, tabs, tab);
  const busy = state.busySlug !== null;
  const focus = useBiosFocus(
    busy,
    shownRows.map((file) => tableKey(file.file_name)),
  );
  const { slot, attachRoot } = focus;
  const libraryRows = files.filter(isUnclaimedLibraryRow);
  // What the resolver knows each library file's content as, asked after the
  // page is shown, one file at a time.
  const identifications = useIdentifications(
    libraryRows.flatMap((file) => (file.library?.md5 ? [file.library.md5] : [])),
  );
  const backupRoot = firmware.backup_root ?? null;
  const platformName = (slug: string) => state.rows.get(slug)?.name ?? slug;
  const otherTab = tab !== null && !tab.launching;
  // The emulator a download from this table is for: the shown one's identity on
  // another emulator's view, the platform's own pick (`null`) on the launching
  // one's — which also covers a launching emulator the answer holds no entry for.
  const shownEmulator = otherTab ? tab.emulator : null;
  // "Download required" fetches one emulator's requirement and is labelled with
  // its name. Where nothing identifies the launching emulator it has no entry,
  // so the shown entry is the first other emulator's and the button is that
  // one's; with no entry at all there is no emulator to name and no button.
  const shownLabel = tab ? tabLabel(tab) : null;
  const requiredLabel = shownLabel === null ? null : `Download required for ${shownLabel}`;
  // The library's own progress, read nowhere but `allDone`, which is one of
  // `showAll`'s conditions. Counted over what the LIBRARY holds, not over the
  // rows — the rows include files no library holds, and offering to fetch those
  // is work the user cannot do. What the pane STATES is that pair narrowed to the
  // rows whose verdict was established (`utils/biosHeldRatio`, over
  // `ratio_server_count` / `ratio_local_count`).
  const total = firmware.server_count ?? files.filter((f) => f.on_server).length;
  const done = firmware.local_count ?? files.filter((f) => f.on_server && f.downloaded).length;
  const allDone = done === total;

  // **The pane words none of the states itself** — `utils/biosSummary.ts` holds
  // them, and this surface takes both halves of one answer: the short `status`
  // as the section's coloured note, where the title beside it says what is being
  // counted, and the `sentence` under it. The order they are read in is decided
  // in there too, which is what stops this pane from reading it differently from
  // the game page's.
  const summary = biosSummary(firmware, files, firmware.bios_level ?? null, row.name || null);
  const { status: summaryLabel, sentence: summaryDescription } = summary;
  // The library's own ratio rides along behind the sentence, in every state and
  // in the words the game page uses — `utils/biosHeldRatio`
  // writes it for both, because the two surfaces say one thing about a platform
  // and a fact one of them carries is a fact the other is missing.
  const heldRatio = biosHeldRatio(firmware);
  // The narrowest of the declines: the emulator this platform launches with was
  // not read at all, so the pane has nothing to point the reader at and says
  // where a file can be put instead. A verdict the resolver left open over an
  // emulator it DID read is not this. It decides one extra LINE only — what the
  // pane offers to fetch is a separate question with a separate input, below.
  const nothingEstablished = firmware.bios_level === "unknown" && firmware.emulator_read === false;
  // Why a download here lands in Tender's firmware store and is linked nowhere.
  const storeOnly = storeOnlySentence(firmware.store_only, firmware.active_core_label ?? null, row.name);

  // The download affordances key off what is missing AND fetchable, and read
  // the VERDICT nowhere — not `bios_level`, not `required_withheld`. They are
  // two independent questions: what the RESOLVER could establish is the
  // emulator's demand, what is FETCHABLE is what the RomM library holds, and
  // neither answers the other. A required file the library
  // does not hold leaves the platform not ready and still gives the user
  // nothing to press here; a platform nothing could be read for still has a
  // library behind it, and fetching from it is the one action that moves the
  // platform along at all.
  //
  // `Download required` counts the SHOWN emulator's own rows the backend marks
  // `fetch_for_required` — its required files, and the options of the regions
  // its one-of group does not cover yet, the one rule the download itself
  // applies — and `Download all` stops at the library's own finished ratio. So
  // neither is a readiness gate either.
  //
  // Reading readiness here is what took the buttons off PS2, GameCube and PSP
  // the moment a BIOS answer was scoped to the emulator that actually launches:
  // those launch standalone emulators the resolver holds no card for, so the
  // verdict declines — which says nothing whatever about the files their
  // library offers.
  //
  // Which rows are fetchable at all — the library's side of it, including why a
  // declared folder is out — is `isFetchable`, shared with the game page's BIOS
  // tab so the two surfaces cannot disagree about what can be downloaded.
  const shownFetchable = shownRows.filter(isFetchable);
  const requiredMissing = shownFetchable.filter(fetchedAsRequired).length;
  const showRequired = requiredMissing > 0 && !state.serverOffline;
  const showAll = !allDone && files.some(isFetchable) && !state.serverOffline;
  const fetchable = new Set(shownFetchable.map((f) => f.file_name));
  const libraryFetchable = new Set(libraryRows.filter(isFetchable).map((f) => f.file_name));
  // What the delete would remove — a record count, not a library one. There is
  // no local fallback: the rows say nothing about who downloaded a file, so a
  // payload without the field offers no delete rather than guessing.
  const deletable = firmware.deletable_count ?? 0;

  const confirmDeleteBios = () =>
    showModal(
      <ConfirmModal
        strTitle={`Delete Tender's BIOS downloads for ${row.name}?`}
        strDescription={`Tender removes every BIOS file it downloaded for ${row.name} and every link to them. Files you placed yourself stay.`}
        strOKButtonText="Delete"
        strCancelButtonText="Cancel"
        onOK={() => {
          focus.mark(BULK_DELETE);
          state.deleteBios(row.slug);
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
    <div ref={attachRoot}>
      {/* The ratio is stated once, here, and takes the same mapping the list's
          dot takes — the header carried a second copy of it until the device
          pass, and its width was what wrapped that line three times. Two places
          state a platform's BIOS state and they now agree by construction. */}
      <SectionTitle title="BIOS files" note={summaryLabel} noteColor={biosColorForLevel(firmware.bios_level ?? null)} />
      <Muted fontSize={NOTE_FONT}>{`${summaryDescription}${heldRatio}`}</Muted>
      {/* The route the summary above cannot name: nothing here could say which
          files this system wants, so the reader has to be told that placing one
          by hand still works. Not under the sentence that no BIOS is chosen in
          the emulator's settings: there the setting is what is
          missing, and placing a file changes nothing. */}
      {storeOnly !== null && <Muted fontSize={NOTE_FONT}>{storeOnly}</Muted>}
      {nothingEstablished && !saysNoBiosChosen(summary) && (
        <Muted fontSize={NOTE_FONT}>You can still put BIOS files in your BIOS folder by hand.</Muted>
      )}
      <EmulatorSelector
        tabs={tabs}
        shown={tab}
        onSelect={(chosen) => focus.step(() => setTabKey(chosen.launching ? null : chosen.emulator))}
        padding="4px 16px 6px"
      />
      {shownRows.length > 0 && <BiosTableHeader />}
      {shownRows.map((file) => (
        <div key={file.file_name} ref={slot(tableKey(file.file_name))}>
          <BiosFileRow
            file={file}
            // Every emulator's rows offer their download and their delete; a
            // download lands in the store and is linked for every emulator its
            // source does not state is not installed.
            action={rowAction(row, state, file, fetchable, backupRoot, focus, tableKey(file.file_name))}
            emulator={shownLabel ?? firmware.active_core_label ?? null}
            placeLines={biosPlaceLines(
              file,
              shownLabel ?? firmware.active_core_label ?? null,
              state.attemptFor(row.slug, file.local_path, downloadName(file)),
              platformName,
            )}
          />
        </div>
      ))}
      {/* One row of buttons rather than stacked full-width ones. Each
          `ButtonItem` is a `Field` row around a button and costs the pane a row
          of its own.

          Every button is always rendered and disables when there is nothing to
          do, the ruling the user gave for the Remove group and for the same
          reason: a button that vanishes is a state the reader has to work out,
          and on PS2 all of them vanished at once. A disabled `DialogButton` is
          still a focus stop, so the row stays walkable. The one exception is
          "Download required" where no emulator has an entry: there is no
          requirement it could fetch, rather than nothing left to do. */}
      {requiredLabel !== null && (
        <ButtonRow padding="2px 16px 6px">
          <div style={{ display: "contents" }} ref={slot(BULK_REQUIRED)}>
            <DialogButton
              style={FLAT_BUTTON}
              disabled={bulkDisabled(BULK_REQUIRED, showRequired)}
              onClick={pressBulk(BULK_REQUIRED, () => state.downloadRequired(row.slug, shownEmulator))}
            >
              {downloadLabel(
                row,
                state,
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
      <SubHeading title={`For all of ${row.name}`} />
      <ButtonRow padding="2px 16px 6px">
        <div style={{ display: "contents" }} ref={slot(BULK_ALL)}>
          <DialogButton
            style={FLAT_BUTTON}
            disabled={bulkDisabled(BULK_ALL, showAll)}
            onClick={pressBulk(BULK_ALL, () => state.downloadAll(row.slug))}
          >
            {downloadLabel(row, state, "all", "Download all", 12, bulkDisabled(BULK_ALL, showAll))}
          </DialogButton>
        </div>
        <div style={{ display: "contents" }} ref={slot(BULK_DELETE)}>
          <DialogButton
            style={FLAT_BUTTON}
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
      <GroupStatus status={scopedStatus(state.status)} forKey={row.slug} scope="bios" />
      {libraryRows.length > 0 && (
        <>
          <SubHeading title="In your RomM library, asked for by no emulator here" />
          <BiosTableHeader />
          {libraryRows.map((file) => (
            <div key={file.file_name} ref={slot(libraryKey(file.file_name))}>
              <BiosFileRow
                file={file}
                action={rowAction(row, state, file, libraryFetchable, backupRoot, focus, libraryKey(file.file_name))}
                emulator={firmware.active_core_label ?? null}
                placeLines={[
                  ...libraryRowLines(file, file.library ? identifications[file.library.md5] : undefined),
                  ...(state.attemptFor(row.slug, null, downloadName(file)).damaged ? [DOWNLOAD_DAMAGED] : []),
                ]}
              />
            </div>
          ))}
        </>
      )}
      {shownRows.length + libraryRows.length > 0 && <BiosLegend files={[...shownRows, ...libraryRows]} />}
    </div>
  );
};

/**
 * Taking a platform back out of Steam, and taking its save files off the disk.
 *
 * Neither button is hidden when its own count is zero. Hiding the group on the
 * shortcut count alone strands a platform whose shortcuts were removed but whose
 * saves remain: those saves are then unreachable, and this is the only page that
 * offers them.
 *
 * The saves count is its own read (`count_platform_saves`) rather than a number
 * taken from somewhere cheaper, because nowhere else has it — the delete finds
 * its files through the platform's installed ROMs and counts only what it
 * removed, afterwards.
 */
const RemoveSection: FC<{ row: PlatformRow; state: PlatformsPageState }> = ({ row, state }) => {
  const syncRunning = useSyncRunning();
  const saveCount = state.saveCountFor(row.slug);
  const confirmDeleteSaves = () =>
    showModal(
      <ConfirmModal
        strTitle={`Delete all save files for ${row.name}?`}
        strDescription="This will delete every local save file for ROMs on this platform. Any local changes that haven't been uploaded to RomM yet will be lost permanently. Make sure saves are synced first."
        strOKButtonText="Delete Save Files"
        strCancelButtonText="Cancel"
        onOK={() => state.deleteSaves(row)}
      />,
    );
  const confirmRemoveShortcuts = () =>
    showModal(
      <ConfirmModal
        strTitle={removeShortcutsTitle(row)}
        strDescription="This takes this platform's games out of your Steam library. Downloaded ROM files and save files are left where they are, and the games come back on the next sync while the platform stays enabled."
        strOKButtonText="Remove Shortcuts"
        strCancelButtonText="Cancel"
        onOK={() => state.removeShortcuts(row)}
      />,
    );
  return (
    <>
      {/* One row, and no REMOVE heading over it: both buttons say what they
          remove and are drawn in red, so a title above them names nothing the
          buttons do not — and it would cost the pane a row. */}
      <ButtonRow padding="6px 16px 4px">
        <DialogButton
          style={FLAT_BUTTON}
          disabled={state.busySlug !== null || syncRunning || row.shortcutCount === 0}
          onClick={confirmRemoveShortcuts}
        >
          <span style={{ color: RED }}>
            {row.shortcutCount === null ? "Remove shortcuts" : `Remove ${pluralize(row.shortcutCount, "shortcut")}`}
          </span>
        </DialogButton>
        {/* Unread is not zero and is not a failure either, and the button must
            not look like either: while the count is still coming it is disabled
            and spins, which claims nothing. A pressable plain label would invite
            a press over an unknown set; a `0` would state an emptiness nobody
            established. A failed read is the third case and says so below. */}
        <DialogButton
          style={FLAT_BUTTON}
          disabled={state.busySlug !== null || saveCount === undefined || saveCount === 0}
          onClick={confirmDeleteSaves}
        >
          <span style={{ color: RED, display: "inline-flex", alignItems: "center", gap: "6px" }}>
            {saveCount === undefined && <Spinner width={12} height={12} />}
            {typeof saveCount === "number" ? `Delete ${pluralize(saveCount, "save file")}` : "Delete save files"}
          </span>
        </DialogButton>
      </ButtonRow>
      {/* One of the six reads this page makes. Its failure gets a line because
          with the spinner above it, a silent failure is a spinner that never
          stops — so the two land together. */}
      {saveCount === null && (
        <Muted>Could not read how many save files this platform holds. Pick the platform again to retry.</Muted>
      )}
      {/* The hint was a ButtonItem `description`, attached to the one button it
          was about; under a row it has nowhere to hang, so it names that button
          instead. Only the shortcut removal is refused during a sync — the
          `remove_platform_shortcuts` use case names the sync rule and
          `delete_platform_saves` deliberately does not — so an unscoped sentence
          claims a restriction the backend does not impose. Scoping the sentence
          rather than refusing the delete too: the sync rule is the authority on
          what a sync blocks, and widening it to make a line true would be the
          tail wagging the dog. */}
      {syncRunning && <Muted>{`Removing shortcuts: ${SYNC_RUNNING_HINT}`}</Muted>}
      <GroupStatus status={scopedStatus(state.status)} forKey={row.slug} scope="remove" />
    </>
  );
};

export const PlatformDetail: FC<{ row: PlatformRow; state: PlatformsPageState }> = ({ row, state }) => {
  const core = state.coreFor(row.slug);
  const firmware = row.firmware;
  const offer = coreOffer(core);
  // The core clause is absent while this platform's core read is in flight, and
  // stays absent if it failed — the read is issued per selection, so walking the
  // list shows each newly focused platform's header without a core until its own
  // answer lands. That, and a failed shortcut-count read dropping "· N in
  // Steam", are the two ways this line loses a piece; both failures now say so
  // on the pane, and the in-flight one is a beat rather than a state.
  const activeLabel = core ? core.active_core_label : null;
  const clause = emulatorClause(core ?? null);
  // The platform-level twin of the game page's `activeCoreIsDefault`, read off
  // the payload's own `is_default`, which marks the entry holding the option
  // `select_default_option` picks — whichever of its labels is stored. One
  // expression feeds the clause AND the icon, so the two cannot disagree — and
  // no active label is muted rather than gold, because no entry lists a null
  // label and "not the default" is not the same statement as "an override".
  const activeIsDefault = entryForLabel(core?.emulators ?? [], activeLabel)?.is_default ?? false;
  const coreColor = coreClauseColour(clause?.gamesCannotStart ?? false, activeLabel !== null && !activeIsDefault);
  const coreClause = clause === null ? null : { text: clause.text, color: coreColor };
  const systemClause = core?.platform_system ? platformSystemClause(core.platform_system) : null;

  return (
    <>
      {/* Two header lines rather than a Sync section: the toggle is in the list
          row, so what is left here is what the platform IS — and, since the
          device round, the core picker too: a full-width button under the
          header cost the pane a `Field`-height row and a warning line to say
          what the picker itself says. The name has the first line to itself,
          because sharing it with the facts squeezed a two-word name onto two
          lines over the counts at the Deck's width. */}
      <Focusable
        flow-children="horizontal"
        style={{ display: "flex", alignItems: "center", gap: "10px", padding: "8px 16px 0" }}
      >
        <span style={{ flex: "1 1 auto", minWidth: 0, fontSize: "16px", fontWeight: 600, color: "#dcdedf" }}>
          {row.name}
        </span>
        {/* Always rendered, disabled when there is nothing to pick, with the
            reason in the tooltip — the same ruling the Remove group follows.
            A disabled button is still a focus stop, and the wide page's own
            sheet gives it Steam's focus outline, so a reader walking the header
            still lands on it and is told why it is dead. */}
        <DialogButton
          style={CORE_BUTTON}
          title={offer.kind === "pick" ? "Emulator Core" : offer.reason}
          disabled={offer.kind !== "pick" || state.busySlug !== null}
          onClick={(e: MouseEvent) => {
            if (offer.kind !== "pick") return;
            showContextMenu(
              buildEmulatorMenu({
                emulators: offer.core.emulators,
                emulatorDataAvailable: offer.core.emulator_data_available,
                emulatorDataReason: offer.core.emulator_data_reason,
                emulatorSource: offer.core.emulator_source,
                emulatorPlatformSystem: offer.core.platform_system,
                activeLabel: offer.core.active_core_label,
                // Null on purpose: this pane IS the platform level, so marking
                // an entry "(system)" would restate where the reader already is.
                platformCoreLabel: null,
                onPick: (label) => state.changeCore(row.slug, label),
              }),
              getEventTarget(e),
            );
          }}
        >
          <FaMicrochip size={16} color={coreColor} />
        </DialogButton>
      </Focusable>
      {/* Both counts are ROM FILES, which is what makes the pair readable: one
          shortcut serves a whole sibling group and the game's page switches
          versions across it, so a version that did not win the binding is
          still reachable and still belongs on the right. Counting shortcuts
          there instead read as "207 are missing" on a platform where nothing
          was. The Remove button below keeps the shortcut count — that one
          really is about Steam entries. */}
      <HeaderFacts
        parts={[
          ...(coreClause ? [{ text: coreClause.text, color: coreClause.color }] : []),
          { text: `${row.romCount} on RomM` },
          ...(row.reachableCount === null ? [] : [{ text: `${row.reachableCount} in Steam` }]),
          ...(systemClause === null ? [] : [{ text: systemClause }]),
        ]}
      />
      {/* The count is what failed, not the removal: taking the platform's games
          out of Steam needs only the slug. So the line says the number is
          missing and stops there — the buttons below stay live. */}
      {state.shortcutCountsFailed && (
        <Muted>
          Could not read how many of these games are in Steam. Removing them still works, it just cannot say how many.
          Reopen the page to try again.
        </Muted>
      )}
      {state.removalProgress?.slug === row.slug && (
        <Muted>{`Removing ${state.removalProgress.removed} of ${state.removalProgress.total}…`}</Muted>
      )}
      <BusyElsewhere
        busyKey={state.busySlug}
        ownKey={row.slug}
        busyName={state.rows.get(state.busySlug ?? "")?.name ?? "another platform"}
      />
      <CoreNotice row={row} state={state} offer={offer} />
      {/* A failed RE-read keeps the answer it could not replace, so what is
          below is the state from before whatever changed it — which is exactly
          where a reader needs telling, and where the notice used to be silent.
          A read that failed with nothing behind it says so where the rows would
          be, below. */}
      {row.firmwareStale && (
        <Muted>
          Could not re-read the BIOS state, so what is below may be out of date. Pick the platform again to retry.
        </Muted>
      )}
      {firmware ? (
        <BiosSection key={row.slug} row={row} state={state} firmware={firmware} />
      ) : (
        <>
          <SectionTitle title="BIOS files" />
          {/* Two ways to have no answer, and they are two different
              sentences. A read still out is a question nobody has answered yet
              — said as one, because the alternative is the reader taking the
              silence for "nothing needed" on a platform whose turn simply has
              not come. A read that did not come back is a question that could
              not be asked. */}
          {row.firmwareState === "pending" && (
            <Muted fontSize={NOTE_FONT}>
              <WaitingLine>Checking what this platform needs…</WaitingLine>
            </Muted>
          )}
          {row.firmwareState === "failed" && (
            <Muted fontSize={NOTE_FONT}>Could not read the BIOS state. Pick the platform again to retry.</Muted>
          )}
        </>
      )}
      <RemoveSection row={row} state={state} />
    </>
  );
};
