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
import type { EmulatorTab } from "../../types";
import { downloadName, isFetchable } from "../../utils/biosFetchable";
import {
  biosDiskMark,
  biosFileDescription,
  biosLegendEntries,
  biosLibraryMark,
  biosTableRowLines,
  contentsWords,
  declaredFolder,
  type BiosTableRow,
} from "../../utils/biosFileNote";
import { needWords } from "../../utils/biosGroup";
import { DOWNLOAD_DAMAGED, IDENTIFYING, biosPlaceLines, libraryRowLines } from "../../utils/biosPlaceWords";
import { isUnclaimedLibraryRow, selectedTab, tabLabel, tabRows } from "../../utils/biosTabs";
import { useIdentifications } from "../../utils/firmwareIdentifications";
import {
  biosBulkOffer,
  biosDownloadButtonState,
  biosRowAction,
  deleteBiosFileConfirmCopy,
  deletePlatformBiosConfirmCopy,
  replaceBiosConfirmCopy,
  type BiosActions,
  type BiosAnswerFields,
  type PlatformKey,
} from "../../utils/useBiosActions";
import { Throbber, ThrobberLabel } from "../../shared/Throbber";
import { EmulatorSelector } from "../EmulatorSelector";
import {
  ButtonRow,
  FLAT_BUTTON,
  GroupStatus,
  MUTED,
  NOTE_FONT,
  PANE_GUTTER,
  PANE_TABLE_REGISTER,
  PaneTableHeader,
  PaneTableRow,
  RED,
  ROW_BUTTON,
  SECONDARY_FONT,
  SubHeading,
  type ScopedStatus,
  type TableRegister,
} from "../layout/pane";
import { useBiosFocus, type BiosFocus } from "./useBiosFocus";

export type { BiosAnswerFields, BiosTableRow };

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
 * across three lines — the cost the marks were introduced to stop paying.
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
 * What the marks in `On disk` mean, under the tables that use them.
 *
 * On the platform pane one entry per line; on a game's BIOS tab (`inline`) the
 * entries stand side by side under the left column's buttons, wrapping where
 * the column runs out.
 */
const BiosLegend: FC<{ files: readonly BiosTableRow[]; inline?: boolean }> = ({ files, inline = false }) => {
  const shown = biosLegendEntries(files);
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
  const contents = contentsWords(file, emulator);
  const mark = biosDiskMark(file);
  const library = biosLibraryMark(file);
  const description = biosFileDescription(file);
  const folder = declaredFolder(file);
  const rowLines = biosTableRowLines(file, emulator, placeLines);
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
        { content: contents, clip: false, style: CONTENTS_CELL },
        { content: action, clip: false },
      ]}
    >
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
 * *text* in red while its button can be pressed. A disabled button shows only
 * Steam's own `Disabled` look, so no colour of ours is laid over it.
 */
function redWhileEnabled(text: string, disabled: boolean): ReactNode {
  return disabled ? text : <span style={{ color: RED }}>{text}</span>;
}

/** The label a download button carries right now — its own word unless its run
 *  is speaking; *disabled* is that button's own state. */
function downloadLabel(part: BiosPart, id: string, label: string, size: number, disabled: boolean): ReactNode {
  const { busyFolder, platform, actions } = part.input;
  const showing = biosDownloadButtonState(busyFolder, platform.folder, actions, id);
  if (showing === "spinner") return <Throbber size={`${size}px`} />;
  return showing === "failed" ? redWhileEnabled("Failed", disabled) : label;
}

/**
 * A BIOS row's one action: fetch it, replace what lies at its place, or remove
 * the copy Tender holds — or nothing, which is a `null` the row needs in order
 * to make itself a focus stop.
 */
function rowAction(part: BiosPart, file: BiosTableRow, fetchable: Set<string>, key: string): ReactNode | null {
  const { platform, busyFolder, serverOffline, actions, answer } = part.input;
  const { focus } = part;
  const busy = busyFolder !== null;
  const disabled = busy && focus.pressed !== key;
  const offer = biosRowAction(file, fetchable.has(file.file_name), serverOffline);
  if (offer === null) return null;
  if (offer.kind === "download") {
    return (
      <DialogButton
        style={ROW_BUTTON}
        disabled={disabled}
        onClick={() => {
          if (busy) return;
          focus.mark(key);
          actions.downloadOne(platform, offer.name, offer.firmwareId);
        }}
      >
        {downloadLabel(part, offer.name, "Download", 11, disabled)}
      </DialogButton>
    );
  }
  if (offer.kind === "replace") {
    const copy = replaceBiosConfirmCopy(offer.fileName, offer.place, answer.backup_root);
    const confirmReplace = () => {
      if (busy) return;
      focus.hold(key);
      showModal(
        <ConfirmModal
          strTitle={copy.title}
          strDescription={copy.description}
          strOKButtonText={copy.confirmLabel}
          strCancelButtonText="Cancel"
          onOK={() => {
            focus.mark(key);
            actions.replaceBios(platform, offer.place);
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
  const copy = deleteBiosFileConfirmCopy(offer.name);
  const confirm = () => {
    if (busy) return;
    focus.hold(key);
    showModal(
      <ConfirmModal
        strTitle={copy.title}
        strDescription={copy.description}
        strOKButtonText={copy.confirmLabel}
        strCancelButtonText="Cancel"
        onOK={() => {
          focus.mark(key);
          actions.deleteBiosFile(platform, offer.name);
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

export interface BiosPartInput {
  /** The platform the files are for, and what its page calls it. */
  platform: PlatformKey;
  platformName: string;
  answer: BiosAnswerFields;
  /** The page's busy key: every press waits while any action of the page runs. */
  busyFolder: string | null;
  /** RomM is unreachable: the downloads are withdrawn, the deletes stand. */
  serverOffline: boolean;
  actions: BiosActions;
  /** What "Download required" asks for on the launching emulator's view: the
   *  platform's own pick (`"pick"`, where the page is the platform's) or the
   *  shown entry's emulator (`"shown"`, where a game may launch with another). */
  launching: "pick" | "shown";
  stop: BiosRowStop;
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
 * rows, the library's own rows and focus.
 */
export function useBiosFiles(input: BiosPartInput): BiosPart {
  const [tabKey, setTabKey] = useState<string | null>(null);
  const files = input.answer.files ?? [];
  const tabs = input.answer.emulator_tabs;
  const tab = selectedTab(tabs, tabKey);
  const shownRows = tabRows(files, tabs, tab);
  const focus = useBiosFocus(
    input.busyFolder !== null,
    shownRows.map((file) => tableKey(file.file_name)),
  );
  const libraryRows = files.filter(isUnclaimedLibraryRow);
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
            action={rowAction(part, file, fetchable, tableKey(file.file_name))}
            emulator={emulator}
            placeLines={biosPlaceLines(
              file,
              emulator,
              input.actions.attemptFor(input.platform.folder, file.local_path, downloadName(file)),
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
 */
export const BiosButtons: FC<{ part: BiosPart; status: ScopedStatus | null }> = ({ part, status }) => {
  const { input, tab, shownRows, focus } = part;
  const { platform, platformName, answer, busyFolder, serverOffline, actions } = input;
  const busy = busyFolder !== null;
  const offer = biosBulkOffer(answer, tab, shownRows, serverOffline, input.launching);
  const copy = deletePlatformBiosConfirmCopy(platformName);

  const confirmDeleteBios = () =>
    showModal(
      <ConfirmModal
        strTitle={copy.title}
        strDescription={copy.description}
        strOKButtonText={copy.confirmLabel}
        strCancelButtonText="Cancel"
        onOK={() => {
          focus.mark(BULK_DELETE);
          actions.deleteBios(platform);
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
      {offer.requiredLabel !== null && (
        <ButtonRow padding="2px 16px 6px">
          <div style={{ display: "contents" }} ref={focus.slot(BULK_REQUIRED)}>
            <DialogButton
              style={BULK_BUTTON}
              disabled={bulkDisabled(BULK_REQUIRED, offer.canDownloadRequired)}
              onClick={pressBulk(BULK_REQUIRED, () => actions.downloadRequired(platform, offer.shownEmulator))}
            >
              {downloadLabel(
                part,
                "required",
                `${offer.requiredLabel} (${offer.requiredMissing})`,
                12,
                bulkDisabled(BULK_REQUIRED, offer.canDownloadRequired),
              )}
            </DialogButton>
          </div>
        </ButtonRow>
      )}
      <SubHeading title={`For all of ${platformName}`} />
      <ButtonRow padding="2px 16px 6px">
        <div style={{ display: "contents" }} ref={focus.slot(BULK_ALL)}>
          <DialogButton
            style={BULK_BUTTON}
            disabled={bulkDisabled(BULK_ALL, offer.canDownloadAll)}
            onClick={pressBulk(BULK_ALL, () => actions.downloadAll(platform))}
          >
            {downloadLabel(part, "all", "Download all", 12, bulkDisabled(BULK_ALL, offer.canDownloadAll))}
          </DialogButton>
        </div>
        <div style={{ display: "contents" }} ref={focus.slot(BULK_DELETE)}>
          <DialogButton
            style={BULK_BUTTON}
            disabled={bulkDisabled(BULK_DELETE, offer.deletable > 0)}
            onClick={() => {
              if (busy) return;
              focus.hold(BULK_DELETE);
              confirmDeleteBios();
            }}
          >
            {redWhileEnabled(`Delete downloads (${offer.deletable})`, bulkDisabled(BULK_DELETE, offer.deletable > 0))}
          </DialogButton>
        </div>
      </ButtonRow>
      <GroupStatus status={status} forKey={platform.folder} scope="bios" />
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
              ...(input.actions.attemptFor(input.platform.folder, null, downloadName(file)).damaged
                ? [DOWNLOAD_DAMAGED]
                : []),
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
