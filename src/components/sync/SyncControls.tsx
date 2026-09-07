/**
 * The Sync page's right column: everything that is not the preview or the run.
 *
 * Options (the persisted Skip-preview intent and Force Full Sync), Steam's
 * memory now and what the last run did to it, and the recorded runs. 270 px, so
 * a run row gets one line of description and long lists are counted rather than
 * listed.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Sync.
 */

import type { FC, ReactNode } from "react";
import { ConfirmModal, DialogButton, Focusable, ToggleField, showModal } from "@decky/ui";
import type { SyncRunRecord, SyncStats } from "../../types";
import { pluralize } from "../../utils/pluralize";
import { formatGb, formatSignedGb, memoryLevelColor } from "../SessionBudgetBanner";
import { AMBER, GREEN, MUTED, Muted, RED, SECONDARY_FONT, SectionTitle } from "../qam/pane";
import type { SyncPageState } from "./useSyncPage";

/** How a run ended, in one word and one colour. `running` is the one in flight;
 *  the five terminals a run reaches exactly once are the rest. */
const RUN_STATUS_COLOR: Record<SyncRunRecord["status"], string | undefined> = {
  running: undefined,
  completed: GREEN,
  paused: AMBER,
  cancelled: MUTED,
  interrupted: AMBER,
  errored: RED,
};

/**
 * When a run started, as the reader thinks of it. Today and yesterday are named
 * rather than dated, because a 270 px row has no width for a date the reader can
 * work out from the word.
 *
 * Reads the clock, like `formatTimeAgo` does and for the same reason: the answer
 * is about now, and re-deriving it per render is what keeps it true across a
 * page that stays open.
 */
export function formatRunStart(iso: string): string {
  const started = new Date(iso);
  if (Number.isNaN(started.getTime())) return iso;
  const time = started.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const now = new Date();
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(started, now)) return `Today ${time}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(started, yesterday)) return `Yesterday ${time}`;
  return `${started.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${time}`;
}

/**
 * What a run covered, in the one line the column has room for.
 *
 * A `null` completed list is a run that never recorded one, not a run that
 * synced nothing — so the planned counts are what can honestly be said about it,
 * and the status word beside the row is what says why the list is missing. An
 * empty list would read as a finished run that touched nothing.
 *
 * A run whose plan held no platform — a collections-only run, or one that
 * planned nothing — drops the platform clause on both branches rather than
 * reading "0 platforms" over a scope it never had. What is left to say then
 * differs by branch, and the two are different facts: a run with no recorded
 * list recorded nothing about its coverage, where a run with an empty one
 * recorded that it completed nothing.
 */
export function formatRunCoverage(run: SyncRunRecord): string {
  const platforms = run.platforms_completed;
  if (platforms === null) {
    return run.platforms_planned > 0 ? `${pluralize(run.platforms_planned, "platform")} planned` : "nothing recorded";
  }
  const parts: string[] = [];
  if (run.platforms_planned > 0 || platforms.length > 0) {
    parts.push(
      platforms.length === run.platforms_planned
        ? pluralize(platforms.length, "platform")
        : `${platforms.length} of ${run.platforms_planned} platforms`,
    );
  }
  const collections = run.collections_completed;
  if (collections !== null && collections.length > 0) parts.push(pluralize(collections.length, "collection"));
  return parts.length > 0 ? parts.join(" · ") : "nothing completed";
}

/** A label/value row in the controls column, and a focus stop — the column
 *  scrolls only by moving focus, so a row nothing can focus is a row nothing can
 *  scroll past. */
const ControlRow: FC<{
  label: string;
  value: ReactNode;
  subline?: string | undefined;
  testId?: string | undefined;
}> = ({ label, value, subline, testId }) => (
  <Focusable onActivate={() => {}} data-testid={testId} style={{ padding: "4px 16px" }}>
    <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
      <span style={{ flex: "1 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
      <span style={{ flex: "0 0 auto", fontSize: SECONDARY_FONT, fontVariantNumeric: "tabular-nums" }}>{value}</span>
    </div>
    {subline !== undefined && (
      <div
        title={subline}
        style={{
          fontSize: SECONDARY_FONT,
          color: MUTED,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {subline}
      </div>
    )}
  </Focusable>
);

export const SyncControls: FC<{ state: SyncPageState }> = ({ state }) => (
  <>
    <OptionsSection state={state} />
    <MemorySection state={state} />
    <RunsSection state={state} />
  </>
);

/**
 * Whether the stats say there is nothing for Force Full Sync to forget.
 *
 * A read that has not answered says neither thing, and the two ways it can be
 * unanswered part here. While one is still coming the button waits for it —
 * not knowing is not evidence that there IS something to clear. Once one has
 * failed nothing further is coming, and a failed read is not an absence either:
 * the button goes live and the line beside it says the reading is missing,
 * because the alternative is a control the reader cannot press and cannot find
 * a reason for.
 */
function nothingToClear(stats: SyncStats | null, statsFailed: boolean): boolean {
  if (stats !== null) return !stats.last_sync && !stats.last_attempt;
  return !statsFailed;
}

/**
 * The line under Force Full Sync, which explains the state the button is in
 * rather than describing the press in the abstract.
 *
 * Ordered by what stops the press: a run in flight, then the clear already
 * made, then the stats — unread, unreadable, or holding nothing to forget.
 */
function forceFullSyncNote(state: SyncPageState): string {
  if (state.run.running) return "Not while a run is in flight.";
  if (state.fullSyncCleared !== null) return `${state.fullSyncCleared}. Pressing again would clear nothing.`;
  if (state.stats === null) {
    return state.statsFailed
      ? "Could not read what has already been synced — this still clears it."
      : "Reading what has already been synced…";
  }
  if (nothingToClear(state.stats, state.statsFailed))
    return "Nothing has been synced yet, so there is nothing to forget.";
  return "Forgets what was synced and rebuilds everything next run.";
}

const OptionsSection: FC<{ state: SyncPageState }> = ({ state }) => {
  const running = state.run.running;
  // Rendered and disabled rather than hidden: Steam's focus lands on it either
  // way, and a button that vanishes takes the reader's place in the column with
  // it. Three things stop the press — a run in flight, a clear already made
  // (pressing again would forget what is already forgotten), and stats saying
  // there is nothing recorded to forget.
  const disabled = running || state.fullSyncCleared !== null || nothingToClear(state.stats, state.statsFailed);
  return (
    <>
      <SectionTitle title="Options" />
      <div style={{ padding: "0 16px" }}>
        {/* Live during a run: the setting is read by the next press of the start
            button, so flipping it while a run is in flight changes nothing about
            that run. */}
        <ToggleField
          label="Skip preview"
          description="Start the sync without asking first."
          checked={state.skipPreview}
          bottomSeparator="none"
          onChange={state.setSkipPreview}
        />
      </div>
      <div style={{ padding: "6px 16px 0" }}>
        <DialogButton
          style={{ width: "100%", minWidth: 0, padding: "6px 10px", fontSize: "13px", color: RED }}
          disabled={disabled}
          onClick={() =>
            showModal(
              <ConfirmModal
                strTitle="Force a full re-sync?"
                strDescription="This forgets what has already been synced, so the next run re-fetches every platform and rewrites every shortcut. Your games stay in Steam — only the plugin's record of what is already correct is cleared, which is also what a resume would have continued from."
                strOKButtonText="Force Full Sync"
                strCancelButtonText="Cancel"
                onOK={state.forceFullSync}
              />,
            )
          }
        >
          Force Full Sync
        </DialogButton>
      </div>
      <Muted>{forceFullSyncNote(state)}</Muted>
      {state.optionsStatus !== null && <Muted>{state.optionsStatus}</Muted>}
    </>
  );
};

const MemorySection: FC<{ state: SyncPageState }> = ({ state }) => {
  const budget = state.budget;
  return (
    <>
      <SectionTitle title="Steam memory" />
      <ControlRow
        label="Now"
        testId="memory-now"
        value={
          budget?.rss_kb == null ? (
            <span style={{ color: MUTED }}>unavailable</span>
          ) : (
            <span style={{ color: memoryLevelColor(budget.rss_kb, budget.warn_kb, budget.ceiling_kb) }}>
              {formatGb(budget.rss_kb)}
            </span>
          )
        }
      />
      <ControlRow
        label="Last run"
        testId="memory-last-run"
        value={
          budget?.memory_delta_kb == null ? (
            <span style={{ color: MUTED }}>not recorded</span>
          ) : (
            <span style={{ color: MUTED }}>{formatSignedGb(budget.memory_delta_kb)}</span>
          )
        }
      />
    </>
  );
};

const RunsSection: FC<{ state: SyncPageState }> = ({ state }) => (
  <>
    <SectionTitle title="Last runs" />
    {/* A failed read is said where the reader is looking, and the rows already
        held stay: they were true when they were read, and an emptied list would
        read as "no runs". */}
    {state.runsFailed && <Muted>Could not read the run history. Open the page again to try.</Muted>}
    {state.runsLoading && state.runs.length === 0 && !state.runsFailed && <Muted>Reading the run history…</Muted>}
    {!state.runsLoading && !state.runsFailed && state.runs.length === 0 && <Muted>No sync has run yet.</Muted>}
    {state.runs.map((run) => (
      <ControlRow
        key={run.id}
        testId={`run-${run.id}`}
        label={formatRunStart(run.started_at)}
        value={<span style={{ color: RUN_STATUS_COLOR[run.status] }}>{run.status}</span>}
        subline={run.error === null ? formatRunCoverage(run) : `${formatRunCoverage(run)} · ${run.error}`}
      />
    ))}
  </>
);
