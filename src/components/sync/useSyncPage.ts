/**
 * Everything the Sync page knows and does: the pending preview and the three
 * ways to end it, the run in flight, the persisted Skip-preview intent, Force
 * Full Sync, and the recorded run history.
 *
 * It lives above the components for the reason `usePlatformsPage` does — the
 * page's three left-column bodies are three renderings of one state, and a
 * component owning its own reads would re-issue them whenever the body changed.
 *
 * **The run is read, never owned.** `useSyncRunView` fires its end-of-run
 * callbacks for the page that owns them, which is Main; this page passes none
 * and reads the same numbers (`src/utils/syncRunView.ts` states the rule). What
 * it does key on the run's end is its OWN reads — the run list, the stats and
 * the session-budget reading all describe the run that just stopped — and those
 * are observations of the frame, not a second announcement of it. They are taken
 * on a stop carrying a terminal stage, because this page also stops a run of its
 * own making: the optimistic frame it retracts when a preview answers.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Sync.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelSync,
  clearSyncCache,
  getSettings,
  getSyncRuns,
  logError,
  saveSkipPreview,
  syncApplyDelta,
  syncCancelPreview,
  syncPreview,
  startSync,
} from "../../api/backend";
import type { SessionBudgetStatus, SyncPreview, SyncRunRecord, SyncStats } from "../../types";
import { detach } from "../../utils/detach";
import {
  adoptPreview,
  clearPendingPreview,
  getPendingPreviewSnapshot,
  refreshPendingPreview,
  usePendingPreview,
} from "../../utils/pendingPreviewStore";
import { PREVIEW_COUNTDOWN_TICK_MS, previewHasChanges, previewSecondsLeft } from "../../utils/previewState";
import { useRunUnits, type RunUnit } from "../../utils/runUnitsStore";
import { previewApplySeconds } from "../../utils/syncEstimate";
import { getSyncProgress, setSyncProgress as setStoredSyncProgress } from "../../utils/syncProgress";
import {
  isCancelRequested,
  reconcileStaleShortcuts,
  requestSyncCancel,
  resetSyncCancel,
} from "../../utils/syncManager";
import { syncResumeState, type SyncResumeState } from "../../utils/syncResume";
import { isTerminalStage, useSyncRunView, type SyncRunView } from "../../utils/syncRunView";
import {
  refreshSessionBudget,
  refreshSessionBudgetAfterChange,
  refreshSyncStats,
  refreshSyncStatsAfterChange,
  useSessionBudget,
  useSyncStats,
  useSyncStatsFailed,
} from "../../utils/syncStatsStore";
import type { SyncButton } from "../SessionBudgetBanner";

/** What a preview action that did not take says when the call itself failed and
 *  there is no answer to quote. A refusal always carries a message
 *  (`.claude/rules/callables.md`); a rejection has no answer at all. */
const PREVIEW_FAILED = "Could not work out what would change. Check the connection to RomM and try again.";

/** What the armed line falls back to when the clear answered without a message
 *  of its own — the same shape every other status line here takes. */
const FULL_SYNC_CLEARED = "The next sync will re-fetch and re-apply everything";

/** How often the session-budget reading is re-taken while it can still move: a
 *  run is climbing it, or a paused run is waiting for a Steam restart to drop
 *  it. Two cadences because only one of them is watching a number change. */
const BUDGET_POLL_RUNNING_MS = 5000;
const BUDGET_POLL_PAUSED_MS = 10000;

export interface SyncPageState {
  /** The preview the backend is holding, or `null`. */
  preview: SyncPreview | null;
  /** Seconds before the backend stops accepting it, `null` when nothing is
   *  pending or the preview carries no deadline (an older backend). */
  previewSecondsLeft: number | null;
  /** Past the deadline: the change table stays, Apply Sync greys out. */
  previewExpired: boolean;
  run: SyncRunView;
  units: readonly RunUnit[];
  stats: SyncStats | null;
  /** The stats read did not answer. Held apart from a `null` {@link stats} that
   *  has simply not been read yet, because a control that waits on the stats has
   *  to tell an answer on its way from one that is not coming. */
  statsFailed: boolean;
  budget: SessionBudgetStatus | null;
  resume: SyncResumeState;
  /**
   * The button the session-budget card should point the reader at — whichever
   * of this page's own is the way forward right now. Named rather than
   * re-derived, for the reason {@link SyncButton} exists (#1789).
   */
  primaryAction: SyncButton;
  /** A preview action is in flight; every one of them is disabled meanwhile. */
  busy: boolean;
  /** The cancel is draining; the button stays dead until the run stops. */
  cancelling: boolean;
  /** Why the last preview action did not take, or `null`. */
  status: string | null;
  /** The same, for the controls column's own two actions. */
  optionsStatus: string | null;
  skipPreview: boolean;
  /** What a successful Force Full Sync answered, held until a run has been and
   *  gone. Non-null is the armed state: the clear has been made, so pressing
   *  again would clear what is already clear. */
  fullSyncCleared: string | null;
  runs: SyncRunRecord[];
  runsLoading: boolean;
  /** The run history could not be read. The rows already held stay on screen —
   *  a failed read is said, never rendered as an empty list. */
  runsFailed: boolean;
  startPreview: () => void;
  applyPreview: () => void;
  refreshPreview: () => void;
  cancelPreview: () => void;
  cancelRun: () => void;
  setSkipPreview: (value: boolean) => void;
  forceFullSync: () => void;
}

/** Which of this page's buttons the session-budget card should name. */
function primaryActionFor(preview: SyncPreview | null, expired: boolean, resume: SyncResumeState): SyncButton {
  if (preview === null) return { label: resume.label, resumes: resume.canResume };
  // A preview is up, so the start button is not on screen. Apply is the way on
  // while it is still good; past that, Refresh is the only thing that moves.
  if (!expired && previewHasChanges(preview)) return { label: "Apply Sync", resumes: resume.canResume };
  return { label: "Refresh", resumes: resume.canResume };
}

export function useSyncPage(): SyncPageState {
  const preview = usePendingPreview();
  const stats = useSyncStats();
  const statsFailed = useSyncStatsFailed();
  const budget = useSessionBudget();
  const units = useRunUnits();
  // No callbacks: a run ends once, and the page that announces it is Main.
  const run = useSyncRunView();
  const running = run.running;

  // Clock mirror for the expiry countdown, written from the interval below and
  // from every handler a preview can reach this page through — never read during
  // render, which must stay pure.
  const [nowMs, setNowMs] = useState<number | null>(null);
  const [skipPreview, setSkipPreviewState] = useState(false);
  const [busy, setBusy] = useState(false);
  // The run a cancel was asked for, by id — `""` where the frame carried none,
  // which is every run this page has started optimistically and not yet heard
  // back about. Whether the button is DISARMED is derived from it and the run in
  // flight rather than stored and cleared from the run transition, which
  // `react-hooks/set-state-in-effect` forbids. The derivation also survives what
  // such a reset would have to catch: the run the ask was aimed at is the one
  // that re-arms it, and a cancelled PREVIEW run ends by this page retracting
  // its own frame, so there is no terminal frame to hang a reset on. An unnamed
  // ask matches whatever is running, because the backend stamps an id only once
  // the run has started and the cancel can be pressed before that.
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [optionsStatus, setOptionsStatus] = useState<string | null>(null);
  const [fullSyncCleared, setFullSyncCleared] = useState<string | null>(null);
  const [runs, setRuns] = useState<SyncRunRecord[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsFailed, setRunsFailed] = useState(false);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const answer = await getSyncRuns();
      if (!answer.success) {
        setRunsFailed(true);
        return;
      }
      setRunsFailed(false);
      setRuns(answer.runs);
    } catch {
      // The rows already held are NOT cleared: they were true when they were
      // read, and a list emptied by a failed refresh reads as "no runs".
      setRunsFailed(true);
    } finally {
      setRunsLoading(false);
    }
  }, []);

  /**
   * Work out what would change, and hold the answer.
   *
   * *discardPending* is what separates Refresh from the first press: the user
   * has answered the preview on screen, so it goes on both sides before the
   * next one is asked for. A failed discard is swallowed — the backend stages
   * one snapshot at a time and the preview about to arrive replaces it anyway.
   */
  const computePreview = useCallback(async (discardPending: boolean) => {
    setBusy(true);
    setStatus(null);
    // Clear any stale cancel flag BEFORE this run starts, so a fresh preview
    // never begins pre-cancelled (#1198). The cancel ask goes with it: an ask
    // left over from an unnamed run would match the one about to start.
    resetSyncCancel();
    setCancellingRunId(null);
    if (discardPending) {
      clearPendingPreview();
      try {
        await syncCancelPreview();
      } catch (e) {
        logError(`Failed to discard the pending preview before refreshing it: ${e}`);
      }
    }
    setStoredSyncProgress({ running: true, stage: "fetching", message: "Fetching library..." });
    try {
      // Reconcile shortcuts the user deleted through Steam's own UI BEFORE the
      // work queue is built: unbind any dead binding so the incremental skip
      // re-fetches the platform and recreates the missing shortcut (#1046).
      // Best-effort — never blocks the preview.
      await reconcileStaleShortcuts();
      const result = await syncPreview();
      if (!result.success) {
        // A `@migration_blocked` refusal arrives here too, carrying its message
        // and none of the fields the type declares — which is why nothing below
        // this line reads the answer.
        setStoredSyncProgress({ running: false, stage: "" });
        setStatus(result.message || PREVIEW_FAILED);
        return;
      }
      // RC-CANCEL-PREVIEW (#1202): a Cancel can land in the sub-second window
      // while sync_preview() is in flight. A preview that finished just before
      // the cancel still resolves success — re-check the flag before putting a
      // phantom Apply on screen. The backend staged this snapshot before the
      // cancel reached it, and a cancel that lands after the preview is computed
      // never travels far enough to discard it, so say so explicitly.
      if (isCancelRequested()) {
        resetSyncCancel();
        detach(syncCancelPreview());
        setStoredSyncProgress({ running: false, stage: "" });
        setStatus("Sync cancelled");
        return;
      }
      setNowMs(Date.now());
      adoptPreview(result);
      // The preview run is over — retract the optimistic running:true rather
      // than waiting for the backend's own terminal frame, which can be dropped
      // or raced and would leave every reader of the store waiting on a run that
      // has already answered.
      setStoredSyncProgress({ running: false, stage: "" });
    } catch {
      setStoredSyncProgress({ running: false, stage: "" });
      setStatus(PREVIEW_FAILED);
    } finally {
      setBusy(false);
    }
  }, []);

  const applyPreview = useCallback(async () => {
    // Read the store rather than the render's copy: this callback outlives the
    // render that made it, and the preview it applies must be the one standing.
    const pending = getPendingPreviewSnapshot();
    if (!pending) return;
    // A press that beat the countdown's tick to the expired state. The backend
    // would refuse this apply, so don't spend the user's press on a failure they
    // could not have avoided — re-read the clock instead, which flips the panel
    // to its expired form and takes the button with it.
    if (previewSecondsLeft(pending, Date.now()) === 0) {
      setNowMs(Date.now());
      return;
    }
    const previewId = pending.preview_id;
    // Seed the apply ETA from the walk cost (shared with the estimate line via
    // previewApplySeconds) so the number the user approved is the run's seed. It
    // lands in the optimistic store write below, so the sync_plan listener sees
    // an etaSeconds already present and leaves its cruder total_roms bound off.
    const etaSeconds = previewApplySeconds(pending.summary);
    resetSyncCancel();
    clearPendingPreview();
    setStatus(null);
    setCancellingRunId(null);
    setBusy(true);
    setStoredSyncProgress({ running: true, stage: "applying", message: "Applying changes...", etaSeconds });
    try {
      const result = await syncApplyDelta(previewId);
      if (!result.success) {
        setStoredSyncProgress({ running: false, stage: "" });
        setStatus(result.message || "Could not apply the preview.");
      }
      // On success the store subscription drives the page from here.
    } catch {
      setStoredSyncProgress({ running: false, stage: "" });
      setStatus("Failed to apply sync");
    } finally {
      setBusy(false);
    }
  }, []);

  const cancelPreview = useCallback(async () => {
    // The user has answered the preview question: whatever a read still open is
    // about to hand back is a snapshot the backend is being told to discard.
    clearPendingPreview();
    setStatus(null);
    try {
      await syncCancelPreview();
    } catch (e) {
      // The panel is right either way — the preview is gone from it, and the
      // backend drops the snapshot at its own deadline. Logged rather than said,
      // because there is nothing the reader would do about it.
      logError(`Failed to discard the pending preview: ${e}`);
    }
  }, []);

  /**
   * Stop the run in flight.
   *
   * The twin of Main's own Cancel, which stays there because Main keeps showing
   * a run in flight. Both disarm into "Cancelling…" rather than re-arming here:
   * the backend drains RUNNING → CANCELLING → IDLE asynchronously, and a button
   * that came back now would let a quick re-press hit the sync_in_progress
   * reject and look like an instant finish (#1202, RC-B). What re-arms it is the
   * run stopping, whichever way it stops.
   */
  const cancelRun = useCallback(async () => {
    // One read of the run id, for the disarm and for the call it scopes. "" in
    // the pre-progress window is the backend's unconditional cancel (#1202).
    const runId = getSyncProgress().runId ?? "";
    setCancellingRunId(runId);
    requestSyncCancel();
    try {
      await cancelSync(runId);
    } catch {
      // The cancel call itself failed — no terminal will arrive from a cancel
      // that never landed, so re-arm the button and say so. The run is NOT ended
      // here: claiming otherwise would tell every other reader of the store that
      // a live run had stopped (#1019).
      setCancellingRunId(null);
      setStatus("Failed to cancel sync");
    }
  }, []);

  const changeSkipPreview = useCallback(async (value: boolean) => {
    setSkipPreviewState(value);
    setOptionsStatus(null);
    try {
      const result = await saveSkipPreview(value);
      if (!result.success) {
        setSkipPreviewState(!value);
        setOptionsStatus("Could not save that; the setting was put back.");
      }
    } catch {
      setSkipPreviewState(!value);
      setOptionsStatus("Could not save that; the setting was put back.");
    }
  }, []);

  const forceFullSync = useCallback(async () => {
    setOptionsStatus(null);
    try {
      const result = await clearSyncCache();
      if (!result.success) {
        setOptionsStatus(result.message || "Could not clear the sync cache.");
        return;
      }
      setFullSyncCleared(result.message || FULL_SYNC_CLEARED);
      // The clear has just discarded the state the pending preview was worked
      // out against, so that preview describes a delta nothing holds any more —
      // applying it would skip what the clear armed a re-fetch for. End it the
      // way Cancel does, on both sides, and let the page fall back to its idle
      // body.
      clearPendingPreview();
      try {
        await syncCancelPreview();
      } catch (e) {
        logError(`Failed to discard the pending preview after a full-sync clear: ${e}`);
      }
    } catch {
      setOptionsStatus("Failed to clear sync cache");
    } finally {
      // Re-read on every exit, including the throw: a call that never answered
      // may still have cleared, so what the stats now hold is read rather than
      // assumed. It must not join a read issued before the clear — see
      // refreshSyncStatsAfterChange.
      detach(refreshSyncStatsAfterChange());
    }
  }, []);

  const startRunDirectly = useCallback(async () => {
    setBusy(true);
    setStatus(null);
    resetSyncCancel();
    setCancellingRunId(null);
    setStoredSyncProgress({ running: true, stage: "fetching", message: "Fetching library..." });
    try {
      await reconcileStaleShortcuts();
      const result = await startSync();
      if (!result.success) {
        setStoredSyncProgress({ running: false, stage: "" });
        setStatus(result.message || "Could not start the sync.");
      }
    } catch {
      setStoredSyncProgress({ running: false, stage: "" });
      setStatus("Failed to start sync");
    } finally {
      setBusy(false);
    }
  }, []);

  // Not memoised: it reads the render's own `skipPreview`, and the press is the
  // only thing that ever calls it.
  const startPreview = () => {
    // Skip preview takes the per-unit pipeline (start_sync) — incremental
    // shortcut delivery, per-unit crash safety, no upfront full library fetch —
    // and the run view is what the reader watches instead of a table. The
    // setting lives on this page, so the button beside it has to mean it.
    if (skipPreview) {
      detach(startRunDirectly());
      return;
    }
    detach(computePreview(false));
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial async data loads on mount are the standard React pattern; the rule is overzealous here
    detach(loadRuns());
    detach(refreshSyncStats());
    detach(refreshSessionBudget());
    // The backend holds a computed preview for 30 minutes and this page's table
    // dies with the render, so ask for it back on every mount. The store decides
    // whether the answer still stands. Stamping the countdown's clock is this
    // side's job — an impure read, so it belongs in a handler.
    detach(refreshPendingPreview().then(() => setNowMs(Date.now())));
    getSettings()
      .then((s) => setSkipPreviewState(s.skip_preview ?? false))
      .catch((e) => logError(`Failed to read the skip-preview setting: ${e}`));
  }, [loadRuns]);

  // These three reads are about a RUN's end — the run list, the stats and the
  // session-budget reading all describe the run that just stopped — so they are
  // taken on a stop that carries a terminal stage, and never on every frame. A
  // stop without one is this page retracting its OWN optimistic frame after a
  // preview (`{running: false, stage: ""}`): that ended no run, and there is
  // nothing about one for them to read. Reading it that way rests on the
  // backend's half — an emitted frame stops a run only with a terminal stage,
  // which CLAUDE.md's invariant register carries as its own entry — so a
  // stopping frame without one came from here. `useSyncRunView`'s own callbacks
  // are not used for it either: those belong to the page that announces the run
  // (Main), and a second announcement is exactly what the hook's contract
  // forbids.
  const terminal = isTerminalStage(run.stage);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running && terminal) {
      detach(loadRuns());
      detach(refreshSyncStatsAfterChange());
      detach(refreshSessionBudgetAfterChange());
      // A run has been and gone, so a fresh Force Full Sync would forget
      // something again. Disarmed here rather than on the run's start, because
      // the button is disabled by the run itself for as long as it is going.
      setFullSyncCleared(null);
    }
    wasRunning.current = running;
  }, [running, terminal, loadRuns]);

  // Poll the live renderer-heap reading while it can still change: during a run
  // (so "Steam memory" tracks the climbing RSS) and while the last run is paused
  // (so the card notices once a Steam restart frees memory and `resume_ready`
  // flips). One dumb interval, faster during a run than while merely waiting.
  const lastRunPaused = stats?.last_attempt?.status === "paused";
  useEffect(() => {
    if (!running && !lastRunPaused) return;
    const id = setInterval(
      () => {
        detach(refreshSessionBudget());
        // While the paused card is showing (idle), re-read the stats too so the
        // card — which keys on last_attempt — recovers if the one-shot terminal
        // refetch was ever missed. Then this poll self-stops.
        if (!running) detach(refreshSyncStats());
      },
      running ? BUDGET_POLL_RUNNING_MS : BUDGET_POLL_PAUSED_MS,
    );
    return () => clearInterval(id);
  }, [running, lastRunPaused]);

  // Drive the expiry countdown. The deadline is absolute, so the only thing that
  // has to tick is the current time — mirrored into state here rather than read
  // in render. Torn down when the preview goes.
  const previewDeadline = preview?.expires_at;
  useEffect(() => {
    if (previewDeadline === undefined) return;
    const id = setInterval(() => setNowMs(Date.now()), PREVIEW_COUNTDOWN_TICK_MS);
    return () => clearInterval(id);
  }, [previewDeadline]);

  const secondsLeft = preview === null || nowMs === null ? null : previewSecondsLeft(preview, nowMs);
  const resume = syncResumeState(stats);
  const previewExpired = secondsLeft === 0;
  const cancelling = cancellingRunId !== null && running && (cancellingRunId === "" || cancellingRunId === run.runId);

  return {
    preview,
    previewSecondsLeft: secondsLeft,
    previewExpired,
    run,
    units,
    stats,
    statsFailed,
    budget,
    resume,
    primaryAction: primaryActionFor(preview, previewExpired, resume),
    busy,
    cancelling,
    status,
    optionsStatus,
    skipPreview,
    fullSyncCleared,
    runs,
    runsLoading,
    runsFailed,
    startPreview,
    applyPreview: () => detach(applyPreview()),
    refreshPreview: () => detach(computePreview(true)),
    cancelPreview: () => detach(cancelPreview()),
    cancelRun: () => detach(cancelRun()),
    setSkipPreview: (value: boolean) => detach(changeSkipPreview(value)),
    forceFullSync: () => detach(forceFullSync()),
  };
}
