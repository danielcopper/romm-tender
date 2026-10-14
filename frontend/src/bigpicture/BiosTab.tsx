/**
 * BiosTab — the BIOS & Emulator pane of the RomM game detail panel.
 *
 * Render only. Everything it shows arrives as props, because the requirement
 * (`biosStatus`) and the emulator it is shown against (`coreInfo`) have to reach
 * a render TOGETHER. On a core change the panel reads both in one `Promise.all`
 * and folds them into one update; split across two owners the change would land
 * as two renders, and in between the pane highlights the previous core's line
 * and names it in the Emulator column against a requirement set that has
 * already moved. The per-file LABELS are not part of that window —
 * `coreInfo.emulators` is the platform's core list, which a core switch does not
 * change.
 *
 * The readiness line is the launching emulator's, and so is everything that acts:
 * a file another emulator demands is not a missing prerequisite for this launch.
 * Under it, one tab per emulator the resolver answered for (#2190), the
 * launching one first and selected: each tab lists its emulator's own
 * requirements, narrowed to what this page can say or do something about
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
 * CSS classes prefixed with `romm-panel-` are injected separately by
 * styleInjector.
 */

import { FC, useState, type ReactElement } from "react";
import type { BiosFileStatus, BiosLevel, BiosStatus, CoreInfo, EmulatorTab, FirmwareWanted } from "../types";
import { BIOS_MISSING_RED, biosColorForLevel } from "../utils/biosColor";
import { isFetchable } from "../utils/biosFetchable";
import { biosFileDescription, biosFileNote } from "../utils/biosFileNote";
import { oneOfRowLine, type GroupLineTone } from "../utils/biosGroup";
import { biosHeldRatio } from "../utils/biosHeldRatio";
import { biosSummary, groupBlocks } from "../utils/biosSummary";
import { emulatorClause } from "../utils/emulatorClause";
import { selectedTab, tabLabel, tabRows } from "../utils/biosTabs";
import { emulatorDataReasonSentence } from "../utils/emulatorSourceWording";
import { EmulatorTabs } from "./EmulatorTabs";
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
}

/**
 * The header line: status dot plus the sentence for this platform's BIOS state.
 *
 * **The sentence is not this surface's to word** — `utils/biosSummary.ts` holds
 * all seven states and both surfaces read it, because before that each wrote its
 * own spelling of the same seven and nothing joined them. What stays here is the
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
  const blocks = groupBlocks(bios);
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
 * Is this row's requirement met? `null` where nothing established it.
 *
 * The row's VERDICT — the resolver's per-file answer — not
 * `downloaded`: a wrong image is there and not met, and a declared folder is
 * there on every RetroDECK install whatever it holds. A payload with no verdict
 * at all falls back to `downloaded`.
 *
 * The dot beside a row and the decision to show that row at all read this one
 * function, so a reader cannot be shown a row whose dot answers a different
 * question from the one that kept it.
 */
function rowVerdict(file: BiosFileStatus): boolean | null {
  return file.satisfied === undefined ? file.downloaded : file.satisfied;
}

/**
 * The dot beside one file row: what it means for THIS launch.
 *
 * A missing option of the launching emulator's group is red where a region it
 * serves is one the group's verdict names as missing — on the game page that is
 * the verdict for this game's own regions, so the image of another region stays
 * grey there.
 */
function fileDotColor(file: BiosFileStatus, missingRegions: ReadonlySet<string>): string {
  const verdict = rowVerdict(file);
  // Grey, the colour of "not established": amber means only a one-of
  // group whose images serve some regions.
  if (verdict === null) return "#8f98a0";
  if (verdict) return "#5ba32b";
  if (file.required_by_active) return "#d94126";
  if (file.one_of?.regions.some((region) => missingRegions.has(region))) return "#d94126";
  // Not met and not required here — missing, wrong or refused alike. What
  // another emulator demands is on that emulator's own tab.
  return "#8f98a0";
}

/**
 * The lines under a file's name — the region it serves in a one-of group, then
 * what its read found.
 *
 * One indented block, because they are one column to the eye. The row's own
 * name stays short so the status dot beside it keeps its line: folding a
 * folder's three image lines into that name is what wrapped the row and
 * orphaned the dot above it.
 *
 * **The packager's label is not among them** — it sits on the name line, where
 * it says what the file IS beside the file's own name. Under the row it read as
 * one more entry in the list of emulators that want the file, which is the one
 * thing it is not.
 *
 * Which emulators want the file is no longer listed here: every emulator has a
 * tab of its own over the list (#2190), so a row is only ever its tab's
 * emulator's requirement.
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

/**
 * Which of the files an emulator asks for this pane puts a row on.
 *
 * The platform page is the management surface and lists every one of them. This
 * is a game's page, and a row earns its place here by saying something about
 * THIS launch or by being something the reader can act on. A Dreamcast page
 * listed eight rows, six of them arcade BIOSes Flycast declares because it also
 * emulates Naomi and AtomisWave — not required, not present, not in the library
 * — and they pushed the two rows that mattered off the top.
 *
 * Five answers keep a row, and the first two are this launch's requirement in
 * its two shapes:
 *
 * - **required for this launch** (`required_by_active`).
 * - **an option of the launching emulator's one-of group** (`one_of`) — one file
 *   of several, each of which meets the group for the regions it serves.
 *   `required_by_active` is false on every such row by construction, because the
 *   group is the requirement and no one option is; dropping these rows leaves
 *   the header stating the group's verdict over a list with none of its files.
 * - **present** — the verdict is met, so the row is the evidence for it.
 * - **fetchable** — `isFetchable`, the same predicate the platform page's
 *   download buttons are built from: a row this page treats as actionable and
 *   that page offers no button for is a dead end pointed at.
 * - **withheld** — nothing could judge the row (`satisfied === null`). An
 *   ignorance is something to say, never something to summarise away.
 *
 * What is left over is declared, not required for this launch, demonstrably
 * absent, and fetchable from nowhere — nothing a reader of THIS page could do
 * anything with. It is counted on one line instead, which names where the rows
 * are.
 *
 * Every tab asks it of its own emulator (#2190): another emulator's rows carry
 * that emulator's `required_by_active` and `one_of`, so "this launch" there
 * reads as a launch with the tab's emulator.
 */
function rowBelongsOnThisPage(file: BiosFileStatus): boolean {
  if (file.required_by_active || file.one_of) return true;
  // Present and withheld in one test: `false` is the only verdict that leaves a
  // row with nothing to say here.
  if (rowVerdict(file) !== false) return true;
  return isFetchable(file);
}

/**
 * One row per firmware file the selected tab's emulator asks for, plus notes for
 * the rest.
 *
 * Each tab shows its emulator's own requirements as the resolver answers for it
 * (#2190) — nothing folded in from another emulator, whose files are on its own
 * tab — narrowed to those {@link rowBelongsOnThisPage} keeps. What the narrowing
 * leaves out is counted first among the notes below, naming the tab's emulator
 * and the page that lists every file. On the launching tab the further notes
 * keep the library's remaining answers apart (#1762): files nothing asks for are
 * a finished answer, files nothing could be asked about are not. On another
 * emulator's tab the rows are muted: a reading of that emulator's answer, never
 * this launch's.
 */
function buildBiosFileList(bios: BiosStatus, tab: EmulatorTab | null, launchingLabel: string | null): ReactElement[] {
  const files = bios.files ?? [];
  const otherTab = tab !== null && !tab.launching;
  const missingRegions = new Set((bios.one_of_groups ?? []).flatMap((group) => group.missing));
  const tabFiles = tabRows(files, bios.emulator_tabs, tab) as readonly BiosFileStatus[];
  const wantedFiles = otherTab ? tabFiles : tabFiles.filter((f) => f.wanted === "needed" || f.wanted === "optional");
  const shownFiles = wantedFiles.filter(rowBelongsOnThisPage);
  const countOf = (wanted: FirmwareWanted) =>
    files.filter((f) => f.wanted === wanted && (f.declared_by === undefined || f.declared_by === null)).length;

  const fileElements = shownFiles.map((f) => {
    const { note, lines } = biosFileNote(f);
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
    // A file that is an option of the emulator's one-of group says which discs
    // it serves, where the per-emulator lines used to.
    const groupLine = f.one_of && !f.required_by_active ? [oneOfRowLine(f.one_of, rowVerdict(f))] : [];
    const dot = otherTab ? "#8f98a0" : fileDotColor(f, missingRegions);

    return (
      <div key={f.file_name} className="romm-panel-file-row" style={otherTab ? { opacity: 0.6 } : undefined}>
        <span key="dot" className="romm-status-dot" style={{ backgroundColor: dot }} />
        <span key="name" className="romm-panel-file-name">
          {f.declared_path || f.file_name}
          {/* Muted like the lines below rather than like the name beside it:
              the name is what the eye lands on and the label is beside it, not
              part of it. */}
          {label !== null && <span key="label" style={{ color: "rgba(255, 255, 255, 0.5)" }}>{` ${label}`}</span>}
          {suffix}
        </span>
        {fileLines([...groupLine, ...lines])}
      </div>
    );
  });

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
  // emulator named is the tab's, because the rows counted are its own — and on
  // another emulator's tab the need is that emulator's, which launches nothing
  // here (#2190 D16).
  const elsewhere = wantedFiles.length - shownFiles.length;
  if (elsewhere > 0) {
    const name = tab !== null ? tabLabel(tab) : (launchingLabel ?? "the emulator");
    const notRequired = otherTab ? `not required by ${name}` : "not required for this launch";
    fileElements.push(
      summaryLine(
        "elsewhere-note",
        `${elsewhere} more file${elsewhere === 1 ? "" : "s"} ${name} lists — ${notRequired}, ` +
          "nothing to download. All files are on the platform page.",
      ),
    );
  }

  if (otherTab) return fileElements;

  for (const [wanted, phrase] of [
    ["not_needed", "no installed emulator asks for"],
    ["unknown", "nothing installed could answer for"],
  ] as const) {
    const count = countOf(wanted);
    if (count === 0) continue;
    fileElements.push(summaryLine(`${wanted}-note`, `${count} file${count === 1 ? "" : "s"} on server ${phrase}`));
  }

  return fileElements;
}

export const BiosTab: FC<BiosTabProps> = ({ biosStatus, biosLevel, coreInfo, isActive, platformName }) => {
  // Which emulator's tab the list shows: the launching one until another is
  // chosen — a view, never a change of what launches (#2190).
  const [tabKey, setTabKey] = useState<string | null>(null);
  if (!isActive || !biosStatus) return null;

  // Left column: BIOS status, the launching emulator's group. The header is the
  // launching emulator's whichever tab the list below shows.
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
  const fileElements = buildBiosFileList(
    biosStatus,
    tab,
    biosStatus.active_core_label ?? coreInfo?.active_core_label ?? null,
  );

  // The tab strip and the list sit below the header, outside its section: a
  // section is itself a focusable button, and a tab inside it would be a button
  // inside a button. Each tab is a stop of its own, above the list it switches.
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
      {tabs && tab && tabs.length > 1 && (
        <EmulatorTabs
          tabs={tabs}
          selected={tab}
          onSelect={(chosen) => setTabKey(chosen.launching ? null : chosen.emulator)}
        />
      )}
      {fileElements.length > 0 &&
        section(
          "bios-files",
          null,
          <div key="bios-file-list" className="romm-panel-file-list">
            {fileElements}
          </div>,
        )}
    </>
  );
};
