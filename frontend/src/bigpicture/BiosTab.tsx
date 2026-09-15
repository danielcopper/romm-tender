/**
 * BiosTab — the BIOS & Emulator pane of the RomM game detail panel.
 *
 * Render only. Everything it shows arrives as props, because the requirement
 * (`biosStatus`) and the core it is shown against (`coreInfo`) have to reach a
 * render TOGETHER. On a core change the panel reads both in one `Promise.all`
 * and folds them into one update; split across two owners the change would land
 * as two renders, and in between the pane highlights the previous core's line
 * and names it in the "Active Core" row against a requirement set that has
 * already moved. The per-file LABELS are not part of that window —
 * `coreInfo.emulators` is the platform's core list, which a core switch does not
 * change.
 *
 * Two axes are rendered side by side and must not be conflated. A file's
 * `wanted` is what the whole machine says about it; `required_by_active` is what
 * the emulator this game launches with says. The readiness line is about the
 * second — a file another emulator demands is not a missing prerequisite for
 * this launch — while the rows below it are drawn from the first, narrowed to
 * what this page can say or do something about (`rowBelongsOnThisPage`). What
 * the narrowing leaves out is counted on a line that names the page listing it,
 * so a row is never dropped silently; the page that leaves nothing out is the
 * Library page's Platforms tab, which is the management surface.
 *
 * A row can also be a file an emulator wants that the RomM library does not hold
 * (`on_server` false). No page in the plugin can fetch it, so it says so rather
 * than looking like a download nobody has started. Not holding it is a separate
 * question from not having it: RetroDECK ships `dolphin-emu/Sys/codehandler.bin`
 * into the BIOS directory, so that row is unfetchable and satisfied at once —
 * which is also what keeps it on the page, since a met verdict is one of the
 * answers a row is kept for — and where the reading establishes whose copy is
 * there, it reads as the distribution's own file rather than as a gap in a
 * library that will never hold it (`utils/biosFileNote`).
 *
 * CSS classes prefixed with `romm-panel-` are injected separately by
 * styleInjector.
 */

import { FC, type ReactElement } from "react";
import type { BiosFileStatus, BiosLevel, BiosStatus, CoreInfo, FirmwareWanted } from "../types";
import { biosColorForLevel } from "../utils/biosColor";
import { isFetchable } from "../utils/biosFetchable";
import { biosFileDescription, biosFileNote } from "../utils/biosFileNote";
import { biosHeldRatio } from "../utils/biosHeldRatio";
import { biosSummary } from "../utils/biosSummary";
import { infoRow, section } from "./panelSection";

interface BiosTabProps {
  /** The platform's BIOS requirement, or null when its core needs none — in
   *  which case there is no tab to render. A platform whose requirement could
   *  not be established passes a status with no files rather than null: not
   *  knowing is something to say, and no tab would have said the opposite. */
  biosStatus: BiosStatus | null;
  /** Backend-computed readiness verdict driving the status-dot color;
   *  null whenever there is no requirement. */
  biosLevel: BiosLevel | null;
  /** Active core + available cores, from the dedicated `get_platform_core_info`
   *  path (#923) — never derived from `biosStatus`. */
  coreInfo: CoreInfo | null;
  isActive: boolean;
}

/**
 * What one core's line says about this file — its own word, or its console's.
 *
 * `required` / `optional` is the core's `.info` and nothing else, and it is
 * never rewritten here. A libretro declaration can mark a file needed or
 * optional and can say nothing more, so an author who knows the console will not
 * start without one of the images the core lists has only those two words to
 * reach for, and the deployed catalogue goes both ways over one PlayStation:
 * SwanStation marks all five of its images optional, Beetle PSX marks three of
 * its own required.
 *
 * `needs_one_of` is the backend's answer to exactly that first shape — a console
 * that needs an image under a core that marks nothing required — and it carries
 * the count, so the line states the whole requirement in the core's own terms:
 * "needs one of its 5 BIOS files". It is the ONE line that can, because the
 * headline above says "at least one" without a number and the file rows each
 * describe one file. A core that already marks the file required is untouched:
 * its console's demand reaches the reader as that word, and annotating it as
 * well would be one requirement written twice — which is what the annotation
 * this replaced did to Beetle PSX under `ps1_rom.bin`, a file it marks optional
 * while hard-requiring three others.
 */
function coreLineSuffix(core: { required: boolean; needs_one_of?: number | null }): string {
  if (core.required) return " (required)";
  if (core.needs_one_of != null) return ` (needs one of its ${core.needs_one_of} BIOS files)`;
  return " (optional)";
}

/**
 * What to call an emulator the picker payload could not name.
 *
 * The key is the resolver's identity, which is the emulator's own spelling —
 * `swanstation_libretro.so` for a libretro core, `DUCKSTATION` for a standalone
 * one. Strip only what a reader would not have typed: the core file's extension
 * and the `_libretro` marker every libretro core carries. An identity made of
 * nothing else is printed whole, because a blank line names no emulator at all.
 */
function fallbackEmulatorLabel(emulator: string): string {
  return emulator.replace(/\.so$/, "").replace(/_libretro$/, "") || emulator;
}

/** Render the per-emulator lines under a BIOS file — one row per emulator that uses it. */
function buildBiosCoreLines(
  cores: Record<string, { required: boolean; needs_one_of?: number | null }>,
  emulatorLabels: Map<string, string>,
  activeEmulator: string | null | undefined,
): ReactElement[] {
  return Object.entries(cores).map(([emulator, coreData]) => {
    const label = emulatorLabels.get(emulator) || fallbackEmulatorLabel(emulator);
    const suffix = coreLineSuffix(coreData);
    // Highlight the resolved active emulator's line (#955). `active_core` is the
    // emulator IDENTITY, the same space these keys are in; a null/undefined
    // active emulator matches nothing.
    const isActiveCore = emulator === activeEmulator;
    return (
      <div
        key={`core-${emulator}`}
        style={{
          color: isActiveCore ? "#d4a72c" : "rgba(255, 255, 255, 0.5)",
          fontSize: "12px",
          fontWeight: isActiveCore ? "bold" : "normal",
        }}
      >
        {`${label}${suffix}`}
      </div>
    );
  });
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
 */
function buildBiosHeader(bios: BiosStatus, biosLevel: BiosTabProps["biosLevel"]): ReactElement[] {
  const heldRatio = biosHeldRatio(bios);

  const biosColor = biosColorForLevel(biosLevel);
  const biosLabel = `${biosSummary(bios, bios.files ?? [], biosLevel).sentence}${heldRatio}`;

  return [
    <div key="bios-title" className="romm-panel-section-title" style={{ marginBottom: "8px" }}>
      BIOS
    </div>,
    <div key="bios-row" className="romm-panel-status-inline">
      <span className="romm-status-dot" style={{ backgroundColor: biosColor }} />
      <span className="romm-panel-value">{biosLabel}</span>
    </div>,
  ];
}

/**
 * Is this row's requirement met? `null` where nothing established it.
 *
 * The row's VERDICT, not `downloaded`: for a declared folder the two come apart,
 * since the folder is there on every RetroDECK install and what satisfies the
 * core is a file inside it. A payload with no verdict at all falls back to
 * `downloaded`, which is what the verdict is for a plain file.
 *
 * The dot beside a row and the decision to show that row at all read this one
 * function, so a reader cannot be shown a row whose dot answers a different
 * question from the one that kept it.
 */
function rowVerdict(file: BiosFileStatus): boolean | null {
  return file.satisfied === undefined ? file.downloaded : file.satisfied;
}

/** The dot beside one file row: what it means for THIS launch, then for others. */
function fileDotColor(file: BiosFileStatus): string {
  const verdict = rowVerdict(file);
  // Amber is the colour this surface already gives a row it cannot call
  // settled, and a null verdict is exactly that.
  if (verdict === null) return "#d4a72c";
  if (verdict) return "#5ba32b";
  if (file.required_by_active) return "#d94126";
  // Missing and not required here, but demanded by some other installed core:
  // amber, because switching cores would make it a blocker.
  const requiredElsewhere = Object.values(file.cores ?? {}).some((c) => c.required);
  return requiredElsewhere ? "#d4a72c" : "#8f98a0";
}

/**
 * The lines under a file's name — what its read found, then who uses it.
 *
 * One indented block, because they are one column to the eye and two blocks
 * would leave the images floating between the row and its cores. The row's own
 * name stays short so the status dot beside it keeps its line: folding a
 * folder's three image lines into that name is what wrapped the row and
 * orphaned the dot above it.
 *
 * **The packager's label is not among them** — it sits on the name line, where
 * it says what the file IS beside the file's own name. Under the row it read as
 * one more entry in the list of emulators that want the file, which is the one
 * thing it is not.
 *
 * The image text is the resolver's verbatim string, and `pre-wrap` keeps the
 * column padding PCSX2 puts in its own option labels — that alignment is what
 * makes a line matchable against the emulator's picker, and it still wraps
 * rather than overflowing the panel.
 */
function fileLines(lines: string[], coreLines: ReactElement[]): ReactElement | null {
  if (lines.length === 0 && coreLines.length === 0) return null;
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
      {coreLines}
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
 * Five answers keep a row, and the first two are one requirement in its two
 * spellings:
 *
 * - **required for this launch** (`required_by_active`).
 * - **the console's own image** (`system_image_candidate`) — the same demand
 *   written the only other way an emulator has for it. A libretro declaration
 *   cannot say "one of these", so an emulator whose console will not start
 *   without an image and that marks every one of them optional carries the
 *   demand here instead, and `required_by_active` is false on every such row by
 *   construction. Dropping it leaves the header stating the console's own demand
 *   — `system_image: "absent"`, the first state `utils/biosSummary.ts` tests for
 *   — over a list with no image in it: SwanStation's five, on a library holding
 *   none of them.
 * - **present** — the verdict is met, so the row is the evidence for it.
 * - **fetchable** — `isFetchable`, the same predicate the platform page's
 *   download buttons are built from: a row this page treats as actionable and
 *   that page offers no button for is a dead end pointed at.
 * - **withheld** — nothing could judge the row (`satisfied === null`). An
 *   ignorance is something to say, never something to summarise away.
 *
 * **The candidate answer is only as narrow as the set the disjunction is counted
 * over, and that set is an emulator's WHOLE declaration**
 * (`FirmwareCatalogue.emulators_needing_one_of_their_files`). For a core serving
 * several systems it is too wide: were a demand ever recorded for the Dreamcast,
 * all eight of Flycast's files would become candidates at once — the six Naomi
 * and AtomisWave arcade BIOSes among them, and no Dreamcast disc starts from one
 * of those. It is unreachable today, and NOT because Flycast marks anything
 * required: on the reference machine it marks all eight optional, `dc_boot.bin`
 * included. What keeps it out is the first of that method's two gates — the
 * catalogue records the Dreamcast's `system_firmware` as `open`, which is the
 * resolver's word for nobody having established WHICH image rather than for a
 * console that runs without one, so `system_needs_an_image` is false, Flycast is
 * not in `demanding`, and no row of its gets `needs_one_of`. The arcade rows are
 * left out by the plain rule below instead.
 *
 * What is left over is declared, not required for this launch, demonstrably
 * absent, and fetchable from nowhere — nothing a reader of THIS page could do
 * anything with. It is counted on one line instead, which names where the rows
 * are.
 */
function rowBelongsOnThisPage(file: BiosFileStatus): boolean {
  if (file.required_by_active || file.system_image_candidate) return true;
  // Present and withheld in one test: `false` is the only verdict that leaves a
  // row with nothing to say here.
  if (rowVerdict(file) !== false) return true;
  return isFetchable(file);
}

/**
 * One row per firmware file this launch can act on, plus a note for the rest.
 *
 * The rows are the files with an owning emulator — the ones whose per-core lines
 * say something — narrowed to those {@link rowBelongsOnThisPage} keeps. The
 * notes below them keep the remaining answers apart (#1762): files nothing asks
 * for are a finished answer, files nothing could be asked about are not, and the
 * old single line called both "not required by any known core" while counting
 * neither. A third note counts what the narrowing left out, and is printed
 * first of the three: those rows are the nearest relatives of the rows above
 * them — a file an installed emulator does ask for.
 */
function buildBiosFileList(bios: BiosStatus, coreInfo: CoreInfo | null): ReactElement[] {
  // Build identity -> label lookup from the dedicated core-info path (#923).
  // The join is the emulator IDENTITY because that is what a row's `cores` map
  // is keyed on: `core_so` is null for every standalone emulator and carries no
  // extension for a libretro one, so it matches nothing here.
  //
  // Two rows can be one emulator — ES-DE lists one `pcsx2_libretro.so` as both
  // LRPS2 and PCSX2 — so the first declared wins, which is the order the picker
  // offers them in and the order the default is chosen from. An entry the
  // resolver could not identify carries no identity and names no line.
  const emulatorLabels = new Map<string, string>();
  for (const e of coreInfo?.emulators ?? []) {
    if (e.emulator && !emulatorLabels.has(e.emulator)) emulatorLabels.set(e.emulator, e.label);
  }

  const files = bios.files ?? [];
  const wantedFiles = files.filter((f) => f.wanted === "needed" || f.wanted === "optional");
  const shownFiles = wantedFiles.filter(rowBelongsOnThisPage);
  const countOf = (wanted: FirmwareWanted) => files.filter((f) => f.wanted === wanted).length;

  const fileElements = shownFiles.map((f) => {
    const coreLines = f.cores ? buildBiosCoreLines(f.cores, emulatorLabels, coreInfo?.active_core) : [];
    const { note, lines } = biosFileNote(f);
    // The row is headed by the file the emulator DECLARED — `declared_path`, not
    // `file_name`, which is only its basename: a `dc/dc_boot.bin` row would
    // otherwise head itself `dc_boot.bin` and a declared folder `bios`, taking
    // away the one thing a reader placing a file by hand needs. The platform
    // detail states the same thing by splitting the path into a muted folder
    // prefix and the name; this row prints the path whole.
    //
    // The head is never `description`, which used to be it: that is the
    // packager's prose out of a core's `.info`, deliberately outside the
    // resolver's contract, and for a row no placement covers the backend fills
    // the file name into it — so the headline was the name wearing another
    // field's clothes. What the description still ADDS follows the name on the
    // same line, in the packager's own punctuation with our declared path in
    // front of it (`scph5500.bin (PS1 JP BIOS)`), which is how it was written
    // before the repeated name was cut out of it.
    //
    // Three parts, three jobs, and the line is read left to right: the NAME,
    // then what the file IS, then how it STANDS. The last keeps the em dash it
    // always had, because a dash is the mark for a state and parentheses are the
    // mark for an identity — which is what lets both sit on one line without
    // reading as a chain of equals. Under the row the label read as a sixth
    // entry in the list of emulators that want the file.
    const label = biosFileDescription(f);
    const suffix = note ? ` — ${note}` : "";

    return (
      <div key={f.file_name} className="romm-panel-file-row">
        <span key="dot" className="romm-status-dot" style={{ backgroundColor: fileDotColor(f) }} />
        <span key="name" className="romm-panel-file-name">
          {f.declared_path || f.file_name}
          {/* Muted like the emulator lines below rather than like the name
              beside it: the name is what the eye lands on and the label is
              beside it, not part of it. Its own span, so the row's one span is
              now three nested pieces and the flex row still sees one item —
              siblings would take the row's 8px gap where the packager wrote a
              space. */}
          {label !== null && <span key="label" style={{ color: "rgba(255, 255, 255, 0.5)" }}>{` ${label}`}</span>}
          {suffix}
        </span>
        {fileLines(lines, coreLines)}
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
  // how many, that this launch needs none of them, and WHERE they are — the
  // Library page's Platforms tab, which is the name the user guide gives that
  // surface. Both claims hold of every row counted rather than of most of them:
  // "required" covers both spellings of the launch's requirement, and "to
  // download" is `isFetchable`, so a row that fails neither is kept above. Two
  // sentences rather than a pluralised one, because "none required" over a set
  // of one reads as a slip.
  const elsewhere = wantedFiles.length - shownFiles.length;
  if (elsewhere > 0) {
    fileElements.push(
      summaryLine(
        "elsewhere-note",
        elsewhere === 1
          ? "1 more file an installed emulator asks for — not required for this launch, " +
              "nothing to download; the Library page's Platforms tab lists it"
          : `${elsewhere} more files an installed emulator asks for — none required for this launch, ` +
              "none to download; the Library page's Platforms tab lists them",
      ),
    );
  }

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

export const BiosTab: FC<BiosTabProps> = ({ biosStatus, biosLevel, coreInfo, isActive }) => {
  if (!isActive || !biosStatus) return null;

  // Left column: BIOS status + file list
  const biosColumn = buildBiosHeader(biosStatus, biosLevel);

  const fileElements = buildBiosFileList(biosStatus, coreInfo);
  if (fileElements.length > 0) {
    biosColumn.push(
      <div key="bios-file-list" className="romm-panel-file-list">
        {fileElements}
      </div>,
    );
  }

  // Right column: Core info
  const coreColumn = [
    <div key="core-title" className="romm-panel-section-title" style={{ marginBottom: "8px" }}>
      Emulator
    </div>,
    infoRow("core", "Active Core", coreInfo?.active_core_label ? coreInfo.active_core_label : "Default"),
  ];

  return section(
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
  );
};
