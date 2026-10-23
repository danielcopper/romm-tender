/**
 * BiosTab — the BIOS & Emulator pane of the RomM game detail panel.
 *
 * Render only. Everything it shows arrives as props, because the requirement
 * (`biosStatus`) and the emulator it is shown against (`coreInfo`) have to reach
 * a render TOGETHER. On a core change the panel reads both in one `Promise.all`
 * and folds them into one update; split across two owners the change would land
 * as two renders, and in between the pane names the previous emulator in the
 * Emulator column against a requirement set that has already moved.
 *
 * The readiness line is the launching emulator's, and so is everything that acts:
 * a file another emulator demands is not a missing prerequisite for this launch.
 * Under it, a selector over every emulator the resolver answered for,
 * the launching one first and selected: the list shows the chosen emulator's
 * own requirements, narrowed to what this page can say or do something about
 * (`rowBelongsOnThisPage`), and choosing one changes the list and nothing else.
 * What the narrowing leaves out is counted on a line that names the page listing
 * it, so a row is never dropped silently; the page that leaves nothing out is the
 * platform page, which is the management surface.
 *
 * A row can also be a file an emulator wants that the RomM library does not hold
 * (`on_server` false). No page in Tender can fetch it, so it says so rather
 * than looking like a download nobody has started. Not holding it is a separate
 * question from not having it: RetroDECK ships `dolphin-emu/Sys/codehandler.bin`
 * into the BIOS directory, so that row is unfetchable and satisfied at once — and
 * where the reading establishes whose copy is
 * there, it reads as the distribution's own file rather than as a gap in a
 * library that will never hold it (`utils/biosFileNote`).
 *
 * The one press it holds is "Replace…" on a row the backend offers it on
 * (`replace_offered`): the same confirmation as on the platform page, then
 * `onReplace`, which the panel answers.
 *
 * CSS classes prefixed with `romm-panel-` are injected separately by
 * styleInjector.
 */

import { FC, useEffect, useRef, useState, type ReactElement } from "react";
import { ConfirmModal, DialogButton, showModal } from "@decky/ui";
import type { BiosFileStatus, BiosLevel, BiosStatus, CoreInfo, EmulatorTab } from "../types";
import { BIOS_MISSING_RED, biosColorForLevel } from "../utils/biosColor";
import { biosFileDescription, biosFileNote } from "../utils/biosFileNote";
import { fileDotColor, rowBelongsOnThisPage } from "../utils/biosGamePageRows";
import { needWords, type GroupLineTone } from "../utils/biosGroup";
import { biosHeldRatio } from "../utils/biosHeldRatio";
import {
  backupLines,
  biosPlaceLines,
  libraryRowLines,
  storeOnlySentence,
  type Identification,
  type RowAttempt,
} from "../utils/biosPlaceWords";
import { useBiosPresses } from "../utils/biosAttempts";
import { biosSummary, groupBlocks } from "../utils/biosSummary";
import { emulatorClause } from "../utils/emulatorClause";
import { isUnclaimedLibraryRow, selectedTab, tabLabel, tabRows } from "../utils/biosTabs";
import { emulatorDataReasonSentence } from "../utils/emulatorSourceWording";
import { ENTRY_FOCUS_DELAY_MS, firstBodyStop, placeEntryFocus } from "../utils/entryFocus";
import { useIdentifications } from "../utils/firmwareIdentifications";
import { detach } from "../utils/detach";
import { scrollFocusedToCenter } from "../utils/scrollHelpers";
import { EmulatorSelector, SHOWN_ENTRY_ATTR } from "./EmulatorSelector";
import { section } from "./panelSection";

interface BiosTabProps {
  /** The platform's BIOS requirement, or null when its core needs none — in
   *  which case there is no tab to render. A platform whose requirement could
   *  not be established passes a status with no files rather than null: not
   *  knowing is something to say, and no tab would have said the opposite. */
  biosStatus: BiosStatus | null;
  /** Backend-computed readiness verdict driving the status-dot color;
   *  null whenever there is no requirement. */
  biosLevel: BiosLevel | null;
  /** The launching emulator as the Emulator column names it, plus the
   *  platform's emulator list. From the dedicated `get_platform_core_info` path,
   *  never derived from `biosStatus`. */
  coreInfo: CoreInfo | null;
  isActive: boolean;
  /** RomM's name for the game's platform, which the console's own demand is
   *  said of. Empty where none is held, and the sentence names the system
   *  without it. */
  platformName?: string;
  /** "Replace…" on the row whose place is the argument, once the user
   *  confirmed it; true once the file was replaced. Without it no row offers
   *  the press. */
  onReplace?: (place: string) => Promise<boolean>;
}

/**
 * The header line: status dot plus the sentence for this platform's BIOS state.
 *
 * **The sentence is not this surface's to word** — `utils/biosSummary.ts` holds
 * every state and both surfaces read it, because before that each wrote its
 * own spelling of the same states and nothing joined them. What stays here is the
 * choice of WHICH of the two answers this page has room for (the sentence, since
 * a game page has no adjacent heading to hang a short note on) and what is
 * appended to it.
 *
 * What is appended is the library's ratio, and it counts a different set from
 * anything the sentence counts — which is why it rides along rather than being
 * folded in, and why it is written by `utils/biosHeldRatio.ts` rather than here.
 * The platform pane appends the same tail to the same sentences, so a second
 * spelling of it on this surface is what would let one platform be described in
 * two amounts.
 *
 * The dot's colour is the backend's verdict through the shared helper and is
 * never re-derived here.
 *
 * Where the answering source gives no emulator list, the line above the state
 * says why, in the sentence the platform pane shows for the same answer.
 */
function buildBiosHeader(
  bios: BiosStatus,
  biosLevel: BiosTabProps["biosLevel"],
  coreInfo: CoreInfo | null,
  platformName: string | null,
): ReactElement[] {
  const heldRatio = biosHeldRatio(bios);
  // Why a download for this game lands in Tender's firmware store and is linked nowhere.
  const storeOnly = storeOnlySentence(
    bios.store_only,
    bios.active_core_label ?? coreInfo?.active_core_label ?? null,
    platformName ?? "this platform",
  );

  const biosColor = biosColorForLevel(biosLevel);
  const biosLabel = `${biosSummary(bios, bios.files ?? [], biosLevel, platformName).sentence}${heldRatio}`;
  const listReason =
    coreInfo !== null && !coreInfo.emulator_data_available
      ? emulatorDataReasonSentence(coreInfo.emulator_data_reason, coreInfo.emulator_source, coreInfo.platform_system)
      : null;

  return [
    <div key="bios-title" className="romm-panel-section-title" style={{ marginBottom: "8px" }}>
      BIOS
    </div>,
    ...(listReason === null
      ? []
      : [
          <div
            key="bios-reason"
            data-testid="bios-reason"
            style={{ color: "rgba(255, 255, 255, 0.5)", fontSize: "12px", marginBottom: "4px" }}
          >
            {listReason}
          </div>,
        ]),
    <div key="bios-row" className="romm-panel-status-inline">
      <span className="romm-status-dot" style={{ backgroundColor: biosColor }} />
      <span className="romm-panel-value">{biosLabel}</span>
    </div>,
    ...(storeOnly === null
      ? []
      : [
          <div
            key="bios-store-only"
            data-testid="bios-store-only"
            style={{ color: "rgba(255, 255, 255, 0.5)", fontSize: "12px", marginTop: "4px" }}
          >
            {storeOnly}
          </div>,
        ]),
  ];
}

/** The dot beside one line of the group block — the same colours a file row's dot uses. */
const GROUP_TONE_COLOR: Readonly<Record<GroupLineTone, string>> = {
  here: "#5ba32b",
  missing: "#d94126",
  other: "#8f98a0",
  // Not established is grey, never amber.
  unchecked: "#8f98a0",
};

// A subheading under the BIOS column: the section label's own class, which
// upper-cases it, with room above to set it off from what precedes it.
const SUBHEADING_STYLE = { marginTop: "12px", marginBottom: "4px" };

/**
 * The launching emulator's one-of groups, between the headline and the file
 * list: which image each disc region needs and whether it is there, with the
 * game's own region marked. Each group stands under a subheading of its own,
 * its lines indented beneath it. The file list below stays whole — the block is
 * a reading of some of its rows, not a replacement for them. What it says is
 * `utils/biosGroup.ts`'s and `utils/biosSummary.ts`'s; this lays it out.
 */
function buildGroupBlock(bios: BiosStatus): ReactElement | null {
  const blocks = groupBlocks(bios, bios.files ?? []);
  if (blocks.length === 0) return null;
  return (
    <div
      key="bios-group"
      data-testid="bios-group"
      className="romm-panel-group"
      style={{ display: "flex", flexDirection: "column", gap: "2px" }}
    >
      {blocks.map((block) => {
        return (
          <div key={[block.heading, ...block.lines.map((line) => line.text)].join("\n")}>
            <div className="romm-panel-section-title" style={SUBHEADING_STYLE}>
              {block.heading}
            </div>
            {block.lines.map((line) => (
              <div key={line.text} className="romm-panel-group-line romm-panel-file-row" style={{ paddingLeft: "8px" }}>
                <span className="romm-status-dot" style={{ backgroundColor: GROUP_TONE_COLOR[line.tone] }} />
                <span className="romm-panel-file-name">{line.text}</span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

/**
 * How wide the emulator selector stands on a game page, centred: about the
 * platform page's, whose detail column is what is left of the wide panel's
 * 806px beside its 264px list (`layout/ListDetail.tsx`). Across the whole game
 * page its arrows sit far apart from the name and the carousel between them.
 */
const GAME_PAGE_SELECTOR_WIDTH = "500px";

/** The need word beside a file's name: muted, and set apart from the name and
 *  its label by the "·" the panel separates such parts with. */
const NEED_WORD = { marginLeft: "4px", color: "rgba(255, 255, 255, 0.5)", fontSize: "12px" } as const;

/**
 * The lines under a file's name — what its read found. What the shown emulator
 * needs of it stands on the name's own line ({@link NEED_WORD}).
 *
 * One indented block, because they are one column to the eye. The row's own
 * name stays short so the status dot beside it keeps its line: folding a
 * folder's three image lines into that name is what wrapped the row and
 * orphaned the dot above it.
 *
 * **The packager's label is not among them** — it sits on the name line, where
 * it says what the file IS beside the file's own name. Under the row it would
 * read as one more line about how the file stands, which is the one thing it is
 * not.
 *
 * A row is only the shown emulator's requirement; another emulator's rows are
 * shown when it is chosen.
 *
 * The image text is the resolver's verbatim string, and `pre-wrap` keeps the
 * column padding PCSX2 puts in its own option labels — that alignment is what
 * makes a line matchable against the emulator's picker, and it still wraps
 * rather than overflowing the panel.
 */
function fileLines(lines: string[]): ReactElement | null {
  if (lines.length === 0) return null;
  return (
    <div
      key="lines"
      style={{
        flexBasis: "100%",
        display: "flex",
        flexDirection: "column" as const,
        gap: "2px",
        marginLeft: "18px",
      }}
    >
      {lines.map((line) => (
        <div
          key={`image-${line}`}
          style={{ color: "rgba(255, 255, 255, 0.5)", fontSize: "12px", whiteSpace: "pre-wrap" }}
        >
          {line}
        </div>
      ))}
    </div>
  );
}

/** Every focus stop of the file list carries it, so the list is found as one whatever its stops. */
const LIST_PART_CLASS = "romm-panel-bios-list";

/**
 * One focus stop of the file list: a run of rows, or one row that offers
 * "Replace…" and is pressed as a whole — a stop is a button, and a button
 * inside it would be a button inside a button.
 */
type BiosListPart =
  | { kind: "rows"; key: string; elements: ReactElement[] }
  | { kind: "replace"; key: string; file: BiosFileStatus; place: string; element: ReactElement };

/**
 * One row per firmware file the shown emulator asks for, a note for the rest,
 * and the library's files no emulator here declares in a section of their own.
 *
 * Each emulator's view shows its own requirements as the resolver answers for
 * it — nothing folded in from another emulator — narrowed to those
 * {@link rowBelongsOnThisPage} keeps. What the narrowing leaves out is counted on
 * one note naming the shown emulator and the page that lists every file.
 * Another emulator's rows are not muted; the selector says which
 * emulator is shown. The library section is the same whichever emulator is
 * shown, and each of its rows says, as its need, whether nothing asks for the
 * file or nothing could be asked.
 */
function buildBiosFileList(
  bios: BiosStatus,
  tab: EmulatorTab | null,
  launchingLabel: string | null,
  identifications: Readonly<Record<string, Identification>>,
  attemptFor: (place: string | null, fileName: string | null) => RowAttempt,
  replaceable: boolean,
): BiosListPart[] {
  const files = bios.files ?? [];
  const otherTab = tab !== null && !tab.launching;
  const missingRegions = new Set(otherTab ? [] : (bios.one_of_groups ?? []).flatMap((group) => group.missing));
  const tabFiles = tabRows(files, bios.emulator_tabs, tab) as readonly BiosFileStatus[];
  const wantedFiles = otherTab ? tabFiles : tabFiles.filter((f) => f.wanted === "needed" || f.wanted === "optional");
  const shownFiles = wantedFiles.filter(rowBelongsOnThisPage);
  // The emulator the rows are its own requirements of: the shown one.
  const emulatorName = tab !== null ? tabLabel(tab) : launchingLabel;
  const libraryFiles = files.filter(isUnclaimedLibraryRow);

  const fileRow = (f: BiosFileStatus, named: string | null, library = false, pressLabel: string | null = null) => {
    const words = biosFileNote(f, named);
    const note = words.note;
    // What Tender's own part in the row is: its store copy, the link at its
    // place — or, in the library's section, what the file's content is.
    // And what the latest press on this page did to it (D30): said here,
    // never in a toast.
    const attempt = attemptFor(f.local_path, f.store_file ?? f.library?.file_name ?? null);
    const lines = [
      ...words.lines,
      ...(library
        ? [
            ...libraryRowLines(f, f.library ? identifications[f.library.md5] : undefined),
            ...backupLines(attempt.movedAside ? [attempt.movedAside] : []),
          ]
        : biosPlaceLines(f, named, attempt)),
    ];
    // The row is headed by the file the emulator DECLARED — `declared_path`, not
    // `file_name`, which is only its basename: a `dc/dc_boot.bin` row would
    // otherwise head itself `dc_boot.bin` and a declared folder `bios`, taking
    // away the one thing a reader placing a file by hand needs. The head is never
    // `description`: that is the packager's prose out of a core's `.info`,
    // deliberately outside the resolver's contract. What the description still
    // ADDS follows the name on the same line, then how the file STANDS behind a
    // dash.
    const label = biosFileDescription(f);
    const suffix = note ? ` — ${note}` : "";
    const dot = fileDotColor(f, missingRegions);

    return (
      <div key={f.file_name} className="romm-panel-file-row" data-bios-place={f.local_path}>
        <span key="dot" className="romm-status-dot" style={{ backgroundColor: dot }} />
        <span key="name" className="romm-panel-file-name">
          {f.declared_path || f.file_name}
          {/* Muted like the lines below rather than like the name beside it:
              the name is what the eye lands on and the label is beside it, not
              part of it. */}
          {label !== null && <span key="label" style={{ color: "rgba(255, 255, 255, 0.5)" }}>{` ${label}`}</span>}
          {suffix}
        </span>
        {/* What the shown emulator needs of the file, on the name's own line
            with a little room before it, so a row with nothing more to say is
            one line tall. */}
        <span key="need" data-testid="bios-need" style={NEED_WORD}>
          {`· ${needWords(f)}`}
        </span>
        {pressLabel !== null && (
          <span key="press" data-testid="bios-press" style={PRESS_LABEL}>
            {pressLabel}
          </span>
        )}
        {fileLines(lines)}
      </div>
    );
  };
  const parts: BiosListPart[] = [];
  // The run of rows the next row joins: the last part, unless that is a row
  // pressed on its own.
  const rows = (): ReactElement[] => {
    // `slice` rather than `.at(-1)`: the panel compiles against ES2020, which has no `Array.prototype.at`.
    const [last] = parts.slice(-1);
    if (last?.kind === "rows") return last.elements;
    const elements: ReactElement[] = [];
    parts.push({ kind: "rows", key: `rows-${parts.length}`, elements });
    return elements;
  };
  for (const f of shownFiles) {
    if (replaceable && f.replace_offered && f.local_path) {
      const element = fileRow(f, emulatorName, false, "Replace…");
      parts.push({ kind: "replace", key: `replace-${f.local_path}`, file: f, place: f.local_path, element });
    } else {
      rows().push(fileRow(f, emulatorName));
    }
  }

  const summaryLine = (key: string, text: string) => (
    <div
      key={key}
      className="romm-panel-file-row"
      style={{ color: "rgba(255, 255, 255, 0.4)", fontSize: "12px", marginTop: "8px" }}
    >
      {text}
    </div>
  );

  // Three facts, and the third is what keeps this from being a way to hide rows:
  // how many, that this launch needs none of them, and WHERE every file is — the
  // platform page. Both claims hold of every row counted rather than of most of
  // them: "required" covers both spellings of the launch's requirement, and "to
  // download" is `isFetchable`, so a row that fails neither is kept above. The
  // emulator named is the shown one, because the rows counted are its own — and
  // with another emulator shown the need is that emulator's, which launches
  // nothing here.
  const elsewhere = wantedFiles.length - shownFiles.length;
  if (elsewhere > 0) {
    const name = emulatorName ?? "the emulator";
    const notRequired = otherTab ? `not required by ${name}` : "not required for this launch";
    rows().push(
      summaryLine(
        "elsewhere-note",
        `${elsewhere} more file${elsewhere === 1 ? "" : "s"} ${name} lists — ${notRequired}, ` +
          "nothing to download. All files are on the platform page.",
      ),
    );
  }

  if (libraryFiles.length > 0) {
    rows().push(
      <div key="library-heading" className="romm-panel-section-title" style={SUBHEADING_STYLE}>
        In your RomM library, asked for by no emulator here
      </div>,
      ...libraryFiles.map((f) => fileRow(f, launchingLabel, true)),
    );
  }

  return parts;
}

/** The press a row pressed as a whole offers, at the end of its name line. */
const PRESS_LABEL = {
  marginLeft: "auto",
  padding: "1px 10px",
  borderRadius: "2px",
  background: "rgba(255, 255, 255, 0.1)",
  color: "#dcdedf",
  fontSize: "12px",
} as const;

/**
 * One focus stop of the file list, styled as a section rather than a button.
 * The stops are spaced like the rows inside one, so the list reads as one.
 */
function listPart(part: BiosListPart, index: number, count: number, onPress: (() => void) | null): ReactElement {
  const first = index === 0;
  const last = index === count - 1;
  return (
    <DialogButton
      key={part.key}
      className={`romm-panel-section ${LIST_PART_CLASS}`}
      style={{
        background: "transparent",
        border: "none",
        padding: `${first ? 12 : 2}px 0 ${last ? 12 : 0}px`,
        textAlign: "left" as const,
        width: "100%",
        cursor: "default",
        display: "block",
      }}
      noFocusRing={false}
      onFocus={scrollFocusedToCenter}
      {...(onPress === null ? {} : { onClick: onPress })}
    >
      <div className="romm-panel-file-list" style={{ margin: `${first ? 8 : 0}px 0 ${last ? 8 : 0}px` }}>
        {part.kind === "rows" ? part.elements : part.element}
      </div>
    </DialogButton>
  );
}

/** The list's stop that holds the row whose place is *place*, or null. */
function stopHolding(root: ParentNode, place: string): HTMLElement | null {
  const row = [...root.querySelectorAll<HTMLElement>("[data-bios-place]")].find((el) => el.dataset.biosPlace === place);
  return row?.closest<HTMLElement>(`.${LIST_PART_CLASS}`) ?? null;
}

export const BiosTab: FC<BiosTabProps> = ({ biosStatus, biosLevel, coreInfo, isActive, platformName, onReplace }) => {
  // Which emulator the list shows: the launching one until another is chosen —
  // a view, never a change of what launches.
  const [tabKey, setTabKey] = useState<string | null>(null);
  const part = useRef<HTMLDivElement>(null);
  const [steppedFromList, setSteppedFromList] = useState(false);
  // The place a replacement finished at, until focus has followed its row.
  const [replacedPlace, setReplacedPlace] = useState<string | null>(null);
  const [replacing, setReplacing] = useState(false);
  const attemptFor = useBiosPresses();
  // What the resolver knows each library file's content as, asked after the
  // page is shown, one file at a time.
  const identifications = useIdentifications(
    (biosStatus?.files ?? []).flatMap((file) =>
      isUnclaimedLibraryRow(file) && file.library?.md5 ? [file.library.md5] : [],
    ),
  );
  // A step that leaves the list with no row for focus to stay on puts it on the
  // carousel's shown entry rather than letting Steam reset it to the top of the
  // page. The list is one focus stop, so focus on it otherwise stays.
  useEffect(() => {
    if (!steppedFromList) return;
    const timer = setTimeout(() => {
      setSteppedFromList(false);
      const root = part.current;
      if (!root || root.querySelector(".romm-panel-file-list")) return;
      const entry = root.querySelector<HTMLElement>(`[${SHOWN_ENTRY_ATTR}]`);
      if (entry) placeEntryFocus(entry, firstBodyStop);
    }, ENTRY_FOCUS_DELAY_MS);
    return () => clearTimeout(timer);
  }, [steppedFromList]);
  // A replaced row is pressed on its own only until the re-read shows it
  // linked; then its stop is gone, and focus goes to the stop that now holds
  // the row rather than Steam resetting it to the top of the page.
  useEffect(() => {
    if (replacedPlace === null) return;
    const timer = setTimeout(() => {
      const root = part.current;
      const stop = root ? stopHolding(root, replacedPlace) : null;
      if (!stop || stop.querySelector('[data-testid="bios-press"]')) return;
      setReplacedPlace(null);
      const active = stop.ownerDocument.activeElement;
      if (active === null || active === stop.ownerDocument.body) placeEntryFocus(stop, () => stop);
    }, ENTRY_FOCUS_DELAY_MS);
    return () => clearTimeout(timer);
  }, [replacedPlace, biosStatus]);
  if (!isActive || !biosStatus) return null;

  // Left column: BIOS status, the launching emulator's group. The header is the
  // launching emulator's whichever emulator the list below shows.
  const biosColumn = buildBiosHeader(biosStatus, biosLevel, coreInfo, platformName || null);
  const groupBlockElement = buildGroupBlock(biosStatus);
  if (groupBlockElement) biosColumn.push(groupBlockElement);

  // Right column: the emulator this game launches with, under its heading and
  // unlabelled — the mirror of the BIOS column, whose sentence stands under
  // `BIOS` the same way. A label here is the road not to take twice over: the
  // heading already names the subject, so it would be a second name for the
  // same thing, and the one it carried called a standalone emulator a core.
  const clause = emulatorClause(coreInfo);
  const coreColumn = [
    <div key="core-title" className="romm-panel-section-title" style={{ marginBottom: "8px" }}>
      Emulator
    </div>,
  ];
  if (clause !== null) {
    coreColumn.push(
      <div
        key="core"
        className="romm-panel-value"
        style={clause.gamesCannotStart ? { color: BIOS_MISSING_RED } : undefined}
      >
        {clause.text}
      </div>,
    );
  }

  const tabs = biosStatus.emulator_tabs;
  const tab = selectedTab(tabs, tabKey);
  const parts = buildBiosFileList(
    biosStatus,
    tab,
    biosStatus.active_core_label ?? coreInfo?.active_core_label ?? null,
    identifications,
    attemptFor,
    onReplace !== undefined,
  );
  const backupRoot = biosStatus.backup_root ?? "the backup folder";
  const confirmReplace = (file: BiosFileStatus, place: string) => {
    if (replacing || !onReplace) return;
    showModal(
      <ConfirmModal
        strTitle={`Replace ${file.file_name}?`}
        strDescription={`The file at ${place} goes to ${backupRoot}, and Tender's copy is linked in its place.`}
        strOKButtonText="Replace"
        strCancelButtonText="Cancel"
        onOK={() => {
          setReplacing(true);
          detach(
            (async () => {
              try {
                if (await onReplace(place)) setReplacedPlace(place);
              } finally {
                setReplacing(false);
              }
            })(),
          );
        }}
      />,
    );
  };

  // The selector and the list sit below the header, outside its section: a
  // section is itself a focusable button, and the selector's own buttons inside
  // it would be buttons inside a button. The selector stands above the list it
  // switches.
  return (
    <>
      {section(
        "bios-core",
        null,
        <div key="bios-core-columns" style={{ display: "flex", gap: "24px" }}>
          <div key="bios-col" style={{ flex: 1, minWidth: 0 }}>
            {biosColumn}
          </div>
          <div key="core-col" style={{ flexShrink: 0, minWidth: "120px" }}>
            {coreColumn}
          </div>
        </div>,
      )}
      <div ref={part}>
        <EmulatorSelector
          tabs={tabs}
          shown={tab}
          maxWidth={GAME_PAGE_SELECTOR_WIDTH}
          onSelect={(chosen) => {
            const active = part.current?.ownerDocument.activeElement ?? null;
            // The list's stops are one list: a step from any of them leaves it.
            const stops = [...(part.current?.querySelectorAll(`.${LIST_PART_CLASS}`) ?? [])];
            if (stops.some((stop) => stop.contains(active))) setSteppedFromList(true);
            setTabKey(chosen.launching ? null : chosen.emulator);
          }}
        />
        {parts.map((listed, index) =>
          listPart(
            listed,
            index,
            parts.length,
            listed.kind === "replace" ? () => confirmReplace(listed.file, listed.place) : null,
          ),
        )}
      </div>
    </>
  );
};
