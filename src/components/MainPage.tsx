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
  refreshMigrationState,
  getSyncStatus,
  getRetroDeckStatus,
  logError,
} from "../api/backend";
import { ENTRY_STOP_ATTR } from "../utils/entryFocus";
import { formatTimeAgo } from "../utils/formatters";
import { pluralize } from "../utils/pluralize";
import { getSyncProgress, setSyncProgress as setStoredSyncProgress } from "../utils/syncProgress";
import { useSyncRunView } from "../utils/syncRunView";
import { useDownloads } from "../utils/downloadStore";
import { usePendingPreview, getPendingPreviewSnapshot, refreshPendingPreview } from "../utils/pendingPreviewStore";
import { previewSecondsLeft } from "../utils/previewState";
import { refreshSyncStats, refreshSyncStatsAfterChange, useSyncStats } from "../utils/syncStatsStore";
import { setMigrationStatus, useMigrationStatus } from "../utils/migrationStore";
import { useSettingsResetState } from "../utils/settingsResetStore";
import { fetchPlaytimeScopeState, usePlaytimeScopeState } from "../utils/playtimeScopeStore";
import { setSaveSortMigrationStatus, useSaveSortMigrationState } from "../utils/saveSortMigrationStore";
import { requestSyncCancel } from "../utils/syncManager";
import { useConnectionProbe } from "../utils/connectionProbe";
import type { BackendFailed, ConnectionFailure } from "../utils/connectionProbe";
import { retroDeckBanner, type RetroDeckBanner } from "../utils/retrodeckHealth";
import { VersionErrorCard, useVersionError } from "./VersionErrorCard";
import { WarningCard } from "./WarningCard";
import { DownloadProgressRow } from "./DownloadProgressRow";
import { MigrationBlockedPage } from "./MigrationBlockedPage";
import { SettingsResetBanner } from "./SettingsResetBanner";
import { LegacyInstallNotice } from "./LegacyInstallBanner";
import { DataLocationNotice } from "./DataLocationNotice";
import { PlaytimeScopeBanner } from "./PlaytimeScopeBanner";
import type { SyncPreview, SyncProgress, SyncRunKind, SyncStats, Page } from "../types";
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

/** How the newest run that did not complete is stated — its outcome and its age,
 *  "cancelled 12m ago", so the row reports what happened and not only when. The
 *  raw timestamp stands in where it cannot be parsed. */
function lastAttemptLine(attempt: NonNullable<SyncStats["last_attempt"]>): string {
  return `${attempt.status} ${formatTimeAgo(attempt.finished_at) ?? attempt.finished_at}`;
}

/** The "Last sync" field value: the completed run's relative time on line 1,
 *  and (when a newer attempt did not complete) that attempt's outcome on a
 *  second right-aligned line — INSIDE the field, so the focus highlight covers
 *  both lines like the Library row's. Needs ``childrenContainerWidth="max"`` on
 *  the field: the default children column is too narrow and wrapped the attempt
 *  line mid-text. With no completed run ever, the cancelled/crashed attempt is
 *  surfaced as line 1 so it never reads a bare "Never" after thousands of games
 *  synced (#1318); otherwise "Never".
 *
 *  An errored run is reported here like any other, which is a different question
 *  from whether it can be continued — ``syncResumeState`` refuses that one, and
 *  the button that offers it is the Sync page's. */
function lastSyncValue(stats: SyncStats): ReactNode {
  if (stats.last_sync) {
    return (
      <span style={{ fontSize: "12px", display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
        <span>{formatTimeAgo(stats.last_sync) ?? stats.last_sync}</span>
        {stats.last_attempt && <span style={{ opacity: 0.6 }}>{lastAttemptLine(stats.last_attempt)}</span>}
      </span>
    );
  }
  if (stats.last_attempt) {
    return <span style={{ fontSize: "12px" }}>{lastAttemptLine(stats.last_attempt)}</span>;
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

/** Whether the preview's work includes a collection the sync would add, drop or
 *  re-populate — read off the two diffs `previewHasChanges` reads for the same
 *  question, so the slot and the Apply button cannot disagree about it. */
function previewMovesACollection(summary: SyncPreview["summary"]): boolean {
  return (
    !!(summary.collection_diff?.added.length || summary.collection_diff?.removed.length) ||
    !!summary.platform_collection_diff?.has_changes
  );
}

/**
 * What the conditional slot states about a preview waiting to be reviewed: its
 * counts, in the same three words the page's own table column headings use.
 *
 * A zero part is dropped rather than shown, and a preview with none of the three
 * names the work it does hold instead of showing a row of zeros — there being
 * exactly one line to spend, and that being the case where the counts cannot
 * spend it. Two of the three such previews can be named: a collection that
 * moved, and cover work. **The collection wins where a preview is both**,
 * because it is the one whose result the reader will see in Steam. What is left
 * keeps the plain invitation: a platform re-stamp, which has nothing a reader
 * would recognise to name, and a genuinely empty delta, which has nothing at
 * all. Neither may be called cover work, and the page behind the slot is where
 * it is said which of them it is.
 */
function previewCountsLine(preview: SyncPreview): string {
  const s = preview.summary;
  const parts: string[] = [];
  if (s.new_count > 0) parts.push(`${s.new_count} new`);
  if (s.changed_count > 0) parts.push(`${s.changed_count} updated`);
  if (s.remove_count > 0) parts.push(`${s.remove_count} removed`);
  if (parts.length > 0) return parts.join(" · ");
  if (previewMovesACollection(s)) return "collection changes";
  return (s.cover_refresh_count ?? 0) > 0 ? "cover work only" : "ready to review";
}

/**
 * Thin horizontal rule dividing the panel's blocks (status | downloads | menu).
 * Those three carry no section title, so a rule is the only thing marking where
 * one ends and the next begins. (The two notice sections above them are titled.)
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

/** How often the stats are re-read while the last run is paused. */
const PAUSED_STATS_POLL_MS = 10000;

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

/** What the conditional slot under the status rows says while it exists: what is
 *  happening, the number that goes with it, and whether a bar belongs under it
 *  (a run has one; a preview waiting to be answered is not moving). */
interface StatusSlot {
  label: string;
  value: string;
  bar: boolean;
}

/** The short label for a run in flight, one per kind the backend states. */
const RUN_KIND_LABEL: Record<SyncRunKind, string> = {
  preview: "Checking for changes",
  apply: "Syncing",
};

/** What the slot calls a run whose kind nothing has established — neither of the
 *  two real answers. Reachable only if a frame arrives without the key, which
 *  both halves of this shipping together make unreachable in practice; the
 *  wording exists so the mapping cannot invent one of the two to fill the hole. */
const RUN_KIND_UNKNOWN_LABEL = "Sync in progress";

/** Whether a `get_sync_status` answer may be written over the store at all.
 *  `storeAtIssue` is the frame the store held when the read was issued;
 *  `stored`, the frame it holds now. */
function snapshotHasAuthority(
  backendProgress: SyncProgress,
  inFlight: boolean | undefined,
  stored: SyncProgress,
  storeAtIssue: SyncProgress,
): boolean {
  // An answer reporting nothing in flight has no authority over a write
  // that landed after the read was issued: a run started in that window
  // writes the optimistic running:true, and this snapshot was taken before
  // it existed, so applying it would retract a run that has just started
  // (#751). The store's own frames then carry the run from here.
  if (!backendProgress.running && stored !== storeAtIssue) return false;
  // Retracting a run the store is tracking is a separate question from
  // reading the frame, and the answer carries both. `inFlight` is the
  // backend's run-lifecycle state; the frame is the last thing a run said.
  // Between a run being started and its first frame — a reconcile plus a
  // round trip — the frame belongs to the PREVIOUS run, so taking it for
  // this one both retracted the run the panel was showing (an idle page
  // over a live preview) and handed the run view a terminal stage it
  // announced as this run's ending.
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
  // What is refused is an idle answer against an optimistic start, which
  // carries no run id because the backend has not stamped one — there the
  // backend is not silent about the run, it has not heard of it yet. That
  // frame is not a wedge: the Sync page handler that wrote it always
  // retracts it itself (a preview answered, a failure aborted, or a
  // per-unit run whose frames stamp a real id), even from an instance the
  // user has already navigated away from.
  const backendRunIdle = inFlight === false;
  const namesStoredRun = !!stored.runId && stored.runId === backendProgress.runId;
  const backendKnowsStoredRun = backendRunIdle && !!stored.runId;
  if (!backendProgress.running && stored.running && !namesStoredRun && !backendKnowsStoredRun) return false;
  return true;
}

/**
 * The frame the seed writes over the store.
 *
 * The backend snapshot is COARSE mid-apply: the fine within-unit counters
 * (current/total/message) are advanced frontend-side per item and never
 * round-trip to the backend, and etaSeconds is frontend-computed from sync_plan
 * (never sent by the backend). A blind replace on remount would wipe the fine
 * line + ETA until the next chunk boundary. So when the backend reports the SAME
 * in-flight run the module store already tracks, MERGE: keep the store's fine
 * fields + etaSeconds, take the backend's authoritative running/stage/runId. Run
 * identity is compared via runId when both sides carry it; when the backend is
 * idle or the runs differ, keep the replace behavior (the store holds nothing
 * worth preserving).
 */
function seededFrame(backendProgress: SyncProgress, stored: SyncProgress): SyncProgress {
  const sameRun = backendProgress.runId && stored.runId ? backendProgress.runId === stored.runId : true;
  const isSameLiveRun = backendProgress.running && stored.running && sameRun;
  // Same live run: spread the store (keeping its fine fields + etaSeconds)
  // and overlay the backend's authoritative running/stage/runId/runKind.
  // The conditional spreads keep the optional three out when the backend
  // omits them (exactOptionalPropertyTypes). `runKind` is overlaid for the
  // same reason `runId` is: the backend states it, and the frame this
  // merges over can be an optimistic start written before the run was
  // claimed. One exception: "applying" is frontend-authoritative (the
  // backend never emits it — its last frame is the fetch anchor), so a
  // stored applying stage survives the seed; taking the backend's stale
  // "fetching" would drop the coarse-bar interpolation and flip the label
  // until the next per-item update. Otherwise replace wholesale.
  const backendStage = stored.stage === "applying" ? undefined : backendProgress.stage;
  return isSameLiveRun
    ? {
        ...stored,
        running: backendProgress.running,
        ...(backendStage !== undefined ? { stage: backendStage } : {}),
        ...(backendProgress.runId !== undefined ? { runId: backendProgress.runId } : {}),
        ...(backendProgress.runKind !== undefined ? { runKind: backendProgress.runKind } : {}),
      }
    : backendProgress;
}

export const MainPage: FC<MainPageProps> = ({ onNavigate }) => {
  // The stats are owned by `utils/syncStatsStore.ts`: three refresh sites in
  // this file ask for them, and the store is what keeps an older answer from
  // landing last and overwriting a newer one. What stays here is the SCHEDULE —
  // the mount burst, the terminal-stage re-read and the poll interval below
  // decide when to ask; the store only owns the data.
  const stats = useSyncStats();
  // `failure` classifies a resolved-but-failed probe so the connection row can
  // show a specific label (auth rejected / server unreachable / no URL / not
  // signed in). Null for every non-failed state and for a probe that never
  // resolved. The probe itself lives outside this component so a QAM close does
  // not abandon a run that has not reached a verdict yet.
  const { connected, failure: connectionFailure } = useConnectionProbe();
  const versionError = useVersionError();
  // Disarmed "Cancelling…" state during the backend's RUNNING→CANCELLING→IDLE
  // drain. The Cancel button stays disabled until the terminal sync_progress
  // stage re-arms it, so a quick re-press can't hit the sync_in_progress reject
  // and look like an instant finish (#1202, RC-B).
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

  // The run in flight, read through the shared hook, so the Sync page shows the
  // same derivation of it rather than a second copy. What stays here is what
  // belongs to THIS page: the once-per-run work below, the "Cancelling…" drain
  // and the transient status line.
  //
  // "A run is in flight" is DERIVED from the run's own frame, never mirrored in
  // a second boolean: DangerZone and RemovedGamesCleanup read the same store
  // field, so a path that ends the run locally without ending it in the store
  // would make the three disagree (#1019). A run this page never started is
  // still this page's to show — the store outlives the Sync page the press
  // happened on.
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
      // Provoked by the run ending, so it may not join a read issued while the
      // run was still going — see refreshSyncStatsAfterChange. The session-budget
      // reading is not re-taken here: nothing on Main renders it any more, and
      // the page that does re-reads it on the same terminal stage and again on
      // its own mount.
      detach(refreshSyncStatsAfterChange());
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

    getSettings()
      .then((s) => {
        if (s.retroarch_input_check) {
          setRetroarchWarning(s.retroarch_input_check);
        }
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
    const storeAtIssue = getSyncProgress();
    getSyncStatus()
      .then(({ inFlight, ...backendProgress }) => {
        const stored = getSyncProgress();
        if (!snapshotHasAuthority(backendProgress, inFlight, stored, storeAtIssue)) return;
        setStoredSyncProgress(seededFrame(backendProgress, stored));
      })
      .catch((e) => logError(`Failed to query sync status: ${e}`));

    return () => {
      if (statusTimeoutRef.current) clearTimeout(statusTimeoutRef.current);
    };
  }, []);

  // Whether a preview is worth a slot. A run in flight owns it, so one held
  // while a run is going is not named — the store keeps it and the slot names it
  // again the moment the run ends. **Main never DISCARDS one**: a preview ends
  // only on the Sync page, through Apply, Cancel or Refresh.
  const pendingPreview = syncing ? null : preview;
  // An expired preview counts as none — the backend drops one past its TTL, so
  // the slot would otherwise offer a review of something it will refuse.
  //
  // What ticks is a single timer aimed at the deadline rather than a per-second
  // interval: nothing on Main counts a preview down (the countdown belongs to
  // the Sync page), so the only moment the clock changes anything here is the
  // one the slot disappears at. The clock itself is stamped by the handlers a
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

  // Belt-and-braces on top of the backend emit-last fix (#39): while the paused
  // notice is showing, re-read the stats so the "Last sync" line and the notice
  // itself (which keys on last_attempt) recover if the one-shot terminal refetch
  // was ever missed or dropped. Then the poll self-stops — last_attempt is no
  // longer paused. Not while a run is going: the run's own end re-reads them,
  // and until it ends the paused attempt is still the truth.
  const lastRunPaused = stats?.last_attempt?.status === "paused";
  useEffect(() => {
    if (syncing || !lastRunPaused) return;
    const id = setInterval(() => detach(refreshSyncStats()), PAUSED_STATS_POLL_MS);
    return () => clearInterval(id);
  }, [syncing, lastRunPaused]);

  const handleCancel = async () => {
    // No preview branch here. This handler is wired to one button — "Cancel
    // Sync", which exists only while a run is in flight — so a preview cannot be
    // what the press is about; ending one is the Sync page's business entirely.
    // A branch reading the STORE's preview would fire exactly where the store
    // holds one while a run is going, discarding it and leaving the run
    // untouched with nothing on Main able to stop it.
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
      // draining or still working, and claiming otherwise would take away the only
      // button that can still stop it and tell every other reader of the store
      // that a live run had stopped (#1019).
      setCancelling(false);
      showTransientStatus("Failed to cancel sync");
    }
  };

  const activeDownloads = downloads.filter((d) => d.status === "queued" || d.status === "downloading");
  const completedDownloads = downloads.filter(
    (d) => d.status === "completed" || d.status === "failed" || d.status === "cancelled",
  );
  const hasDownloads = activeDownloads.length > 0 || completedDownloads.length > 0;

  // The conditional slot, absent unless the Sync page has something to report.
  // Two occasions and no third: a run in flight — coarse step, counter and bar,
  // with the stage caption, the fine-detail line and the estimate left to the
  // page that has room for them — and a preview waiting to be answered. A
  // cancelled or interrupted run is deliberately not one: the Last sync row
  // states it, and continuing it is a button on the page.
  let slot: StatusSlot | null = null;
  if (syncing) {
    slot = {
      label: run.runKind === null ? RUN_KIND_UNKNOWN_LABEL : RUN_KIND_LABEL[run.runKind],
      value: run.totalSteps ? `${run.step} of ${run.totalSteps}` : "",
      bar: true,
    };
  } else if (pendingPreview && !previewExpired) {
    slot = { label: "Changes ready", value: previewCountsLine(pendingPreview), bar: false };
  }

  if (versionError) {
    return <VersionErrorCard message={versionError} compact />;
  }

  if (migration.pending) {
    return <MigrationBlockedPage migration={migration} />;
  }

  return (
    <>
      <LegacyInstallNotice />
      {settingsReset.pending && <SettingsResetBanner backedUpTo={settingsReset.backedUpTo} />}
      {playtimeScope.pending && <PlaytimeScopeBanner />}
      {/* Untitled status block (Connection / Last sync / Library) leads the
          panel — a hairline is what separates one block from the next, so a
          "Status" title would cost a row and buy nothing. */}
      <PanelSection>
        {retrodeckBanner && (
          <PanelSectionRow>
            {/* WarningCard is shared with the game-detail context, so it carries no
                focus contract of its own. This QAM-only wrapper's no-op activation
                makes the notice itself a stop for focus-driven scrolling. */}
            <Focusable onActivate={() => {}}>
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
              {/* A stop that STATES, like the two rows around it. It stays
                  focusable with nothing to activate so a reader can walk the
                  block and Steam can scroll it into view, which is the only way
                  back up to it: the panel opens on the menu below, not here. The
                  way to the Sync page is that menu, and the conditional slot
                  below while there is something to report. */}
              <Field label="Last sync" focusable={true} bottomSeparator="none" childrenContainerWidth="max">
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
        {slot && (
          <PanelSectionRow>
            {/* The one status row that can be pressed; it opens the Sync page,
                exactly as the menu's Sync entry does. The bar rides the field's
                description so the row and the bar are one focus stop — Steam
                scrolls a region by moving focus, and a bar of its own would be
                an unreachable row between two stops. */}
            <Field
              label={<span data-testid="sync-slot-label">{slot.label}</span>}
              focusable={true}
              onActivate={() => onNavigate("sync")}
              bottomSeparator="none"
              {...(slot.bar
                ? {
                    description: (
                      <ProgressBar
                        indeterminate={run.coarseFraction === undefined}
                        {...(run.coarseFraction !== undefined ? { nProgress: run.coarseFraction } : {})}
                      />
                    ),
                  }
                : {})}
            >
              <span data-testid="sync-slot-value" style={{ fontSize: "12px" }}>
                {slot.value}
              </span>
            </Field>
          </PanelSectionRow>
        )}
        {syncing && (
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
        )}
        {/* Not gated on the run being idle: a cancel whose CALL failed leaves the
            run in flight, and its "Failed to cancel sync" line has to reach the
            user under the rows it is about or it is lost entirely. Every other
            status is set by a path that has already ended its run. */}
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
              <Focusable onActivate={() => {}}>
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
              <Focusable onActivate={() => {}}>
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
        {/* Last of the button-carrying notices: nothing is lost either way and
            the plugin is running, so what is outstanding is only where its data
            ends up. Its button opens a modal rather than a page, because the
            condition is answered once and for all. */}
        <DataLocationNotice />
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
          {/* Where the panel opens. The three status rows above act on nothing,
              so opening on the first of them spends the reader's first press on
              a move to what they came for. The declaration names this area and
              the router's own rule picks the stop inside it, so nothing here
              decides which element takes focus.

              A wrapper because the attribute has to sit on an element we
              render: Steam's `ButtonItem` takes its own props and nothing
              establishes that it passes an unknown one down to the DOM.
              `display: contents` keeps the wrapper out of the layout — it
              carries the attribute and nothing else. */}
          <div {...{ [ENTRY_STOP_ATTR]: "" }} style={{ display: "contents" }}>
            <ButtonItem layout="below" bottomSeparator="none" onClick={() => onNavigate("sync")}>
              Sync
            </ButtonItem>
          </div>
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
