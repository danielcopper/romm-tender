/**
 * The numbers a QAM page shows for the sync run in flight, derived in one place
 * so a second page will be able to render the same run without a second copy of
 * the derivation.
 *
 * The facts themselves live in module stores that outlive every page — the frame
 * stream in `syncProgress.ts`, the run-scoped rate and plan weights in
 * `syncEta.ts`. The run's per-unit rows are a fourth store a page reads for
 * itself, never through this hook (`runUnitsStore.ts`). What this hook owns is
 * the reading: which stage label the run is at, how full the coarse bar is, how
 * far into the running unit it has got, what the fine-detail line says across a
 * unit boundary, what the estimate reads, and — once per run — that the run has
 * ended.
 *
 * **The side effects of a run ending are the caller's, and only one caller's.**
 * A run ends once, but every mounted consumer of this hook watches it end: each
 * holds its own watch, so each would fire its own {@link
 * SyncRunViewOptions.onRunEnd}. The page that owns the run's end — today Main,
 * which announces it, re-reads the stats and the session budget, and asks for a
 * preview the run may have staged — passes the callbacks. A second consumer
 * passes none and reads the same numbers. What the hook does for every consumer
 * is tear down the live-ETA state, which is idempotent and belongs to the
 * reading rather than to the page.
 *
 * **Why a run's end is two callbacks and not one with a mode.** The backend
 * signals a run's end TWICE, in a fixed order: `sync_complete`, which
 * `index.tsx` merges into the store as `{running: false, stage}` — keeping the
 * PREVIOUS frame's message, e.g. "Finalizing…" — and then the run's own terminal
 * frame carrying the authoritative wording ("Sync complete: N games from M
 * platforms", the cancelled/interrupted sentence, or a budget pause's resume
 * guidance). Both belong to the same run: the first does the once-per-run
 * teardown, the second is allowed to correct the text it left behind. They are
 * different actions, so they are different callbacks.
 */

import { useEffect, useRef, useState } from "react";
import { logError } from "../api/backend";
import { formatDuration } from "./syncEstimate";
import {
  displayedEtaSeconds,
  formatEtaCountdown,
  latchedCoarseFraction,
  observeApplyProgress,
  resetEta,
} from "./syncEta";
import { getSyncProgress, onSyncProgressChange, withinUnitFraction } from "./syncProgress";
import type { SyncProgress, SyncStage } from "../types";

const TERMINAL_STAGES: ReadonlySet<SyncStage> = new Set<SyncStage>(["done", "cancelled", "error"]);

/** Whether a stage stops the run — the three the backend pairs with
 *  `running: false`, and the only frames that may end a watch. */
function isTerminalStage(stage: SyncProgress["stage"]): boolean {
  return !!stage && TERMINAL_STAGES.has(stage);
}

const STAGE_LABELS: Record<SyncStage, string> = {
  discovering: "Discovering platforms",
  fetching: "Fetching library",
  applying: "Applying shortcuts",
  finalizing: "Finalizing",
  done: "Done",
  cancelled: "Cancelled",
  error: "Error",
};

/** The caption a page shows for the run's stage; "Syncing" before the run names
 *  one (a panel's own optimistic start carries no stage). */
function stageLabel(stage: SyncProgress["stage"]): string {
  return stage ? STAGE_LABELS[stage] : "Syncing";
}

/** The bare fine-detail message. The coarse "step/totalSteps" is shown on the
 *  bar row, so it is not repeated here. */
function formatProgressText(progress: SyncProgress | null): string {
  if (!progress) return "Syncing...";
  return progress.message || "Syncing...";
}

/** The id of the run a frame belongs to, `""` for a frame that names none — a
 *  panel's own optimistic start, or a backend with no run stamped yet. */
function frameRunId(progress: SyncProgress): string {
  return progress.runId ?? "";
}

/** The run a frame puts in flight, or `null` when the frame has none running. */
function inFlightRunId(progress: SyncProgress): string | null {
  return progress.running ? frameRunId(progress) : null;
}

/**
 * Whether a terminal frame ends the run being watched — true unless it names a
 * DIFFERENT run, since an unnamed run on either side (a panel's own optimistic
 * start before the backend stamps an id, or a frame that carries none) cannot be
 * shown to be another one, and refusing it would strand the reading on a run
 * that has already ended. `null` — nothing watched at all — is the one case that
 * ends nothing: that is a terminal frame the consumer FOUND rather than
 * witnessed, the stored frame every QAM close leaves behind (#1019).
 *
 * The leniency leaves one window open, knowingly: between a run's two terminal
 * signals — the merged `sync_complete` and the frame that follows it, under
 * 100 ms apart — a user who starts the next run in the gap has the old run's
 * second frame taken as ending the new one, costing that run its first status
 * line. Accepted rather than closed: the previous boolean had the same window,
 * nothing here can widen it, and the alternative — refusing an unnamed terminal
 * frame — strands the reading on the far more reachable case above.
 */
function endsWatchedRun(watched: string | null, runId: string): boolean {
  return watched !== null && (watched === "" || runId === "" || watched === runId);
}

/** The callbacks a run's end fires. Both are the OWNING page's — a second
 *  consumer of the same run passes neither. */
export interface SyncRunViewOptions {
  /** The watched run has just ended, once per run, with its terminal frame. */
  onRunEnd?: (progress: SyncProgress) => void;
  /** The same run's second terminal frame, carrying the account of how it ended
   *  that replaces the text the merged `sync_complete` frame left behind. Called
   *  only for a frame that has a message of its own, so a page never has to
   *  decide whether an empty one should blank what is already shown. */
  onTerminalWording?: (message: string, stage: SyncProgress["stage"]) => void;
}

/** What a page needs to render the run in flight. */
export interface SyncRunView {
  /** Whether a run is in flight, read from the frame and never mirrored in a
   *  second boolean — other readers of the same store would disagree with a page
   *  that ended a run locally without ending it in the store (#1019). */
  running: boolean;
  stage: SyncProgress["stage"];
  stageLabel: string;
  /** The running unit's 1-based index, `0` before the run reaches one. */
  step: number;
  /** The run's unit count, `0` before the plan reaches the frontend. */
  totalSteps: number;
  /** How far into the running unit the run is (0..1), placed in the running
   *  phase's own monotonic sub-slice. Exactly one unit runs at a time, so this
   *  is the live position of whichever row `runUnitsStore` has in `running`. */
  withinUnitFraction: number;
  /** The coarse bar's percentage (0-100), or `undefined` while the run has no
   *  unit count — which the bar reads as indeterminate. */
  coarseFraction: number | undefined;
  /** Whether a fine-detail line should be shown at all. */
  hasFineDetail: boolean;
  fineDetailText: string;
  /** The estimate readout, or `null` when neither a measured countdown nor a
   *  static seed exists — honest silence. */
  etaText: string | null;
  runId: string;
}

export function useSyncRunView(options: SyncRunViewOptions = {}): SyncRunView {
  // The frame this consumer last saw, seeded from the store so a mount mid-run
  // renders the live run on its first pass rather than a frame later.
  const [progress, setProgress] = useState<SyncProgress | null>(() => getSyncProgress());
  // Last non-empty fine-detail line, carried across unit-boundary anchor frames
  // so the fine-detail row (and its inline spinner) stay MOUNTED when the next
  // unit's FETCHING anchor frame resets current/total to 0 (#1415) — otherwise
  // the row unmounts for a frame and the panel flickers. Populated by the store
  // subscriber from any frame with real fine detail; reset to null when the run
  // ends, so a terminal/idle state never surfaces a stale line.
  const [carriedFineDetail, setCarriedFineDetail] = useState<string | null>(null);
  // A dumb mirror of syncEta's live countdown (seconds), or null when not measured
  // yet / between runs. syncEta owns the sticky deadline; the impure now-read that
  // resolves it to seconds lives in the store subscriber (an event handler), NOT
  // the render — the render must stay pure. Progress frames drive the subscriber,
  // so the countdown ticks once per frame.
  const [liveEtaDisplay, setLiveEtaDisplay] = useState<number | null>(null);
  // The run this consumer is watching, by id, and whether its end has been
  // handled. Seeded at first render — BEFORE the subscription below exists, so
  // the mount seed's own write is measured against the state it replaced rather
  // than against itself; a stored terminal frame the consumer merely finds
  // therefore watches nothing and announces nothing (#1019).
  const watchedRunId = useRef<string | null>(inFlightRunId(getSyncProgress()));
  const watchedRunEnded = useRef(false);
  // The callbacks as of the latest render, so the subscription below can be made
  // once and still call the page's current closures — an inline callback is a
  // fresh function every render, and re-subscribing per render would put the
  // run's end back in reach of a race with its own teardown.
  const callbacks = useRef(options);
  useEffect(() => {
    callbacks.current = options;
  });

  useEffect(() => {
    /**
     * The watched run has ended: everything that happens once per run.
     *
     * Which of the three actions below a frame calls for is decided at the call
     * site and never re-derived in any of them. Those verdicts are taken from the
     * run refs before the refs move on and OUTSIDE the subscriber's try, so a
     * throw in here can never leave a page believing a finished run is still
     * live; recomputing them inside would put that decision back into the guarded
     * region.
     */
    const endWatchedRun = (frame: SyncProgress) => {
      // Tear down the run's live-ETA state (deadline included) so the next run
      // measures fresh, and clear the display mirror.
      resetEta();
      setLiveEtaDisplay(null);
      callbacks.current.onRunEnd?.(frame);
    };

    /**
     * The same run's SECOND terminal signal, which ends nothing — {@link
     * endWatchedRun} has already run for this run. A frame with no message of its
     * own changes nothing rather than blanking what is already shown, which is
     * why the message is a required argument: the caller's guard is the only
     * place that decision is taken, and the signature makes calling without one
     * impossible rather than merely wrong.
     */
    const correctTerminalWording = (message: string, stage: SyncProgress["stage"]) => {
      callbacks.current.onTerminalWording?.(message, stage);
    };

    /**
     * The non-terminal half: keep the live-rate ETA moving. Called only from the
     * subscriber, and only for a frame whose stage is not terminal.
     *
     * Feeds the estimator from applying frames that carry ITEM progress only —
     * fetch frames carry page/cover counters, and an applying-stage cover-refresh
     * frame (``coverRefresh``, #1456) carries a cover counter, not item progress,
     * so both must be skipped. syncEta re-anchors its sticky deadline internally;
     * the countdown is then mirrored into state here. Both now-reads are impure
     * and so must stay on this side of the render boundary, which they do — this
     * runs from an event handler, never from render.
     */
    const advanceLiveEta = (frame: SyncProgress) => {
      if (
        frame.stage === "applying" &&
        frame.step !== undefined &&
        frame.current !== undefined &&
        !frame.coverRefresh
      ) {
        observeApplyProgress(frame.step, frame.current, Date.now());
      }
      setLiveEtaDisplay(displayedEtaSeconds(Date.now()));
    };

    // Subscribe to the module store — every backend sync_progress event and
    // every frontend updateSyncProgress notifies, driving a re-render. What the
    // end-of-run work keys on is the watched RUN reaching a terminal stage, not a
    // terminal stage being present: the frame found in the store on mount ends
    // nothing, and the two frames that end the same run are one ending (see the
    // run refs above).
    return onSyncProgressChange(() => {
      // The local mirror must update FIRST and unconditionally — it is what
      // drives the re-render. Everything after it is derived work (terminal
      // teardown, estimator feeding, ETA state) that must never be able to break
      // the re-render chain (on-device freeze, cause not yet reproduced in tests).
      const frame = getSyncProgress();
      setProgress(frame);
      // Run bookkeeping, taken before the refs move on and outside the try below so
      // a subscriber throw can never leave a page believing a finished run is
      // still live. A running frame is what starts a watch; the frames that end one
      // split into the first (the run ended: tear down and announce) and any that
      // follow it (the same run's authoritative wording: the text, nothing else).
      const inFlight = inFlightRunId(frame);
      if (inFlight !== null) {
        watchedRunId.current = inFlight;
        watchedRunEnded.current = false;
      }
      const terminal = isTerminalStage(frame.stage);
      const runEndedNow =
        terminal && !watchedRunEnded.current && endsWatchedRun(watchedRunId.current, frameRunId(frame));
      const laterTerminalFrame = terminal && watchedRunEnded.current;
      if (runEndedNow) watchedRunEnded.current = true;
      // Carry the fine-detail line so the row survives a unit boundary's anchor
      // frame (which resets current/total, #1415); drop it the moment the run
      // ends so the next run starts clean. Kept outside the try below so the
      // reset can never be skipped by a subscriber throw.
      //
      // The carry is refreshed by any running frame that has a message AND a
      // position: a real fine-detail frame (``total`` > 0) OR a unit-boundary
      // FETCHING anchor (``total`` 0 but ``totalSteps`` > 0). At the boundary the
      // anchor's own message ("Fetching <next unit>") REPLACES the previous
      // unit's carried text, so the fine line names the new unit immediately
      // instead of lagging on the old one until the next real frame lands a
      // network RTT later. The initial optimistic "Fetching library…" start (no
      // total, no totalSteps) is excluded, so it keeps the stage-label spinner;
      // an empty-message frame never clears the carry (replace, never remove).
      if (!frame.running) {
        setCarriedFineDetail(null);
      } else if (frame.message && (frame.total || frame.totalSteps)) {
        setCarriedFineDetail(frame.message);
      }
      // A terminal frame that is neither verdict — the stored one a mount merely
      // finds, watching nothing (#1019) — matches no arm and does nothing, which
      // is the whole of what it should do.
      try {
        if (runEndedNow) {
          endWatchedRun(frame);
        } else if (laterTerminalFrame && frame.message) {
          correctTerminalWording(frame.message, frame.stage);
        } else if (!terminal) {
          advanceLiveEta(frame);
        }
      } catch (e) {
        logError(`sync-progress subscriber failed: ${e}`);
      }
    });
  }, []);

  // Two-level progress. The main determinate bar tracks COARSE unit progress
  // but INTERPOLATES within the running unit so a large unit (e.g. 2091 items at
  // step 2/8) doesn't sit frozen: the bar fills from the step's floor toward the
  // next notch as the unit is worked. Notch positions come from the plan's
  // per-unit item weights when measured (#1382), else each unit is an equal
  // 1/totalSteps slice. 0/0 totalSteps means the run hasn't reached a unit yet →
  // indeterminate. ``coarseFraction`` is a percentage (0-100), not a fraction.
  //
  // While actively working a unit (``fetching``/``applying``) the current unit
  // is not yet done, so the completed count is ``step - 1``; the terminal-ish
  // stages (``finalizing``/``done``) carry ``step == totalSteps`` as a
  // completed count, so they keep the full ``step`` and the bar reads 100%.
  // The within-unit fill splits the running unit's width into three monotonic
  // sub-slices — fetch → covers → apply (#1407, ``withinUnitFraction``) — each
  // filling by its own phase's ``current/total`` within a strictly-higher band
  // than the phase before. So the fetch and cover frames now DO advance the bar
  // (within their own sub-slice), never backwards at a phase boundary even
  // though each phase restarts ``current/total`` from zero. An old backend that
  // sends no sub-stage falls back to resting at the unit floor during fetch.
  const step = progress?.step ?? 0;
  const activeUnit = progress?.stage === "fetching" || progress?.stage === "applying";
  const completedSteps = activeUnit ? Math.max(0, step - 1) : step;
  const withinUnit = withinUnitFraction(progress);
  // Weight the bar by the plan's per-unit item weights (#1382) — the same
  // skip-aware, delta-corrected weights the countdown uses — so a
  // predicted-skip unit takes no width and a huge platform takes its real
  // share — except a run's LEADING zero-weight units, which still refresh
  // covers and so claim an equal index slice rather than pinning the bar to
  // empty (#1506). The latched wrapper adds a run-scoped high-water floor so a
  // mid-run upward weight correction (observeUnitTotal on a mispredicted
  // trailing skip) can't retract shown width (#1509). Falls back to
  // equal-per-unit index weighting when no plan is measured (QAM opened mid-run
  // before any sync_plan, old backend) or the plan can't apportion (unit-count
  // mismatch, all-zero weights).
  const weightedFraction = progress?.totalSteps
    ? latchedCoarseFraction(completedSteps, withinUnit, progress.totalSteps)
    : null;
  const coarseFraction = progress?.totalSteps
    ? Math.max(0, Math.min(100, (weightedFraction ?? (completedSteps + withinUnit) / progress.totalSteps) * 100))
    : undefined;
  const currentHasFineDetail = !!(progress?.total && progress.message);
  // Keep the fine-detail row mounted across unit boundaries: the next unit's
  // FETCHING anchor frame resets current/total (#1415), so fall back to the last
  // non-empty fine detail carried by the store subscriber. Cleared when the run
  // ends, so terminal/idle states never surface a stale line. The bar's own
  // within-unit fill still reads the live current/total (never the carry), so
  // this affects only which rows mount, not the bar (#1407).
  const hasFineDetail = currentHasFineDetail || carriedFineDetail !== null;
  const fineDetailText = currentHasFineDetail ? formatProgressText(progress) : (carriedFineDetail ?? "");

  // Estimated-time readout for the in-flight run. Prefer the live measured
  // countdown ("9 min left") once the estimator has a rate; before that, fall
  // back to the static seed carried on the store as an upper bound ("up to
  // X min"). Absent both, the row is omitted (honest silence).
  const staticEtaSeconds = progress?.etaSeconds;
  let etaText: string | null = null;
  if (liveEtaDisplay !== null) {
    etaText = formatEtaCountdown(liveEtaDisplay);
  } else if (staticEtaSeconds !== undefined) {
    etaText = `up to ${formatDuration(staticEtaSeconds)}`;
  }

  return {
    running: progress?.running ?? false,
    stage: progress?.stage,
    stageLabel: stageLabel(progress?.stage),
    step,
    totalSteps: progress?.totalSteps ?? 0,
    withinUnitFraction: withinUnit,
    coarseFraction,
    hasFineDetail,
    fineDetailText,
    etaText,
    runId: progress?.runId ?? "",
  };
}
