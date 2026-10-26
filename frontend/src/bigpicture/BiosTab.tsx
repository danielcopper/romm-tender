/**
 * BiosTab — the BIOS pane of the RomM game detail panel.
 *
 * Two columns. The left one holds the launching emulator's readiness sentence,
 * its one-of groups, the emulator selector, the shown emulator's files as a
 * table with a button on each row that has one, and the buttons for the
 * requirement and for the whole platform; the right one the files in the RomM
 * library no emulator here asks for. Where the page is too narrow for both, the
 * right column stands below the left one. The table, its buttons, their
 * confirmations, the line their presses leave and where focus goes after one
 * are the platform page's (`bios/BiosFiles.tsx`), so a file reads and acts the
 * same on both pages.
 *
 * The answer it shows arrives as props, because the requirement (`biosStatus`)
 * and the emulator it is shown against (`coreInfo`) have to reach a render
 * TOGETHER: on a core change the panel reads both in one `Promise.all` and folds
 * them into one update. What the page holds of its own — which emulator the
 * table shows, a press under way, the line it left and what it did to each row —
 * is {@link useBiosTab}'s, which the panel calls, so that a tab switch keeps it
 * (`docs/architecture/qam-panel.md`, "The game page's tabs").
 *
 * CSS classes prefixed with `romm-panel-` are injected separately by
 * styleInjector.
 */

import { FC, useState, type FocusEvent, type ReactElement } from "react";
import { Focusable } from "@decky/ui";
import type { BiosLevel, BiosStatus, CoreInfo } from "../types";
import { biosColorForLevel } from "../utils/biosColor";
import { type GroupLineTone } from "../utils/biosGroup";
import { biosHeldRatio } from "../utils/biosHeldRatio";
import { biosAnswerStale, biosReadState } from "../utils/biosReadState";
import { storeOnlySentence } from "../utils/biosPlaceWords";
import { biosSummary, groupBlocks } from "../utils/biosSummary";
import { emulatorDataReasonSentence } from "../utils/emulatorSourceWording";
import { scrollFocusedToCenter } from "../utils/scrollHelpers";
import {
  BiosButtons,
  BiosEmulatorSelector,
  BiosLibraryTable,
  BiosPartLegend,
  BiosShownTable,
  useBiosFiles,
  type BiosPart,
} from "./bios/BiosFiles";
import { announceBiosChange, useBiosActions, type BiosStatusLine } from "./bios/useBiosActions";
import { PANE_GUTTER } from "./layout/pane";
import { section } from "./panelSection";
import { WaitingLine } from "./WaitingLine";

export interface BiosTabProps {
  /** The game's BIOS answer, or null while none is held: still out, or a read
   *  that failed with nothing behind it (`readFailed`). The answer that no BIOS
   *  is needed is a status like any other, drawn the same way. */
  biosStatus: BiosStatus | null;
  /** Backend-computed readiness verdict driving the status-dot color;
   *  null while no answer is held. */
  biosLevel: BiosLevel | null;
  /** Whether the latest read of the answer failed — with `biosStatus` held, what
   *  is shown is from before it (`utils/biosReadState.ts`). */
  readFailed?: boolean;
  /** The platform's emulator list, for the line that says why there is none.
   *  From the dedicated `get_platform_core_info` path, never derived from
   *  `biosStatus`. */
  coreInfo: CoreInfo | null;
  /** RomM's name for the game's platform, which the console's own demand is
   *  said of. Empty where none is held, and the sentence names the system
   *  without it. */
  platformName?: string;
  /** What the page holds of its own, from {@link useBiosTab}. */
  tabState: BiosTabState;
}

export interface BiosTabStateInput {
  /** The game's BIOS answer, which the table's rows come from. */
  biosStatus: BiosStatus | null;
  /** RomM's name for the game's platform; the slug stands in where it is empty. */
  platformName?: string;
  /** The game's platform, which every press acts on. */
  platformSlug?: string;
  /** The game every press is for. */
  romId?: number | null;
  /** Read the BIOS answers a press changes — the tab's own and the play row's;
   *  the press's spinner ends once they are in. */
  onReread?: () => Promise<void>;
  /** Tell the other pages the platform's firmware changed, once `onReread` is in. */
  onAnnounce?: (slug: string) => void;
}

/** What the BIOS tab holds of its own across the switches that unmount its pane. */
export interface BiosTabState {
  part: BiosPart;
  status: BiosStatusLine | null;
}

const NO_REREAD = async (): Promise<void> => {};

/**
 * The BIOS tab's own state: which emulator the table shows, a press under way
 * and the line it left. A version switch keeps it, since all of it is about the
 * game's platform, which the switch keeps.
 */
export function useBiosTab({
  biosStatus,
  platformName,
  platformSlug = "",
  romId = null,
  onReread = NO_REREAD,
  onAnnounce = announceBiosChange,
}: BiosTabStateInput): BiosTabState {
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [status, setStatus] = useState<BiosStatusLine | null>(null);
  const actions = useBiosActions({
    setBusy: setBusySlug,
    setStatus,
    reread: onReread,
    announce: onAnnounce,
    romId,
  });
  const part = useBiosFiles({
    slug: platformSlug,
    platformName: platformName || platformSlug,
    answer: biosStatus ?? {},
    busySlug,
    serverOffline: biosStatus?.server_offline ?? false,
    actions,
    launching: "shown",
    stop: "button",
  });
  return { part, status };
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
    biosTitle(),
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

function biosTitle(): ReactElement {
  return (
    <div key="bios-title" className="romm-panel-section-title" style={{ marginBottom: "8px" }}>
      BIOS
    </div>
  );
}

/**
 * The tab while no answer is held: its heading, and either that the answer is
 * on its way or that the read for it failed. The two never read alike — a
 * reader takes a silent tab for an answer.
 */
function unansweredTab(failed: boolean): ReactElement {
  return section(
    "bios-core",
    null,
    biosTitle(),
    failed ? (
      <div key="bios-read-failed" className="romm-panel-value" data-testid="bios-read-failed">
        Could not read the BIOS state. Open the game page again to retry.
      </div>
    ) : (
      <div key="bios-checking" className="romm-panel-value">
        <WaitingLine>Checking…</WaitingLine>
      </div>
    ),
  );
}

/** The dot beside one line of the group block. */
const GROUP_TONE_COLOR: Readonly<Record<GroupLineTone, string>> = {
  here: "#5ba32b",
  missing: "#d94126",
  other: "#8f98a0",
  // Not established is grey, never amber.
  unchecked: "#8f98a0",
};

// A subheading under the BIOS heading: the section label's own class, which
// upper-cases it, with room above to set it off from what precedes it.
const SUBHEADING_STYLE = { marginTop: "12px", marginBottom: "4px" };

/**
 * The launching emulator's one-of groups, under the headline: which image each
 * disc region needs and whether it is there, with the game's own region marked.
 * Each group stands under a subheading of its own, its lines indented beneath
 * it. The table below stays whole — the block is a reading of some of its rows,
 * not a replacement for them. What it says is `utils/biosGroup.ts`'s and
 * `utils/biosSummary.ts`'s; this lays it out.
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
 * The left column's width — about that of a wide Quick Access page — and the
 * least the right one needs beside it. Together with the gap between them they
 * are what the page has to be wide enough for, or the right column wraps below
 * the left: Big Picture's game page is on a desktop monitor, and is not on the
 * Deck.
 */
const MAIN_COLUMN = "760px";
const SIDE_COLUMN = "400px";
/** A column, and the focus group inside it filling the column's whole height. */
const COLUMN_BOX = { display: "flex", flexDirection: "column", minWidth: 0 } as const;
const COLUMN_FILL = { flexGrow: 1 } as const;

/**
 * Steam's own scroll to a focused element does not centre it reliably on the
 * game page (`utils/scrollHelpers.ts`), so the tab centres every stop inside it
 * — rows, buttons and the selector's — as a `section` does for itself.
 */
function centreFocused(e: FocusEvent<HTMLElement>): void {
  scrollFocusedToCenter({ currentTarget: e.target });
}

export const BiosTab: FC<BiosTabProps> = ({
  biosStatus,
  biosLevel,
  readFailed = false,
  coreInfo,
  platformName,
  tabState: { part, status },
}) => {
  const { attachRoot } = part.focus;
  const held = biosStatus !== null;
  if (!held) return unansweredTab(biosReadState(held, readFailed) === "failed");

  // The header is the launching emulator's whichever emulator the table shows.
  const header = buildBiosHeader(biosStatus, biosLevel, coreInfo, platformName || null);
  if (biosAnswerStale(held, readFailed)) {
    header.unshift(
      <div
        key="bios-stale"
        data-testid="bios-stale"
        style={{ color: "rgba(255, 255, 255, 0.5)", fontSize: "12px", marginBottom: "4px" }}
      >
        Could not re-read the BIOS state, so what is below may be out of date. Open the game page again to retry.
      </div>,
    );
  }
  const groupBlock = buildGroupBlock(biosStatus);
  if (groupBlock) header.push(groupBlock);
  const hasLibrary = part.libraryRows.length > 0;

  // The selector and the table sit below the header, outside its section: a
  // section is itself a focusable button, and their buttons inside it would be
  // buttons inside a button. Everything in the left column stands in the
  // pane's gutter, so the sentence, the selector and the table line up.
  //
  // The columns stretch to the row's height because Steam's grid flow between
  // them needs them to overlap: docs/architecture/qam-panel.md, "The game
  // page's BIOS tab stands in two columns".
  return (
    <Focusable className="romm-bios-columns" flow-children="grid">
      <div
        ref={attachRoot}
        data-testid="bios-columns"
        style={{ display: "flex", flexWrap: "wrap", alignItems: "stretch", gap: "24px 40px" }}
        onFocus={centreFocused}
      >
        <div
          data-testid="bios-main-column"
          style={{ ...COLUMN_BOX, flex: hasLibrary ? `0 0 ${MAIN_COLUMN}` : "1 1 100%", maxWidth: "100%" }}
        >
          <Focusable className="romm-bios-column" flow-children="column" style={COLUMN_FILL}>
            {section(
              "bios-core",
              null,
              <div key="bios-header" style={{ padding: `0 ${PANE_GUTTER}` }}>
                {header}
              </div>,
            )}
            <BiosEmulatorSelector part={part} padding={`4px ${PANE_GUTTER} 6px`} />
            <BiosShownTable part={part} />
            <BiosButtons part={part} status={status && { key: status.slug, scope: status.scope, text: status.text }} />
            <div data-testid="bios-legend-row" style={{ opacity: 0.6, marginTop: "12px" }}>
              <BiosPartLegend part={part} inline />
            </div>
          </Focusable>
        </div>
        {hasLibrary && (
          <div data-testid="bios-library-column" style={{ ...COLUMN_BOX, flex: `1 1 ${SIDE_COLUMN}` }}>
            <Focusable className="romm-bios-column" flow-children="column" style={COLUMN_FILL}>
              <BiosLibraryTable part={part} />
            </Focusable>
          </div>
        )}
      </div>
    </Focusable>
  );
};
