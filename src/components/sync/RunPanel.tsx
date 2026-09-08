/**
 * The Sync page's left column while a run is in flight: the whole run as one
 * bar, and under it every unit of the plan with its own state.
 *
 * Two levels from facts the frontend already holds. The bar, the stage caption,
 * the step counter and the estimate are `useSyncRunView`'s — the same
 * derivation Main's slot takes its bar and counter from, so the two surfaces
 * cannot disagree about the run they are both showing. The caption, the
 * fine-detail line and the estimate are read here and nowhere else: Main leaves
 * them to the page with room for them. The rows are `runUnitsStore`'s, seeded
 * from the plan and advanced by the run's own frames, which is what lets a page
 * opened mid-run show the units already worked through rather than only the
 * current one.
 *
 * Cancel Sync sits directly under the bar, above the list. That is the ORDER of
 * the column and not a compromise: the bar, the stage and the button that stops
 * the run are one thing, and the unit table under them is evidence rather than a
 * control. It is also the only place a controller can reach it from — a region
 * scrolls by moving focus and the stick walks the rows one at a time, so a
 * button under a sixteen-unit plan is sixteen presses away, which is what a
 * device round measured before it moved up here. It is where focus lands when
 * this body takes the column, too: the pane claims it on mount by the frame's
 * own entry-focus rule, because the button that got the reader here unmounted
 * with the body it was in.
 *
 * The unit list scrolls on its own, inside what is left of the column under the
 * bar and the button: a plan of fourteen platforms and three collections is
 * taller than the Deck's column, and without a region of its own the running
 * unit walks out of sight below the fold. The running row is scrolled to the
 * middle of that region as the run reaches it, because nothing moves focus
 * during a run and Steam scrolls a region only by moving focus.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Sync.
 */

import { useEffect, useRef, type CSSProperties, type FC, type ReactNode } from "react";
import { DialogButton, ProgressBar } from "@decky/ui";
import type { RunUnit } from "../../utils/runUnitsStore";
import { pluralize } from "../../utils/pluralize";
import type { SyncProgress, SyncStage } from "../../types";
import { ENTRY_FOCUS_DELAY_MS, firstBodyStop, placeEntryFocus } from "../../utils/entryFocus";
import { offsetWithinScroller } from "../../utils/scrollHelpers";
import { ButtonRow, FLAT_BUTTON, GREEN, MUTED, Muted, SECONDARY_FONT, SectionTitle } from "../qam/pane";
import { ScrollRegion } from "../qam/ScrollRegion";
import { InlineBar, PaneRow, TableHeader, TableRow } from "./paneTable";
import type { SyncPageState } from "./useSyncPage";

/** Unit, what is happening to it, what it produced. */
const RUN_COLUMNS = "minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1fr)";

/** Marks the unit list's own scroller, so the running row can be scrolled to
 *  from the ref on the pane around it. */
const UNIT_REGION_TESTID = "run-units";

/** The pane fills the column and hands what is left over to the unit list —
 *  which is what gives that list a height to scroll inside. `minHeight: 0` on
 *  both, or a flex child's floor is its content and the list grows instead. */
const RUN_PANE: CSSProperties = { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 };

/** `height: auto` overrides the region's own `100%`: inside a column flex that
 *  would measure against the pane rather than against what is left of it. */
const UNIT_REGION: CSSProperties = { flex: "1 1 auto", height: "auto", minHeight: 0 };

/**
 * Put *row* in the middle of *region*, without scrolling past either end.
 *
 * A run walks its own rows, so nothing moves focus and Steam scrolls nothing —
 * the list has to be moved here or the running unit walks off the bottom. The
 * clamp is what keeps the first and last rows where they belong: centring row 1
 * of 16 would ask for a negative offset, and a region whose content already fits
 * has no offset to give, so it is left alone.
 */
function centreRowInRegion(region: HTMLElement, row: HTMLElement): void {
  const furthest = region.scrollHeight - region.clientHeight;
  if (furthest <= 0) return;
  const centred = offsetWithinScroller(row, region) + row.getBoundingClientRect().height / 2 - region.clientHeight / 2;
  region.scrollTo({ top: Math.max(0, Math.min(furthest, centred)), behavior: "smooth" });
}

/**
 * What the running row's status says, scoped to the UNIT rather than to the run.
 *
 * `stageLabel` on the shared hook names the run's phase ("Fetching library"),
 * which is the right caption over the whole-run bar and the wrong one in a row
 * that already names one platform. Same stages, said from one row's point of
 * view.
 */
const UNIT_STAGE_TEXT: Record<SyncStage, string> = {
  discovering: "starting",
  fetching: "fetching",
  applying: "applying shortcuts",
  finalizing: "finishing",
  done: "done",
  cancelled: "stopped",
  error: "stopped",
};

function unitStageText(stage: SyncProgress["stage"]): string {
  return stage ? UNIT_STAGE_TEXT[stage] : "working";
}

/** What a unit's apply has brought about so far — "4 added · 1 updated", with a
 *  zero part dropped. The em dash is "nothing yet", which for a finished unit is
 *  also the honest reading of a wholesale incremental skip: no shortcut was
 *  written, and the run never said why. */
function unitOutcome(unit: RunUnit): string {
  const parts: string[] = [];
  if (unit.created > 0) parts.push(`${unit.created} added`);
  if (unit.updated > 0) parts.push(`${unit.updated} updated`);
  return parts.length > 0 ? parts.join(" · ") : "—";
}

/**
 * What the plan holds for a unit the run has not reached.
 *
 * `predictedSkip` is a plan-time prediction and never the run's verdict
 * (ADR-0023), so it is worded as an expectation. The new-shortcut count is what
 * the reader is waiting on where the plan carried one; a collection or an older
 * backend carries none, and then the unit's ROM count is what can honestly be
 * said about it.
 */
function unitPlan(unit: RunUnit): string {
  if (unit.predictedSkip) return "expected to skip";
  if (unit.newShortcutCount !== null && unit.newShortcutCount > 0) return `${unit.newShortcutCount} new`;
  return pluralize(unit.romCount, "ROM");
}

export const RunPanel: FC<{ state: SyncPageState }> = ({ state }) => {
  const run = state.run;
  const stepText = run.totalSteps > 0 ? `unit ${run.step} of ${run.totalSteps}` : "";
  const noteParts = [stepText, run.etaText ?? ""].filter((part) => part !== "");
  const pane = useRef<HTMLDivElement | null>(null);
  const running = state.units.find((unit) => unit.state === "running");
  const runningTestId = running ? unitTestId(running) : null;
  // Claim focus for the body that has just taken the column. The button the
  // reader pressed to get here — Apply Sync, or the start button on the idle
  // body — went with the body it was in, and Steam's navigation keeps a focus
  // pointer across the swap and resolves it onto whatever now sits at that
  // position, so the reader is left holding nothing. The frame's own rule,
  // scoped to this pane: the first stop in it that holds no stop of its own,
  // which is Cancel Sync — every row below it is a stop too, so what puts the
  // rule on the button is the button being first, the same ordering the pane is
  // laid out for. On the mount ALONE, which is the swap: a run re-renders per
  // frame and none of those is a moment to move the reader.
  useEffect(() => {
    const root = pane.current;
    if (root === null) return;
    // The frame's own delay, for its reason: Steam resolves that retained
    // pointer after the mount, and a focus placed before it is taken back.
    const timer = setTimeout(() => placeEntryFocus(root, firstBodyStop), ENTRY_FOCUS_DELAY_MS);
    return () => clearTimeout(timer);
  }, []);
  useEffect(() => {
    const root = pane.current;
    if (root === null || runningTestId === null) return;
    const region = root.querySelector<HTMLElement>(`[data-testid="${UNIT_REGION_TESTID}"]`);
    const row = root.querySelector<HTMLElement>(`[data-testid="${runningTestId}"]`);
    if (region !== null && row !== null) centreRowInRegion(region, row);
  }, [runningTestId]);
  return (
    <div ref={pane} style={RUN_PANE}>
      <SectionTitle title="Sync running" {...(noteParts.length > 0 ? { note: noteParts.join(" · ") } : {})} />
      <PaneRow>
        <div style={{ fontSize: SECONDARY_FONT, color: MUTED, paddingBottom: "4px" }} data-testid="run-stage">
          {run.stageLabel}
        </div>
        <ProgressBar
          indeterminate={run.coarseFraction === undefined}
          {...(run.coarseFraction !== undefined ? { nProgress: run.coarseFraction } : {})}
        />
      </PaneRow>
      <ButtonRow padding="6px 16px 4px">
        <DialogButton style={FLAT_BUTTON} disabled={state.cancelling} onClick={state.cancelRun}>
          {state.cancelling ? "Cancelling…" : "Cancel Sync"}
        </DialogButton>
      </ButtonRow>
      {state.units.length === 0 ? (
        // The plan arrives once per run, so a store that started empty after a
        // plugin reload stays empty for the rest of it. The frames still carry
        // the fine-detail line the whole panel is reading, so that is what the
        // column shows; the sentence is for the run that has neither.
        <Muted>{run.hasFineDetail ? run.fineDetailText : "Per-unit detail is not available for this run."}</Muted>
      ) : (
        <ScrollRegion testId={UNIT_REGION_TESTID} style={UNIT_REGION}>
          <TableHeader columns={RUN_COLUMNS} cells={["Unit", "Status", "Result"]} numericFrom={2} />
          {state.units.map((unit) => (
            <RunUnitRow key={`${unit.type}:${unit.id}`} unit={unit} state={state} />
          ))}
        </ScrollRegion>
      )}
      {state.status !== null && <Muted>{state.status}</Muted>}
    </div>
  );
};

/** One unit's row, by the id its `data-testid` carries — the handle the pane
 *  scrolls the running row by, and the one the tests read it by. */
function unitTestId(unit: RunUnit): string {
  return `run-unit-${unit.type}-${unit.id}`;
}

const RunUnitRow: FC<{ unit: RunUnit; state: SyncPageState }> = ({ unit, state }) => {
  // The cell clips; the title is what the reader gets back.
  const nameCell: ReactNode = (
    <span title={unit.type === "collection" ? `${unit.name} · collection` : unit.name}>
      {unit.name}
      {unit.type === "collection" && <span style={{ color: MUTED }}> · collection</span>}
    </span>
  );

  let status: ReactNode;
  let result: string;
  if (unit.state === "done") {
    status = <span style={{ color: GREEN }}>done</span>;
    result = unitOutcome(unit);
  } else if (unit.state === "running") {
    // Exactly one unit runs at a time, so the live position of THIS row is the
    // frame the whole page is already reading — paired here rather than mirrored
    // onto the row, which would re-render every reader on every frame.
    status = (
      <span>
        {unitStageText(state.run.stage)}
        <InlineBar fraction={state.run.withinUnitFraction} />
      </span>
    );
    result = unitOutcome(unit);
  } else {
    status = <span style={{ color: MUTED }}>waiting</span>;
    result = unitPlan(unit);
  }

  return (
    <TableRow
      columns={RUN_COLUMNS}
      numericFrom={2}
      testId={unitTestId(unit)}
      cells={[
        nameCell,
        status,
        <span key="result" style={{ color: unit.state === "waiting" ? MUTED : undefined }}>
          {result}
        </span>,
      ]}
    />
  );
};
