import { useState, useEffect, useRef, FC, ReactNode } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  Field,
  Focusable,
  ProgressBar,
  Spinner,
  DialogButton,
  ConfirmModal,
  showModal,
} from "@decky/ui";
import { FaCheckCircle, FaTimesCircle, FaExclamationTriangle } from "react-icons/fa";
import {
  cancelSync,
  getSettings,
  fixRetroarchInputDriver,
  startSync,
  refreshMigrationState,
  getSyncStatus,
  getRetroDeckStatus,
  logError,
} from "../api/backend";
import { formatTimeAgo } from "../utils/formatters";
import { pluralize } from "../utils/pluralize";
import { getSyncProgress, setSyncProgress as setStoredSyncProgress } from "../utils/syncProgress";
import { useSyncRunView } from "../utils/syncRunView";
import { useDownloads } from "../utils/downloadStore";
import { usePendingPreview, getPendingPreviewSnapshot, refreshPendingPreview } from "../utils/pendingPreviewStore";
import { previewSecondsLeft } from "../utils/previewState";
import { requestPreviewOnOpen } from "../utils/previewRequest";
import { syncResumeState } from "../utils/syncResume";
import {
  refreshSessionBudget,
  refreshSessionBudgetAfterChange,
  refreshSyncStats,
  refreshSyncStatsAfterChange,
  useSessionBudget,
  useSyncStats,
} from "../utils/syncStatsStore";
import { setMigrationStatus, useMigrationStatus } from "../utils/migrationStore";
import { useSettingsResetState } from "../utils/settingsResetStore";
import { fetchPlaytimeScopeState, usePlaytimeScopeState } from "../utils/playtimeScopeStore";
import { setSaveSortMigrationStatus, useSaveSortMigrationState } from "../utils/saveSortMigrationStore";
import { reconcileStaleShortcuts, requestSyncCancel, resetSyncCancel } from "../utils/syncManager";
import { useConnectionProbe } from "../utils/connectionProbe";
import type { BackendFailed, ConnectionFailure } from "../utils/connectionProbe";
import { retroDeckBanner, type RetroDeckBanner } from "../utils/retrodeckHealth";
import { VersionErrorCard, useVersionError } from "./VersionErrorCard";
import { WarningCard } from "./WarningCard";
import { DownloadProgressRow } from "./DownloadProgressRow";
import { MigrationBlockedPage } from "./MigrationBlockedPage";
import { SettingsResetBanner } from "./SettingsResetBanner";
import { PlaytimeScopeBanner } from "./PlaytimeScopeBanner";
import { formatGb, formatSignedGb, memoryLevelColor } from "./SessionBudgetBanner";
import type { SyncProgress, SyncStats, Page } from "../types";
import { detach } from "../utils/detach";
import { wrapText } from "../utils/textStyles";

interface MainPageProps {
  onNavigate: (page: Exclude<Page, "main">) => void;
}

/** The connection-row label for a failed probe, mapped from the backend's
 *  `{reason, message}`. The two `config_error` sub-cases are split by message
 *  text (the slug is shared). Anything unclassified falls back to the generic
 *  "Not connected". `version_error` is included for completeness even though a
 *  version failure short-circuits the whole panel to the VersionErrorCard. */
function connectionFailureLabel(failure: ConnectionFailure | null | undefined): string {
  const reason = failure?.reason;
  const message = failure?.message ?? "";
  switch (reason) {
    case "auth_failed":
      return "Sign-in rejected";
    case "server_unreachable":
      return "Server unreachable";
    case "version_error":
      return "Unsupported RomM version";
    case "config_error":
      if (/server url/i.test(message)) return "No server URL";
      if (/not signed in/i.test(message)) return "Not signed in";
      return "Not connected";
    default:
      return "Not connected";
  }
}

export const ConnectionIndicator: FC<{
  connected: boolean | null | BackendFailed;
  failure?: ConnectionFailure | null;
}> = ({ connected, failure }) => {
  if (connected === "backend_failed") {
    return (
      <>
        <FaExclamationTriangle style={{ color: "#d4a72c", fontSize: "14px" }} />
        <span style={{ fontSize: "12px" }}>Backend error</span>
      </>
    );
  }
  if (connected === null) {
    return (
      <>
        <Spinner width={14} height={14} />
        <span style={{ fontSize: "12px", opacity: 0.7 }}>Checking...</span>
      </>
    );
  }
  if (connected) {
    return (
      <>
        <FaCheckCircle style={{ color: "#59bf40", fontSize: "14px" }} />
        <span style={{ fontSize: "12px" }}>Connected</span>
      </>
    );
  }
  return (
    <>
      <FaTimesCircle style={{ color: "#d4343c", fontSize: "14px" }} />
      <span style={{ fontSize: "12px" }}>{connectionFailureLabel(failure)}</span>
    </>
  );
};

// The fine-detail line is clamped to two lines (``WebkitLineClamp``) and its box
// is reserved at exactly that height up front. Without the reservation a message
// that wraps 1→2 lines (or shrinks 2→1 at a unit boundary) reflows the ETA row
// and Cancel button below it — the visible jolt of the residual boundary flicker.
// ``minHeight`` in ``em`` ties to the element's own 12px font (``wrapText``), so
// two 1.4-line-height lines reserve 2.8em with no magic pixel value.
const FINE_DETAIL_LINE_HEIGHT = 1.4;
const FINE_DETAIL_CLAMP_LINES = 2;

/** Wall-clock ``HH:MM`` for the "last attempt" hint; the raw ISO on a bad parse. */
function formatClockTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/** The "Last sync" field value: the completed run's relative time on line 1,
 *  and (when a newer attempt did not complete) the attempt on a second
 *  right-aligned line — INSIDE the field, so the focus highlight covers both
 *  lines like the Library row's. Needs ``childrenContainerWidth="max"`` on the
 *  field: the default children column is too narrow and wrapped the attempt
 *  line mid-text. With no completed run ever, the cancelled/crashed attempt is
 *  surfaced as line 1 so it never reads a bare "Never" after thousands of
 *  games synced (#1367); otherwise "Never". */
function lastSyncValue(stats: SyncStats): ReactNode {
  if (stats.last_sync) {
    return (
      <span style={{ fontSize: "12px", display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
        <span>{formatTimeAgo(stats.last_sync) ?? stats.last_sync}</span>
        {stats.last_attempt && (
          <span style={{ opacity: 0.6 }}>
            last attempt: {formatClockTime(stats.last_attempt.finished_at)} ({stats.last_attempt.status})
          </span>
        )}
      </span>
    );
  }
  if (stats.last_attempt) {
    return (
      <span style={{ fontSize: "12px" }}>
        {formatClockTime(stats.last_attempt.finished_at)} ({stats.last_attempt.status})
      </span>
    );
  }
  return <span style={{ fontSize: "12px" }}>Never</span>;
}

/**
 * The Library row's one-line summary — "N games · M platforms · K collections" —
 * each part correctly singular/plural, zero parts omitted. Games is always
 * present (the row renders only when ``roms > 0``).
 */
function formatLibraryLine(stats: SyncStats): string {
  const parts = [pluralize(stats.roms, "game")];
  if (stats.platforms > 0) parts.push(pluralize(stats.platforms, "platform"));
  const collections = stats.collections ?? 0;
  if (collections > 0) parts.push(pluralize(collections, "collection"));
  return parts.join(" · ");
}

/**
 * What the Sync button says while a preview is waiting to be reviewed.
 *
 * The count is what makes the label worth pressing — "Review changes" alone says
 * nothing about whether there is anything in it — but a preview whose delta is
 * pure updates, removals or cover work has no new games to name, and "0 new"
 * would read as "nothing to do" over a run that has plenty. The page behind the
 * button states all of it; this is the part that fits on Main.
 */
function reviewLabel(newCount: number): string {
  return newCount > 0 ? `Review changes · ${newCount} new` : "Review changes";
}

/**
 * Thin horizontal rule dividing the panel's blocks (status | sync | menu).
 * The panel carries no section headings — these rules are the only block
 * boundaries.
 */
const BlockSeparator: FC = () => (
  <PanelSectionRow>
    <div data-testid="block-separator" style={{ height: "1px", backgroundColor: "rgba(255, 255, 255, 0.12)" }} />
  </PanelSectionRow>
);

/** The affirmative green — matches the connection checkmark and the healthy
 *  memory level — used only for a cleanly-finished sync's status line. */
const STATUS_SUCCESS_COLOR = "#59bf40";

/** How long the transient status line lingers before auto-clearing. Kept long
 *  enough to still be readable after a glance away from a just-finished sync. */
const STATUS_CLEAR_MS = 15000;

/** Tone of the transient status line. Only a clean sync finish is affirmative
 *  (green); a cancel/error/other keeps the neutral panel-text look. */
type StatusTone = "success" | "neutral";

interface TransientStatus {
  text: string;
  tone: StatusTone;
}

/** A terminal sync stage's status tone — green only on a clean finish; a
 *  cancel or error stays neutral so green never reads as "all good". */
function terminalStatusTone(stage: SyncProgress["stage"]): StatusTone {
  return stage === "done" ? "success" : "neutral";
}

export const MainPage: FC<MainPageProps> = ({ onNavigate }) => {
  // Both facts are owned by `utils/syncStatsStore.ts`: seven refresh sites in
  // this file ask for them, and the store is what keeps an older answer from
  // landing last and overwriting a newer one. What stays here is the SCHEDULE —
  // the mount burst, the terminal-stage re-read and the poll interval below
  // decide when to ask; the store only owns the data.
  const stats = useSyncStats();
  const budgetStatus = useSessionBudget();
  // `failure` classifies a resolved-but-failed probe so the connection row can
  // show a specific label (auth rejected / server unreachable / no URL / not
  // signed in). Null for every non-failed state and for a probe that never
  // resolved. The probe itself lives outside this component so a QAM close does
  // not abandon a run that has not reached a verdict yet.
  const { connected, failure: connectionFailure } = useConnectionProbe();
  const versionError = useVersionError();
  // Disarmed "Cancelling…" state during the backend's RUNNING→CANCELLING→IDLE
  // drain. The Sync/Cancel button stays disabled until the terminal
  // sync_progress stage re-arms it, so a quick re-press can't hit the
  // sync_in_progress reject and look like an instant finish (#1202, RC-B).
  const [cancelling, setCancelling] = useState(false);
  const [status, setStatus] = useState<TransientStatus | null>(null);
  // The preview lives in a module store, not here: the answer to `sync_preview`
  // is delivered to the instance that pressed Sync, and leaving the main page
  // mid-run unmounts that instance while the run carries on
  // (`utils/pendingPreviewStore.ts` states the whole rule).
  const preview = usePendingPreview();
  // Clock mirror for the preview deadline, stamped by the handlers a preview can
  // reach this instance through and once more by the timer below when the
  // deadline passes — never read during render.
  const [previewNowMs, setPreviewNowMs] = useState<number | null>(null);
  // The persisted Skip-preview intent, whose control is on the Sync page. Main
  // only READS it — the router unmounts the page it leaves, so every return from
  // the Sync page re-runs the mount read below and the value is never stale.
  const [skipPreview, setSkipPreview] = useState(false);
  const [retroarchWarning, setRetroarchWarning] = useState<{ warning: boolean; current?: string } | null>(null);
  const [retrodeckBanner, setRetrodeckBanner] = useState<RetroDeckBanner | null>(null);
  const migration = useMigrationStatus();
  const settingsReset = useSettingsResetState();
  const playtimeScope = usePlaytimeScopeState();
  const saveSortMigration = useSaveSortMigrationState();
  const downloads = useDownloads();
  const statusTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showTransientStatus = (text: string, tone: StatusTone = "neutral") => {
    if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
    setStatus({ text, tone });
    statusTimeoutRef.current = setTimeout(() => setStatus(null), STATUS_CLEAR_MS);
  };

  // The run in flight, read through the shared hook, so the Sync page will show
  // the same derivation of it rather than a second copy. What stays here is what
  // belongs to THIS page: the once-per-run work below, the "Cancelling…" drain,
  // the transient status line and the optimistic start the handlers retract.
  //
  // "A run is in flight" is DERIVED from the run's own frame, never mirrored in
  // a second boolean: DangerZone and RemovedGamesCleanup read the same store
  // field, so a path that ends the run locally without ending it in the store
  // would make the three disagree (#1019). The optimistic start is not lost — a
  // Sync click writes running:true into the store, which the hook feeds straight
  // back.
  const run = useSyncRunView({
    onRunEnd: (progress) => {
      // True terminal reached — re-arm the button out of any "Cancelling…"
      // drain state (#1202, RC-B).
      setCancelling(false);
      showTransientStatus(progress.message || "Sync finished", terminalStatusTone(progress.stage));
      // A preview run that just ended may have staged a snapshot this panel never
      // received: `sync_preview` answers the instance that pressed Sync, and that
      // instance is gone whenever the user left the page mid-run. Ask for it —
      // unless the store already has it, which is the same run answering through
      // the other door. A run that staged nothing (an apply, a cancel, Skip
      // Preview) is answered `preview: null` and the store is left as it stands.
      // Stamp the countdown's clock either way: this is where a preview can
      // appear without this instance adopting it, and the impure now-read
      // belongs in a handler.
      setPreviewNowMs(Date.now());
      if (getPendingPreviewSnapshot() === null) detach(refreshPendingPreview());
      // Both re-reads are provoked by the run ending, so neither may join a read
      // issued while it was still going — see the two AfterChange functions.
      detach(refreshSyncStatsAfterChange());
      // Refresh the live heap reading so the paused / high-heap banner reflects
      // the run's end state (a pause leaves it high; a completed run may too).
      detach(refreshSessionBudgetAfterChange());
    },
    onTerminalWording: (message, stage) => showTransientStatus(message, terminalStatusTone(stage)),
  });
  const syncing = run.running;

  useEffect(() => {
    refreshMigrationState()
      .then(({ retrodeck, save_sort }) => {
        setMigrationStatus(retrodeck);
        setSaveSortMigrationStatus(save_sort);
      })
      .catch((e) => logError(`Failed to refresh migration state: ${e}`));
    detach(refreshSyncStats());
    // Live renderer-heap reading for the session-budget banners (#1383). Fail-open:
    // the backend always resolves (rss_kb null when unreadable), so the banners
    // degrade to text-only rather than erroring.
    detach(refreshSessionBudget());

    getSettings()
      .then((s) => {
        if (s.retroarch_input_check) {
          setRetroarchWarning(s.retroarch_input_check);
        }
        setSkipPreview(s.skip_preview ?? false);
      })
      .catch((e) => logError(`Failed to load settings: ${e}`));

    // RetroDECK path-resolution health — warn the user when the resolved roots
    // are likely wrong (retrodeck.json unreadable, or its home missing on
    // disk). "ok"/"absent" stay quiet (banner cleared to null).
    getRetroDeckStatus()
      .then((s) => setRetrodeckBanner(retroDeckBanner(s.status, s)))
      .catch((e) => logError(`Failed to query RetroDECK status: ${e}`));

    // The backend holds a computed preview for 30 minutes, but this panel's copy
    // of it dies with the render — leaving the main page for a submenu used to
    // strand a preview that was still perfectly appliable. Ask for it back on
    // every mount.
    // The store decides whether the answer still stands: it loses to anything the
    // user answered while it was open, and its own failure is logged there.
    // Stamping the countdown's clock is this side's job — an impure read, so it
    // belongs in a handler rather than in render or in the interval's effect.
    detach(refreshPendingPreview().then(() => setPreviewNowMs(Date.now())));

    // Cross-device playtime scope notice. The backend sets a durable flag when a
    // playtime reconcile is rejected for a token missing `roms.user.read`; it
    // self-clears once a scoped token is minted, so we re-read it on every mount.
    fetchPlaytimeScopeState().catch((e) => logError(`Failed to check playtime scope notice: ${e}`));

    // Backend is authoritative for in-flight sync state. Seed the module
    // store from get_sync_status() so a QAM close/reopen recovers the live
    // run rather than guessing from the event-fed store alone.
    //
    // But the backend snapshot is COARSE mid-apply: the fine within-unit
    // counters (current/total/message) are advanced frontend-side per item and
    // never round-trip to the backend, and etaSeconds is frontend-computed from
    // sync_plan (never sent by the backend). A blind replace on remount would
    // wipe the fine line + ETA until the next chunk boundary. So when the
    // backend reports the SAME in-flight run the module store already tracks,
    // MERGE: keep the store's fine fields + etaSeconds, take the backend's
    // authoritative running/stage/runId. Run identity is compared via runId when
    // both sides carry it; when the backend is idle or the runs differ, keep the
    // replace behavior (the store holds nothing worth preserving).
    const storeAtIssue = getSyncProgress();
    getSyncStatus()
      .then(({ inFlight, ...backendProgress }) => {
        const stored = getSyncProgress();
        // An answer reporting nothing in flight has no authority over a write
        // that landed after the read was issued: a Sync click in that window
        // writes the optimistic running:true, and this snapshot was taken before
        // it existed, so applying it would retract a run that has just started
        // (#751). The store's own frames then carry the run from here.
        if (!backendProgress.running && stored !== storeAtIssue) return;
        // Retracting a run the store is tracking is a separate question from
        // reading the frame, and the answer carries both. `inFlight` is the
        // backend's run-lifecycle state; the frame is the last thing a run said.
        // Between pressing Sync and the new run's first frame — a reconcile plus
        // a round trip — the frame belongs to the PREVIOUS run, so taking it for
        // this one both retracted the run the panel was showing (idle buttons
        // over a live preview) and handed the subscriber below a terminal stage
        // it announced as this run's ending.
        //
        // A retraction is therefore allowed only where the answer is evidence
        // about the run the store is tracking, which is one of two things: the
        // answer NAMES that run (its own ending, e.g. a terminal frame this panel
        // missed while it was away), or the backend reports the lifecycle
        // explicitly idle AND the store's run carries a backend-stamped id, so it
        // is a run the backend has seen and is now telling us is over. That
        // second clause is what keeps a lost terminal frame from wedging the
        // panel on a run that is not running: the next mount corrects it.
        //
        // What is refused is an idle answer against the panel's OWN optimistic
        // start, which carries no run id because the backend has not stamped one
        // — there the backend is not silent about the run, it has not heard of it
        // yet. That frame is not a wedge: the handler that wrote it always
        // retracts it itself (a preview answered, a failure aborted, or a
        // per-unit run whose frames stamp a real id), even from an instance the
        // user has already navigated away from.
        const backendRunIdle = inFlight === false;
        const namesStoredRun = !!stored.runId && stored.runId === backendProgress.runId;
        const backendKnowsStoredRun = backendRunIdle && !!stored.runId;
        if (!backendProgress.running && stored.running && !namesStoredRun && !backendKnowsStoredRun) return;
        const sameRun = backendProgress.runId && stored.runId ? backendProgress.runId === stored.runId : true;
        const isSameLiveRun = backendProgress.running && stored.running && sameRun;
        // Same live run: spread the store (keeping its fine fields + etaSeconds)
        // and overlay the backend's authoritative running/stage/runId. The
        // conditional spreads keep the optional stage/runId out when the backend
        // omits them (exactOptionalPropertyTypes). One exception: "applying" is
        // frontend-authoritative (the backend never emits it — its last frame is
        // the fetch anchor), so a stored applying stage survives the seed; taking
        // the backend's stale "fetching" would drop the coarse-bar interpolation
        // and flip the label until the next per-item update. Otherwise replace
        // wholesale.
        const backendStage = stored.stage === "applying" ? undefined : backendProgress.stage;
        const progress: SyncProgress = isSameLiveRun
          ? {
              ...stored,
              running: backendProgress.running,
              ...(backendStage !== undefined ? { stage: backendStage } : {}),
              ...(backendProgress.runId !== undefined ? { runId: backendProgress.runId } : {}),
            }
          : backendProgress;
        setStoredSyncProgress(progress);
      })
      .catch((e) => logError(`Failed to query sync status: ${e}`));

    return () => {
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
    };
  }, []);

  // Whether a preview is worth offering a review of. A run in flight owns the
  // panel, so one held while a run is going does not show — the store keeps it
  // and the button comes back the moment the run ends. **Main never DISCARDS
  // one**: a preview ends only on the Sync page, through Apply, Cancel or
  // Refresh.
  const pendingPreview = syncing ? null : preview;
  // An expired preview counts as none — the backend drops one past its TTL, so
  // the button would otherwise offer a review of something it will refuse.
  //
  // What ticks is a single timer aimed at the deadline rather than a per-second
  // interval: nothing on Main counts a preview down (the countdown belongs to
  // the Sync page), so the only moment the clock changes anything here is the
  // one the label flips at. The clock itself is stamped by the handlers a
  // preview can reach this instance through — the mount read and the terminal
  // frame — and never read in render, which must stay pure.
  const previewExpired =
    pendingPreview !== null && previewNowMs !== null && previewSecondsLeft(pendingPreview, previewNowMs) === 0;
  const previewDeadline = pendingPreview?.expires_at;
  useEffect(() => {
    if (previewDeadline === undefined) return;
    const untilExpiry = previewDeadline * 1000 - Date.now();
    if (untilExpiry <= 0) return;
    const timer = setTimeout(() => setPreviewNowMs(Date.now()), untilExpiry);
    return () => clearTimeout(timer);
  }, [previewDeadline]);

  // Poll the live renderer-heap reading while it can still change: during a sync (so
  // the "Steam memory" row tracks the climbing RSS mid-apply) AND while the last run
  // is paused (so the paused banner notices once a Steam restart frees memory and
  // ``resume_ready`` flips — otherwise it sits stale after the restart). One dumb
  // interval, faster during a sync than while merely waiting for a restart; torn down
  // when neither condition holds or on unmount.
  const lastRunPaused = stats?.last_attempt?.status === "paused";
  useEffect(() => {
    if (!syncing && !lastRunPaused) return;
    const id = setInterval(
      () => {
        detach(refreshSessionBudget());
        // Belt-and-braces on top of the backend emit-last fix (#39): while the paused
        // banner is showing (idle), also re-read stats so the "Last sync" line + the
        // paused banner (which keys on last_attempt) recover if the one-shot terminal
        // refetch was ever missed/dropped. Then this poll self-stops (last_attempt is
        // no longer paused).
        if (!syncing) {
          detach(refreshSyncStats());
        }
      },
      syncing ? 5000 : 10000,
    );
    return () => clearInterval(id);
  }, [syncing, lastRunPaused]);

  // A start/apply call never reached a running backend sync (rejected up front or
  // threw). Nothing is draining, so the optimistic running:true is retracted from
  // the store — which is also what returns this panel to its idle body.
  const abortOptimisticSync = (msg: string) => {
    setStatus({ text: msg, tone: "neutral" });
    setCancelling(false);
    setStoredSyncProgress({ running: false, stage: "" });
  };

  /**
   * The Sync button's press, in its two working states.
   *
   * **The preview is not computed here.** The press starts one and opens the
   * Sync page, and that page is what issues the call — so the progress, the
   * answer and, above all, a refusal are reported where the reader now is. This
   * instance is unmounted a moment later, and a `@migration_blocked` refusal or
   * a server that went away in between would land on nobody.
   *
   * With Skip preview on there is no preview to open a page for: the run starts
   * here and Main shows it, exactly as it does today.
   */
  const handleSync = async () => {
    // Clear any stale cancel flag BEFORE this sync starts, so a fresh sync never
    // begins pre-cancelled (#1198). Run identity for a Cancel click comes from
    // the backend-fed sync_progress store (#1202).
    resetSyncCancel();
    setCancelling(false);
    setStatus(null);
    if (!skipPreview) {
      requestPreviewOnOpen();
      onNavigate("sync");
      return;
    }
    // Optimistically disable the button and show the in-progress UI before the
    // backend's first sync_progress event lands — writing running:true into the
    // MODULE store (the single source of truth the subscription reads), not a
    // shadowing local state.
    setStoredSyncProgress({ running: true, stage: "fetching", message: "Fetching library..." });
    try {
      // Reconcile shortcuts the user deleted via Steam's own UI BEFORE the work
      // queue is built: unbind any dead binding so the incremental skip
      // re-fetches the platform and recreates the missing shortcut (#1046).
      // Best-effort — never blocks the sync.
      await reconcileStaleShortcuts();
      // Skip Preview takes the per-unit pipeline (start_sync) — incremental
      // shortcut delivery, per-unit crash safety, no upfront full library fetch.
      const startResult = await startSync();
      if (!startResult.success) {
        abortOptimisticSync(startResult.message);
      }
      // On success the store subscription drives the UI from here.
    } catch {
      abortOptimisticSync("Failed to start sync");
    }
  };

  const handleCancel = async () => {
    // No preview branch here. This handler is wired to one button — "Cancel
    // Sync", in the body a run in flight owns — so a preview cannot be what the
    // press is about; ending one is the Sync page's business entirely. A branch
    // reading the STORE's preview would fire exactly where the store holds one
    // while a run is going, discarding it and leaving the run untouched with no
    // other way out of the syncing body.
    //
    // RC-B (#1202): do NOT re-arm the Sync button here. The backend drains
    // RUNNING → CANCELLING → IDLE asynchronously; flipping back to enabled now
    // lets a quick re-press hit the sync_in_progress reject and look like an
    // "instant finish". Disarm into the "Cancelling…" state and wait for the
    // terminal sync_progress stage (the store subscription) to re-arm.
    setCancelling(true);
    requestSyncCancel();
    try {
      // Scope the cancel to the active run via the backend-fed run id; "" in the
      // pre-progress window → the backend's unconditional cancel (#1202).
      await cancelSync(getSyncProgress().runId ?? "");
      // Success: stay disarmed; the terminal stage tears the UI down and
      // re-arms, surfacing the backend's final message — no status here, so no
      // instant-finish flash during the drain.
    } catch {
      // The cancel call itself failed — no terminal will arrive from a cancel that
      // never landed, so re-arm the button and surface the failure for a retry. The
      // run is NOT ended here: a cancel that never reached the backend leaves it
      // draining or still working, and claiming otherwise would both re-offer a
      // Sync button that only earns a sync_in_progress reject and tell the other
      // readers of the store that a live run had stopped (#1019).
      setCancelling(false);
      showTransientStatus("Failed to cancel sync");
    }
  };

  const activeDownloads = downloads.filter((d) => d.status === "queued" || d.status === "downloading");
  const completedDownloads = downloads.filter(
    (d) => d.status === "completed" || d.status === "failed" || d.status === "cancelled",
  );
  const hasDownloads = activeDownloads.length > 0 || completedDownloads.length > 0;

  // Sync is unavailable when the server test failed OR the plugin backend never
  // started — both gate the Sync buttons off.
  const connectionUnavailable = connected === false || connected === "backend_failed";

  // What the sync button is called and whether pressing it continues a run.
  // Derived in `utils/syncResume.ts` because the Sync page names its own start
  // button — and the session-budget card there — from the same answer.
  const resume = syncResumeState(stats);

  if (versionError) {
    return <VersionErrorCard message={versionError} compact />;
  }

  if (migration.pending) {
    return <MigrationBlockedPage migration={migration} />;
  }

  let syncBody: ReactNode;
  if (syncing) {
    const stepText = run.totalSteps ? `${run.step}/${run.totalSteps}` : "";
    syncBody = (
      <>
        <PanelSectionRow>
          {/* Own the caption in a full-width row and use the bare ProgressBar.
              ProgressBarWithInfo is a Steam Field (label column | bar column);
              with no label text the empty column shoves the bar into the right
              half and clips it (#751). The bare ProgressBar is just the bar and
              spans the full panel width. NOT focusable: the syncing rows sit
              between the focusable status rows and the Cancel button, so they
              are always in view — a focus highlight here only fakes
              interactivity. */}
          <div style={{ width: "100%" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "12px",
                marginBottom: "4px",
              }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                {/* During silent phases (the initial "Fetching library" anchor
                    frame, before any narrated fine-detail page frame) there is
                    no fine line to carry a spinner, so the panel looks hung.
                    Show the spinner inline with the stage label so a running
                    sync always has motion. When the fine line is present it
                    already has its own spinner — don't show two. */}
                {!run.hasFineDetail && <Spinner width={14} height={14} />}
                <span data-testid="sync-stage">{run.stageLabel}</span>
              </span>
              {stepText && <span data-testid="sync-step">{stepText}</span>}
            </div>
            <ProgressBar
              indeterminate={run.coarseFraction === undefined}
              {...(run.coarseFraction !== undefined ? { nProgress: run.coarseFraction } : {})}
            />
          </div>
        </PanelSectionRow>
        {run.hasFineDetail && (
          <PanelSectionRow>
            <Field
              bottomSeparator="none"
              label={
                <div style={{ display: "flex", alignItems: "flex-start", gap: "8px" }}>
                  <Spinner width={14} height={14} />
                  {/* Wrap the narrated messages ("Fetching Game Boy Advance
                      (page 4/62)") on word boundaries instead of clipping them
                      mid-parenthesis (shared wrap rule). The clamp caps this
                      live-updating line at two lines so a long platform name
                      can't grow the row unboundedly. */}
                  <span
                    data-testid="sync-fine"
                    style={{
                      ...wrapText,
                      display: "-webkit-box",
                      WebkitLineClamp: FINE_DETAIL_CLAMP_LINES,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                      lineHeight: FINE_DETAIL_LINE_HEIGHT,
                      // Reserve the full two-line clamp box so a 1↔2-line wrap
                      // change never reflows the ETA row / Cancel button below.
                      minHeight: `${FINE_DETAIL_CLAMP_LINES * FINE_DETAIL_LINE_HEIGHT}em`,
                    }}
                  >
                    {run.fineDetailText}
                  </span>
                </div>
              }
            />
          </PanelSectionRow>
        )}
        {run.etaText !== null && (
          <PanelSectionRow>
            <Field label="Estimated time" bottomSeparator="none">
              <span data-testid="estimate-time" style={{ fontSize: "12px" }}>
                {run.etaText}
              </span>
            </Field>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            bottomSeparator="none"
            disabled={cancelling}
            onClick={() => {
              detach(handleCancel());
            }}
          >
            {cancelling ? "Cancelling…" : "Cancel Sync"}
          </ButtonItem>
        </PanelSectionRow>
      </>
    );
  } else {
    // The idle body. A run in flight renders the branch above instead, so nothing
    // here needs an "already syncing" guard — the button only ever exists while
    // there is no run to collide with, and the connection is all that can gate it.
    //
    // One button, four states (docs/architecture/qam-panel.md, section Main).
    // Two of them are here: a pending preview is a review the Sync page holds,
    // and everything else starts one. The other two are the branch above (a run
    // in flight) and the resume, which is this same press under another name.
    syncBody = (
      <>
        {pendingPreview && !previewExpired ? (
          <PanelSectionRow>
            <ButtonItem layout="below" bottomSeparator="none" onClick={() => onNavigate("sync")}>
              {reviewLabel(pendingPreview.summary.new_count)}
            </ButtonItem>
          </PanelSectionRow>
        ) : (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              bottomSeparator="none"
              onClick={() => {
                detach(handleSync());
              }}
              disabled={connectionUnavailable}
              description={resume.scopeText ?? undefined}
            >
              {resume.label}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </>
    );
  }

  return (
    <>
      {settingsReset.pending && <SettingsResetBanner backedUpTo={settingsReset.backedUpTo} />}
      {playtimeScope.pending && <PlaytimeScopeBanner />}
      {/* Untitled status block (Connection / Last sync / Library / Steam memory)
          leads the panel — the following PanelSection titles provide the block
          breaks, so no "Status" title is needed. */}
      <PanelSection>
        {retrodeckBanner && (
          <PanelSectionRow>
            {/* WarningCard is shared with the game-detail context, so it carries no
                focusable child of its own — wrap it here (QAM-only) so gamepad focus
                can reach it. */}
            <Focusable>
              <WarningCard title={retrodeckBanner.title} message={retrodeckBanner.message} compact />
            </Focusable>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <Field
            label="Connection"
            focusable={true}
            bottomSeparator="none"
            description={
              connected === "backend_failed" ? "Plugin backend failed to start — check Decky logs." : undefined
            }
          >
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <ConnectionIndicator connected={connected} failure={connectionFailure} />
            </div>
          </Field>
        </PanelSectionRow>
        {stats && (
          <>
            <PanelSectionRow>
              {/* A stop that ACTS: the run this line reports, the history it
                  belongs to and the preview that would change it are all on the
                  Sync page, so the row is the second way in beside the button. */}
              <Field
                label="Last sync"
                focusable={true}
                onActivate={() => onNavigate("sync")}
                bottomSeparator="none"
                childrenContainerWidth="max"
              >
                {lastSyncValue(stats)}
              </Field>
            </PanelSectionRow>
            {stats.roms > 0 && (
              <PanelSectionRow>
                <Field
                  label="Library"
                  description={
                    <div style={{ width: "100%", textAlign: "right", fontSize: "12px" }}>
                      {formatLibraryLine(stats)}
                    </div>
                  }
                  focusable={true}
                  bottomSeparator="none"
                />
              </PanelSectionRow>
            )}
          </>
        )}
        {/* Steam renderer memory (#1383): the live RSS as an always-on info row,
            plus the last completed sync's signed growth. Omitted entirely when the
            reading is unavailable (rss_kb null) rather than shown as a blank. */}
        {budgetStatus?.rss_kb != null && (
          <PanelSectionRow>
            <Field label="Steam memory" focusable={true} bottomSeparator="none">
              <span data-testid="steam-memory" style={{ fontSize: "12px" }}>
                {/* Only the value gets traffic-light colouring (green/yellow/red),
                    driven by the payload thresholds; the delta stays muted. Both sit
                    on one line: "0.6 GB · last run +0.7". */}
                <span
                  data-testid="steam-memory-value"
                  style={{
                    color: memoryLevelColor(budgetStatus.rss_kb, budgetStatus.warn_kb, budgetStatus.ceiling_kb),
                  }}
                >
                  {formatGb(budgetStatus.rss_kb)}
                </span>
                {budgetStatus.memory_delta_kb != null && (
                  <span data-testid="steam-memory-delta" style={{ opacity: 0.6 }}>
                    {" · last run "}
                    {formatSignedGb(budgetStatus.memory_delta_kb)}
                  </span>
                )}
              </span>
            </Field>
          </PanelSectionRow>
        )}
        {retroarchWarning?.warning && (
          <PanelSectionRow>
            <Field
              label="RetroArch: input_driver issue"
              description={`Using "${retroarchWarning.current}"`}
              bottomSeparator="none"
            >
              <DialogButton
                onClick={() =>
                  showModal(
                    <ConfirmModal
                      strTitle="Fix RetroArch input_driver?"
                      strDescription="This will change input_driver to sdl2 in your RetroArch config. Controllers should work better in RetroArch menus after this change."
                      strOKButtonText="Apply Fix"
                      strCancelButtonText="Cancel"
                      onOK={() => {
                        detach(
                          (async () => {
                            try {
                              const result = await fixRetroarchInputDriver();
                              if (result.success) {
                                setRetroarchWarning(null);
                              }
                            } catch {
                              // ignore
                            }
                          })(),
                        );
                      }}
                    />,
                  )
                }
              >
                Fix
              </DialogButton>
            </Field>
          </PanelSectionRow>
        )}
        {saveSortMigration.pending && (
          <>
            <PanelSectionRow>
              <Focusable>
                <div
                  style={{
                    padding: "8px 12px",
                    backgroundColor: "rgba(212, 167, 44, 0.15)",
                    borderLeft: "3px solid #d4a72c",
                    borderRadius: "4px",
                    fontSize: "12px",
                  }}
                >
                  <div style={{ fontWeight: "bold", color: "#d4a72c", marginBottom: "4px" }}>
                    {"\u26A0\uFE0F"} RetroArch save sorting changed
                  </div>
                  <div style={{ color: "rgba(255, 255, 255, 0.7)" }}>
                    {saveSortMigration.saves_count ?? 0} save file(s) to migrate
                  </div>
                </div>
              </Focusable>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" bottomSeparator="none" onClick={() => onNavigate("settings")}>
                Go to Settings
              </ButtonItem>
            </PanelSectionRow>
          </>
        )}
        {/* A notice, not the card: Restart Steam now and the resume live on the
            Sync page, and a condition's action exists only at its home. */}
        {lastRunPaused && (
          <>
            <PanelSectionRow>
              <Focusable>
                <div
                  data-testid="sync-paused-notice"
                  style={{
                    padding: "8px 12px",
                    backgroundColor: "rgba(61, 157, 246, 0.15)",
                    borderLeft: "3px solid #3d9df6",
                    borderRadius: "4px",
                    fontSize: "12px",
                  }}
                >
                  <div style={{ fontWeight: "bold", color: "#3d9df6", marginBottom: "4px" }}>Sync paused</div>
                  <div style={{ color: "rgba(255, 255, 255, 0.7)" }}>
                    The last run stopped at a safe point to protect Steam&apos;s memory. Restarting Steam and resuming
                    it are on the Sync page.
                  </div>
                </div>
              </Focusable>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" bottomSeparator="none" onClick={() => onNavigate("sync")}>
                Open Sync
              </ButtonItem>
            </PanelSectionRow>
          </>
        )}
        <BlockSeparator />
      </PanelSection>

      <PanelSection>
        {syncBody}
        {/* Not gated on the run being idle: a cancel whose CALL failed leaves the
            run in flight, and its "Failed to cancel sync" line has to reach the
            user under the progress rows or it is lost entirely. Every other status
            is set by a path that has already ended its run — but a status OUTLIVES
            the run that set it (15s), so the two paths that start a run clear it
            rather than let it ride into the next one. */}
        {status?.text && (
          <PanelSectionRow>
            <Field
              label={
                <span
                  data-testid="sync-status"
                  style={{
                    ...wrapText,
                    ...(status.tone === "success" ? { color: STATUS_SUCCESS_COLOR } : {}),
                  }}
                >
                  {status.text}
                </span>
              }
              focusable={true}
              bottomSeparator="none"
            />
          </PanelSectionRow>
        )}
        <BlockSeparator />
      </PanelSection>

      {hasDownloads && (
        <PanelSection>
          {activeDownloads.slice(0, 2).map((item) => (
            <DownloadProgressRow
              key={item.rom_id}
              caption={item.rom_name}
              bytesDownloaded={item.bytes_downloaded}
              totalBytes={item.total_bytes}
            />
          ))}
          {activeDownloads.length > 2 && (
            <PanelSectionRow>
              <Field
                label={`+${activeDownloads.length - 2} more downloading`}
                focusable={true}
                bottomSeparator="none"
              />
            </PanelSectionRow>
          )}
          {completedDownloads.length > 0 && (
            <PanelSectionRow>
              {/* Self-describing — the downloads block carries no heading, so a
                  bare "1 completed" floats without context. */}
              <Field
                label={`${pluralize(completedDownloads.length, "download")} completed`}
                focusable={true}
                bottomSeparator="none"
              />
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem layout="below" bottomSeparator="none" onClick={() => onNavigate("downloads")}>
              View All
            </ButtonItem>
          </PanelSectionRow>
          <BlockSeparator />
        </PanelSection>
      )}

      <PanelSection>
        <PanelSectionRow>
          <ButtonItem layout="below" bottomSeparator="none" onClick={() => onNavigate("sync")}>
            Sync
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" bottomSeparator="none" onClick={() => onNavigate("library")}>
            Library
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" bottomSeparator="none" onClick={() => onNavigate("settings")}>
            Settings
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" bottomSeparator="none" onClick={() => onNavigate("data")}>
            Data Management
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
