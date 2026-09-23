import { useEffect, useRef, useState, FC, Fragment, type CSSProperties } from "react";
import { showToast } from "../utils/toast";
import {
  ButtonItem,
  DialogButton,
  Field,
  Focusable,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  ProgressBar,
  ToggleField,
  showModal,
} from "@decky/ui";
import {
  cancelPrune,
  getPrunePreview,
  startPrune,
  stagePruneInstalledSelection,
  logError,
  logInfo,
  logWarn,
  type PrunePreviewItem,
  type PrunePreviewRequest,
  type PrunePreviewResult,
  type PruneScope,
} from "../api/backend";
import { detach } from "../utils/detach";
import {
  beginPrunePreview,
  beginPruneRun,
  getPruneState,
  isPruneResultLost,
  onPruneStateChange,
  type PruneComplete,
  type PruneProgress,
} from "../utils/pruneStore";
import { scrollNearestToBottom, scrollNearestToTop } from "../utils/scrollHelpers";
import { getSyncProgress, onSyncProgressChange } from "../utils/syncProgress";
import { withTimeout } from "../utils/withTimeout";
import type { PageRead } from "./data/useDataPage";
import {
  ButtonRow,
  CELL_CLIP,
  FLAT_BUTTON,
  MUTED,
  PaneTableHeader,
  PaneTableRow,
  SECONDARY_FONT,
  type TableCell,
  type TableRegister,
} from "./layout/pane";

const PAGE_SIZE = 50;
const SELECTION_PAGE_SIZE = 100;
const PRUNE_CALLABLE_TIMEOUT_MS = 15000;
const RESULT_LOST_MESSAGE = "The cleanup result was lost — check your library and run the scan again.";
/** What Cancel can and cannot promise — the running group is never rolled back. */
const CANCEL_HINT = "Stops before the next game. The one being processed now finishes and reports what it changed.";
/** Shown once Stop was pressed, so a second press never looks necessary. */
const CANCELLING_HINT = "Stopping — finishing the current safe step, then reporting what changed.";

/**
 * What makes the finished run's details region a focus stop.
 *
 * Everything inside it is plain text, and a region here scrolls only by focus
 * moving into it, so without a stop of its own everything past its 180 px is out
 * of reach with a controller. `focusableIfEmpty` rather than an activate handler:
 * it makes the node a stop only while the node holds no child nav node, so it
 * steps aside by itself the day a control is added in there, where a plain
 * `focusable` would keep the stop and swallow it — and an activate handler would
 * promise an action this region has not got. `FocusableProps` declares neither
 * option, so it arrives through a spread of this — the ESLint rule's paragraphs
 * under "Two widths" in `docs/architecture/qam-panel.md` state both halves.
 *
 * This diverges from the idiom its peers use: `SessionBudgetBanner` and the other
 * text-only notices became stops by carrying a no-op `onActivate`. The difference
 * is not style — that promotes the node to `focusable`, which keeps the stop and
 * swallows whatever control is added inside it later.
 *
 * The region asks for no `tabIndex` of its own: that attribute is Steam's to write
 * (`docs/architecture/qam-panel.md`), and where it writes none it means it, so a
 * hand-written one would override a deliberate omission.
 */
const DETAILS_REGION_STOP: { focusableIfEmpty: boolean } = { focusableIfEmpty: true };

/**
 * Ask the backend to stop `runId`. Returns the message to surface, or null when
 * the request was accepted — the run's own terminal frame reports the outcome,
 * so a success here must not overwrite it with chatter.
 */
async function requestPruneCancel(runId: string): Promise<string | null> {
  logInfo(`[prune] Cancel pressed for run ${runId}`);
  try {
    const result = await withTimeout(cancelPrune(runId), PRUNE_CALLABLE_TIMEOUT_MS);
    if (!result.success) {
      logWarn(`[prune] cancelPrune refused: reason=${result.reason ?? "none"}`);
      return result.message;
    }
    logInfo(`[prune] cancelPrune accepted for run ${runId}`);
    return null;
  } catch (e) {
    logError(`[prune] cancelPrune threw for run ${runId}: ${e}`);
    return `Could not request cancellation: ${e}`;
  }
}

const plural = (count: number, singular: string, pluralForm: string): string => (count === 1 ? singular : pluralForm);

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index++;
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[index]}`;
}

/**
 * The backend's stage slugs in the words the user reads — one entry per
 * `emit_progress` call site in `services/prune/executor.py`, and no others.
 * An unknown stage falls back to its slug with underscores opened up, so a new
 * backend stage degrades to something readable instead of vanishing; that
 * fallback is a safety net, not a licence to leave a real stage unmapped.
 *
 * Exported so a test can pin the key set against the backend's: an entry for a
 * stage that is never emitted hides a missing one behind it.
 */
export const STAGE_LABELS: Record<string, string> = {
  checking: "Checking with RomM",
  creating_recovery: "Backing up",
  recovery_sealed: "Backup complete",
  repointing: "Updating the Steam shortcut",
  removing_shortcut: "Removing the Steam shortcut",
  removing: "Removing local data",
  removed: "Done",
};

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage.replace(/_/g, " ");
}

/**
 * How full the bar is: the groups already behind the run, not the one in it.
 *
 * `current` names the group being worked on, so it is finished only once a later
 * frame moves past it. Counting it as done filled a one-group run before its
 * first byte moved, and a group publishes nothing during its longest phase — a
 * multi-hundred-megabyte content backup — so the bar sat at full for the whole
 * of it. Clamped: a frame that arrives late or out of order must not push the
 * fill past either end.
 */
function finishedGroupsPercent(current: number, total: number): number {
  return Math.min(100, Math.max(0, ((current - 1) / total) * 100));
}

/** Caption + bar for a running cleanup, shared by the dialog and Data Management's Gone from RomM pane. */
const CleanupProgress: FC<{ progress: PruneProgress }> = ({ progress }) => (
  <div style={{ width: "100%" }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", fontSize: "12px" }}>
      <span style={{ overflowWrap: "anywhere" }}>
        {stageLabel(progress.stage)} — {progress.name}
      </span>
      <span style={{ flexShrink: 0, marginLeft: "8px" }}>
        {progress.current} / {progress.total}
      </span>
    </div>
    <ProgressBar
      indeterminate={progress.total <= 0}
      {...(progress.total > 0 ? { nProgress: finishedGroupsPercent(progress.current, progress.total) } : {})}
    />
  </div>
);

/**
 * The bar's last frame, rendered beside the run's summary.
 *
 * Full means the run is over, not that it succeeded — the summary next to it
 * says how it ended. Without it the running bar, which stops one group short of
 * full, would simply vanish when the terminal frame lands.
 */
const FinishedCleanupBar: FC = () => <ProgressBar nProgress={100} />;

/** How many rows a group actually removed, however the frame reported it. */
function removedInGroup(item: { removed_count?: number; removed_rom_ids?: number[] }): number {
  return item.removed_count ?? item.removed_rom_ids?.length ?? 0;
}

function requestFor(
  scope: PruneScope,
  romId: number | null,
  previewId: string | null,
  offset: number,
): PrunePreviewRequest {
  return { scope, rom_id: romId, preview_id: previewId, offset, limit: PAGE_SIZE };
}

/**
 * Why Confirm is unavailable, in the user's words. Several of these conditions
 * already render a warning somewhere in the dialog, but the dialog scrolls —
 * a greyed button with its explanation off-screen reads as a dead control, and
 * a press that does nothing at all is indistinguishable from a broken plugin.
 */
function confirmBlockedReason(state: {
  completed: boolean;
  runInFlight: boolean;
  allEntriesLoaded: boolean;
  total: number;
  destructiveConfirmed: boolean;
  insufficientSpace: boolean;
  unknownSelectedSize: boolean;
  anyOptionChosen: boolean;
}): string | null {
  if (state.completed) return null;
  if (state.runInFlight) return "A cleanup is already running.";
  if (!state.allEntriesLoaded) return `Load all ${state.total} entries before confirming.`;
  if (!state.destructiveConfirmed) return "Confirm you understand there will be no recovery bundle.";
  if (state.insufficientSpace) {
    return state.unknownSelectedSize
      ? "A selected ROM's size can't be measured, so the recovery bundle can't be guaranteed to fit."
      : "The selected ROM content doesn't fit in the free space at the recovery target.";
  }
  if (!state.anyOptionChosen) return "Choose at least one cleanup option above.";
  return null;
}

/**
 * Stage the installed-content opt-ins page by page, chaining each page onto the
 * selection the previous one opened. A refused page reports itself and stops the
 * whole Confirm — a partially staged selection must never reach the run.
 *
 * Exported so a test can pin the page boundary directly: reaching it through the
 * dialog needs one rendered row and one click per selected id, which makes the
 * cost of proving it grow with the boundary it is proving.
 */
export async function stageInstalledSelections(
  previewId: string,
  selected: number[],
  setStatus: (message: string) => void,
): Promise<{ ok: true; selectionId: string | null } | { ok: false }> {
  let selectionId: string | null = null;
  for (let offset = 0; offset < selected.length; offset += SELECTION_PAGE_SIZE) {
    const page = selected.slice(offset, offset + SELECTION_PAGE_SIZE);
    const staged: {
      success: boolean;
      selection_id?: string;
      message?: string;
    } = await withTimeout(
      stagePruneInstalledSelection({
        preview_id: previewId,
        selection_id: selectionId,
        rom_ids: page,
        final: offset + page.length >= selected.length,
      }),
      PRUNE_CALLABLE_TIMEOUT_MS,
    );
    if (!staged.success || !staged.selection_id) {
      setStatus(staged.message ?? "Installed-content selections could not be staged.");
      logWarn(`[prune] Confirm aborted while staging installed content: ${staged.message ?? "no message"}`);
      return { ok: false };
    }
    selectionId = staged.selection_id;
  }
  return { ok: true, selectionId };
}

/** What the Confirm button says: pressed, then the run's current stage, then its resting label. */
function confirmButtonLabel(starting: boolean, progress: PruneProgress | null): string {
  if (starting) return "Starting...";
  if (progress) return `${progress.stage.replace(/_/g, " ")}...`;
  return "Confirm Cleanup";
}

/** Why the scan button is unavailable, or nothing when it is not. */
function scanButtonDescription(syncRunning: boolean, runActive: boolean): string | undefined {
  if (syncRunning) return "Unavailable while a library sync is running.";
  if (runActive) return "A cleanup is running. Its progress is shown below.";
  return undefined;
}

/** Game, Platform, Verdict, Installed, Keep a copy. */
const CANDIDATE_COLUMNS = "minmax(0, 1fr) 64px 160px 64px 72px";

/** The pane's register without its gutter: the dialog body is padded already. */
const CANDIDATE_REGISTER: TableRegister = { rowPadding: "4px 0", headerPadding: "0 0 4px", rule: true };

const SECONDARY_CELL: CSSProperties = { color: MUTED, fontSize: SECONDARY_FONT, fontVariantNumeric: "tabular-nums" };

const ROW_WARNING: CSSProperties = { color: "#e5a43b", fontSize: SECONDARY_FONT };

/**
 * What a row's Verdict cell says, and the sentence it is short for. It states
 * what is known now and never the run's outcome: a version still on RomM is
 * removed too if the run's check finds every version of its game gone.
 */
function verdictFor(item: PrunePreviewItem): { short: string; full: string } {
  const ofGroup = item.group_size > 1 ? ` · one of ${item.group_size}` : "";
  if (item.candidate) {
    return {
      short: `Gone from RomM${ofGroup}`,
      full: "Gone from RomM — removed once the server confirms it.",
    };
  }
  return {
    short: `Still on RomM${ofGroup}`,
    full: "Still on RomM at your last sync. Removed only if the final check finds every version of this game gone — then the whole game goes, Steam shortcut included.",
  };
}

/** What tells two versions of one game apart: their id, and the file where it says more than the name. */
function versionLine(item: PrunePreviewItem, name: string): string {
  return item.fs_name && item.fs_name !== name ? `ROM ${item.rom_id} · ${item.fs_name}` : `ROM ${item.rom_id}`;
}

function installedCell(item: PrunePreviewItem): TableCell {
  const style = { ...SECONDARY_CELL, textAlign: "right" } as const;
  if (!item.installed) return { content: "—", title: "No ROM files on this device", style };
  if (item.installed_bytes === null) return { content: "unknown", title: "Size unavailable", style };
  return { content: formatBytes(item.installed_bytes), style };
}

const CandidateRow: FC<{
  item: PrunePreviewItem;
  included: boolean;
  recovery: boolean;
  runInFlight: boolean;
  onInclude: (checked: boolean) => void;
}> = ({ item, included, recovery, runInFlight, onInclude }) => {
  const name = item.name || item.fs_name || `ROM ${item.rom_id}`;
  const verdict = verdictFor(item);
  const truncated = item.name_truncated || item.fs_name_truncated || item.group_id_truncated || item.warning_truncated;
  const keepCopy: TableCell = item.installed
    ? {
        content: (
          <ToggleField
            checked={included}
            disabled={runInFlight || !recovery}
            bottomSeparator="none"
            onChange={onInclude}
          />
        ),
        title: "Include this version's installed ROM content in the recovery bundle",
        // A control draws its focus ring outside its own box.
        clip: false,
      }
    : { content: "—", style: { ...SECONDARY_CELL, textAlign: "right" } };
  return (
    <PaneTableRow
      columns={CANDIDATE_COLUMNS}
      register={CANDIDATE_REGISTER}
      testId={`cleanup-row-${item.rom_id}`}
      // An installed row's Keep-a-copy toggle is its stop already.
      focusStop={!item.installed}
      {...(item.candidate ? {} : { style: { opacity: 0.75 } })}
      cells={[
        {
          content: name,
          title: name,
          ...(item.candidate ? { style: { fontWeight: 600 } } : {}),
        },
        { content: item.platform_slug, title: item.platform_slug, style: SECONDARY_CELL },
        {
          content: verdict.short,
          title: verdict.full,
          style: { fontSize: SECONDARY_FONT, color: item.candidate ? "#c7d5e0" : MUTED },
        },
        installedCell(item),
        keepCopy,
      ]}
    >
      <div style={{ ...SECONDARY_CELL, ...CELL_CLIP }} title={versionLine(item, name)}>
        {versionLine(item, name)}
      </div>
      {truncated && (
        <div style={ROW_WARNING}>
          One or more display fields were shortened to keep this preview page within the Decky wire limit.
        </div>
      )}
      {item.warning && <div style={ROW_WARNING}>{item.warning}</div>}
      {item.installed && (!recovery || !included) && (
        <div style={ROW_WARNING}>Without a backup, the downloaded ROM file is deleted along with this version.</div>
      )}
    </PaneTableRow>
  );
};

/**
 * The candidates, then — as a section of the same table — the other versions a
 * whole-game removal could still take. The backend sorts candidates first, so
 * the first non-candidate row is where that section starts.
 */
const CandidateTable: FC<{
  items: readonly PrunePreviewItem[];
  includedContent: ReadonlySet<number>;
  recovery: boolean;
  runInFlight: boolean;
  onInclude: (romId: number, checked: boolean) => void;
}> = ({ items, includedContent, recovery, runInFlight, onInclude }) => (
  <div data-testid="cleanup-table" style={{ marginTop: "14px" }}>
    <PaneTableHeader
      columns={CANDIDATE_COLUMNS}
      register={CANDIDATE_REGISTER}
      testId="cleanup-header"
      cells={[
        "Game",
        "Platform",
        "Verdict",
        { content: "Installed", style: { textAlign: "right" } },
        { content: "Keep a copy", style: { textAlign: "right" } },
      ]}
    />
    {items.map((item, index) => (
      <Fragment key={item.rom_id}>
        {!item.candidate && (index === 0 || items[index - 1]!.candidate) && (
          <div data-testid="cleanup-kept-section" style={{ margin: "12px 0 4px" }}>
            <div style={{ fontWeight: 700 }}>Other versions of these games — still on RomM</div>
            <div style={{ fontSize: SECONDARY_FONT, color: MUTED }}>
              Still on RomM at your last sync. Each is removed only if the final check finds every version of its game
              gone — then the whole game goes, Steam shortcut included.
            </div>
          </div>
        )}
        {/* Steam scrolls only far enough to reveal the focused control, so
            reaching the last row brings up what sits under it — its own lines,
            and the note after the table. */}
        <div {...(index === items.length - 1 ? { onFocus: scrollNearestToBottom } : {})}>
          <CandidateRow
            item={item}
            included={includedContent.has(item.rom_id)}
            recovery={recovery}
            runInFlight={runInFlight}
            onInclude={(checked) => onInclude(item.rom_id, checked)}
          />
        </div>
      </Fragment>
    ))}
  </div>
);

/** A run in flight: its caption and bar, and the Stop that ends it. */
const CleanupRunProgress: FC<{ progress: PruneProgress; cancelling: boolean; onStop: () => void }> = ({
  progress,
  cancelling,
  onStop,
}) => (
  <div style={{ marginTop: "10px", color: "#c7d5e0" }}>
    <div role="status" aria-live="polite">
      <CleanupProgress progress={progress} />
    </div>
    <div style={{ color: MUTED, fontSize: "12px", marginTop: "4px" }}>{CANCEL_HINT}</div>
    <ButtonRow padding="6px 0 0">
      <DialogButton style={FLAT_BUTTON} disabled={cancelling} onClick={onStop}>
        {cancelling ? "Stopping..." : "Stop Cleanup"}
      </DialogButton>
    </ButtonRow>
    {cancelling && (
      <div role="status" aria-live="polite" style={{ color: MUTED, fontSize: "12px", marginTop: "4px" }}>
        {CANCELLING_HINT}
      </div>
    )}
  </div>
);

/** A finished run: the full bar, the counts, and the groups worth a second look. */
const CleanupResult: FC<{ complete: PruneComplete }> = ({ complete }) => (
  <div style={{ marginTop: "10px", color: complete.success ? "#8fd18b" : "#ffcc66" }}>
    <FinishedCleanupBar />
    <div role="status" aria-live="polite">
      {complete.removed_count ?? complete.removed_rom_ids.length} removed;{" "}
      {complete.problem_count ??
        complete.results.filter((item) => ["partial", "failed", "skipped"].includes(item.status)).length}{" "}
      skipped, partial, or failed.
    </div>
    <Focusable
      role="region"
      aria-label="Cleanup details"
      {...DETAILS_REGION_STOP}
      style={{ maxHeight: "180px", overflowY: "auto", marginTop: "6px" }}
    >
      {complete.message && (
        <div style={{ fontSize: "12px", marginTop: "4px" }}>
          {complete.reason ? `${complete.reason}: ` : ""}
          {complete.message}
        </div>
      )}
      {complete.results
        .filter(
          (item) =>
            ["partial", "failed", "skipped"].includes(item.status) ||
            (item.warnings?.length ?? 0) > 0 ||
            item.warnings_omitted ||
            item.warnings_truncated ||
            // A sealed bundle that removed nothing leaves a folder on
            // disk; saying so is what stops it being a mystery later.
            (item.bundle_path !== undefined && removedInGroup(item) === 0),
        )
        .map((item) => (
          <div key={item.group_id} style={{ fontSize: "12px", marginTop: "4px" }}>
            {item.name || item.group_id}: {item.message}
            {item.bundle_path !== undefined && removedInGroup(item) === 0 && (
              <div>Backup created, nothing removed. The folder stays at {item.bundle_path}.</div>
            )}
            {item.message_truncated && <div>Detail was shortened to fit the Decky wire limit.</div>}
            {item.warnings?.map((warning) => (
              <div key={warning}>Warning: {warning}</div>
            ))}
            {item.warnings_omitted && (item.warning_count ?? 0) > (item.warnings?.length ?? 0) && (
              <div>{(item.warning_count ?? 0) - (item.warnings?.length ?? 0)} additional warning(s) omitted.</div>
            )}
            {item.warnings_truncated && <div>One or more displayed warnings were shortened.</div>}
          </div>
        ))}
    </Focusable>
  </div>
);

interface CleanupModalProps {
  initial: PrunePreviewResult;
  scope: PruneScope;
  romId: number | null;
  closeModal?: () => void;
}

const CleanupModal: FC<CleanupModalProps> = ({ initial, scope, romId, closeModal }) => {
  const [items, setItems] = useState<PrunePreviewItem[]>(initial.items ?? []);
  const [loadingMore, setLoadingMore] = useState(false);
  const [starting, setStarting] = useState(false);
  const [cancelRequestedFor, setCancelRequestedFor] = useState<string | null>(null);
  const [runStarted, setRunStarted] = useState(false);
  const [status, setStatus] = useState("");
  const [repoint, setRepoint] = useState(true);
  const [removeRows, setRemoveRows] = useState(true);
  const [removeDeadGames, setRemoveDeadGames] = useState(true);
  const [recovery, setRecovery] = useState(true);
  const [confirmWithoutRecovery, setConfirmWithoutRecovery] = useState(false);
  const [includedContent, setIncludedContent] = useState<Set<number>>(new Set());
  const [freeBytes, setFreeBytes] = useState(initial.free_bytes ?? 0);
  const [pruneState, setPruneState] = useState(getPruneState());
  const [resultLost, setResultLost] = useState(isPruneResultLost());

  useEffect(() => {
    const unsubscribe = onPruneStateChange(() => {
      setPruneState(getPruneState());
      setResultLost(isPruneResultLost());
    });
    return () => {
      unsubscribe();
    };
  }, []);

  const total = initial.total ?? items.length;
  // The headline count is the rows this run can remove on its own. `total` also
  // counts the siblings that are merely disclosed because a whole-game removal
  // could still take them, and leading with that number reads as a threat to
  // versions RomM still serves.
  const candidateTotal = initial.candidate_total ?? total;
  // A row that is not a candidate can only ever be deleted by a whole-game
  // removal (`selected_prune_ids` returns nothing else for it), so with that
  // option off it is disclosing a thing that cannot happen. Every page is still
  // fetched and the wire payload is untouched — this is what gets rendered.
  const visibleItems = removeDeadGames ? items : items.filter((item) => item.candidate);
  const selectedBytes = visibleItems.reduce(
    (sum, item) => sum + (includedContent.has(item.rom_id) ? (item.installed_bytes ?? 0) : 0),
    0,
  );
  const unknownSelectedSize = visibleItems.some(
    (item) => includedContent.has(item.rom_id) && item.installed && item.installed_bytes === null,
  );
  const insufficientSpace = recovery && (unknownSelectedSize || selectedBytes > freeBytes);
  const destructiveConfirmed = recovery || confirmWithoutRecovery;
  const allEntriesLoaded = items.length === total;
  const progress = pruneState.progress;
  const complete = pruneState.complete;
  const runInFlight = complete === null && (starting || pruneState.runId !== null);
  // Derived from the RUN's lifecycle, never the cancel callable's: the callable
  // resolving means the request was received, not that the run has stopped.
  // Tying the lock to it let seven presses through in three seconds (#1570 F19).
  // The exits are the terminal frame (complete), the run going away in the
  // store, and an outright refusal — the backend's idempotency is the safety
  // net behind that, not the mechanism.
  const cancelling = complete === null && cancelRequestedFor !== null && cancelRequestedFor === pruneState.runId;
  const blockedReason = confirmBlockedReason({
    completed: complete !== null,
    runInFlight,
    allEntriesLoaded,
    total,
    destructiveConfirmed,
    insufficientSpace,
    unknownSelectedSize,
    anyOptionChosen: repoint || removeRows || removeDeadGames,
  });
  // Disabled ONLY where a press is unsafe or meaningless: a run is already
  // going, or this dialog is showing a finished one. Every other reason to
  // refuse is explained when pressed — a greyed button whose explanation the
  // user has to hunt for is the shape the silent no-op took.
  const pressBlocked = complete !== null || runInFlight;

  const toggleContent = (romIdToToggle: number, checked: boolean): void => {
    setIncludedContent((current) => {
      const next = new Set(current);
      if (checked) next.add(romIdToToggle);
      else next.delete(romIdToToggle);
      return next;
    });
  };

  const loadMore = async (): Promise<void> => {
    if (!initial.preview_id || items.length >= total) return;
    setLoadingMore(true);
    try {
      const next = await withTimeout(
        getPrunePreview(requestFor(scope, romId, initial.preview_id, items.length)),
        PRUNE_CALLABLE_TIMEOUT_MS,
      );
      if (!next.success) {
        setStatus(next.message ?? "Could not load more candidates.");
        return;
      }
      setItems((current) => [...current, ...(next.items ?? [])]);
      if (typeof next.free_bytes === "number") setFreeBytes(next.free_bytes);
    } catch (e) {
      setStatus(`Could not load more candidates: ${e}`);
    } finally {
      setLoadingMore(false);
    }
  };

  const refreshFreeSpace = async (): Promise<void> => {
    if (!initial.preview_id) return;
    try {
      const refreshed = await withTimeout(
        getPrunePreview({
          scope,
          rom_id: romId,
          preview_id: initial.preview_id,
          offset: 0,
          limit: 0,
        }),
        PRUNE_CALLABLE_TIMEOUT_MS,
      );
      if (!refreshed.success || typeof refreshed.free_bytes !== "number") {
        setStatus(refreshed.message ?? "Could not refresh recovery space.");
        return;
      }
      setFreeBytes(refreshed.free_bytes);
    } catch (e) {
      setStatus(`Could not refresh recovery space: ${e}`);
    }
  };

  const start = async (): Promise<void> => {
    // Confirm is the destructive commit point, so every press is logged and
    // every outcome is visible in the dialog. A press that returns silently is
    // indistinguishable on device from a plugin that has stopped responding.
    logInfo(`[prune] Confirm pressed (preview=${initial.preview_id ?? "none"}, scope=${scope}, total=${total})`);
    if (!initial.preview_id) {
      setStatus("This cleanup preview has no id — close and scan again.");
      logError("[prune] Confirm aborted: the modal holds no preview id");
      return;
    }
    if (blockedReason !== null) {
      setStatus(`Cleanup did not start: ${blockedReason}`);
      logWarn(`[prune] Confirm refused locally: ${blockedReason}`);
      return;
    }
    setStarting(true);
    setStatus(
      includedContent.size
        ? `Staging ${includedContent.size} installed-content selection(s)...`
        : "Starting cleanup...",
    );
    try {
      const staged = await stageInstalledSelections(initial.preview_id, [...includedContent], setStatus);
      if (!staged.ok) return;
      const result = await withTimeout(
        startPrune({
          preview_id: initial.preview_id,
          confirmed: true,
          repoint_shortcuts: repoint,
          remove_rows: removeRows,
          remove_fully_vanished: removeDeadGames,
          create_recovery_bundle: recovery,
          installed_selection_id: staged.selectionId,
        }),
        PRUNE_CALLABLE_TIMEOUT_MS,
      );
      if (!result.success) {
        setStatus(result.message ?? "Cleanup could not start.");
        logWarn(`[prune] startPrune refused: reason=${result.reason ?? "none"} message=${result.message ?? "none"}`);
        return;
      }
      if (!result.run_id) {
        // A success without a run id can never be adopted by id — say so instead
        // of wedging frame admission on a run the store will never recognise.
        setStatus("Cleanup started but the backend response carried no run id.");
        logError("[prune] startPrune reported success with no run id");
        return;
      }
      setRunStarted(true);
      if (!beginPruneRun(result.run_id, initial.preview_id)) {
        // The run IS executing; this dialog just can't receive its frames, and
        // neither can Data Management. Saying "running..." here would leave the
        // user watching a progress line that can never arrive.
        setStatus("Cleanup started, but this dialog lost track of it. Check the log, then re-scan to see the result.");
        logError(`[prune] run ${result.run_id} started but preview ${initial.preview_id} was no longer pending`);
        return;
      }
      setStatus("Cleanup running...");
      logInfo(`[prune] startPrune accepted: run=${result.run_id}`);
    } catch (e) {
      const adopted = getPruneState();
      if (adopted.runId !== null) {
        setRunStarted(true);
        setStatus(adopted.complete ? "Cleanup completed." : "Cleanup running...");
        logWarn(`[prune] startPrune response was lost; adopted run ${adopted.runId} from its frames instead: ${e}`);
      } else {
        setStatus(`Cleanup could not start: ${e}`);
        logError(`[prune] startPrune threw with no run adopted: ${e}`);
      }
    } finally {
      setStarting(false);
    }
  };

  const stop = async (runId: string): Promise<void> => {
    setCancelRequestedFor(runId);
    const failure = await requestPruneCancel(runId);
    if (failure !== null) {
      // Refused means this run is not running, so no terminal
      // frame is coming to re-open the control — do it here.
      setCancelRequestedFor(null);
      setStatus(failure);
    }
  };

  const changeRemoveDeadGames = (checked: boolean): void => {
    setRemoveDeadGames(checked);
    // Turning it off hides the rows only a whole-game removal could
    // take, so their content selections must not stay staged.
    if (!checked) {
      setIncludedContent((current) => {
        const stillShown = new Set(items.filter((item) => item.candidate).map((item) => item.rom_id));
        return new Set([...current].filter((romId) => stillShown.has(romId)));
      });
    }
  };

  const changeRecovery = (checked: boolean): void => {
    setRecovery(checked);
    if (checked) setConfirmWithoutRecovery(false);
    else setIncludedContent(new Set());
  };

  return (
    <ModalRoot closeModal={closeModal}>
      {/* The whole dialog scrolls as one. A separate inner scroller for the table
          gives the controller two scroll axes to choose between on every focus
          move. */}
      <div
        style={{
          minWidth: "440px",
          maxWidth: "720px",
          padding: "18px",
          paddingBottom: "28px",
          maxHeight: "76vh",
          overflowY: "auto",
        }}
      >
        <div style={{ fontSize: "20px", fontWeight: 700, marginBottom: "6px" }}>Clean Up Removed RomM Games</div>
        <div style={{ color: "#c7d5e0", marginBottom: "14px" }}>
          {`${candidateTotal} locally kept ${plural(candidateTotal, "version", "versions")}${
            scope === "bulk" ? "" : " of this game"
          } ${plural(candidateTotal, "is", "are")} no longer on your RomM server.`}{" "}
          Nothing is removed until each one is checked against the server again — only entries RomM confirms as gone can
          be deleted.
          {removeDeadGames && total > candidateTotal
            ? " Other versions of the same games are listed below; they stay, unless the check finds every version of a game gone."
            : ""}
        </div>

        {/* Two columns the stick crosses sideways; each column is walked up and
            down, and leaving one at either end leaves the options. */}
        <Focusable flow-children="horizontal" style={{ display: "flex", gap: "24px" }}>
          <Focusable flow-children="vertical" style={{ flex: "1 1 0", minWidth: 0 }}>
            {/* Focusing the first control has to bring the intro above it back into
                view — Steam's focus engine only scrolls far enough to reveal the
                control itself, which strands the text off the top on a controller. */}
            <div onFocus={scrollNearestToTop}>
              <ToggleField
                label="Repoint vanished shortcuts"
                description="Each to its game's live default version, keeping the shortcut."
                checked={repoint}
                disabled={runInFlight}
                onChange={setRepoint}
              />
            </div>
            <ToggleField
              label="Remove gone versions"
              description="The confirmed rows and their installed content, in games that still have a live version."
              checked={removeRows}
              disabled={runInFlight}
              onChange={setRemoveRows}
            />
          </Focusable>
          <Focusable flow-children="vertical" style={{ flex: "1 1 0", minWidth: 0 }}>
            <ToggleField
              label="Remove fully vanished games"
              description="Only for games where the server confirms every single version is gone — each is removed whole, with any Steam shortcut it has. The recovery bundle keeps the shortcut's Steam details so you can rebuild it by hand."
              checked={removeDeadGames}
              disabled={runInFlight}
              onChange={changeRemoveDeadGames}
            />
            <ToggleField
              label="Create recovery bundle"
              description={`Verified bundles are sealed under ${initial.recovery_root ?? "the recovery directory"}.`}
              checked={recovery}
              disabled={runInFlight}
              onChange={changeRecovery}
            />
            {!recovery && (
              <ToggleField
                label="I understand there is no recovery bundle"
                description="Local database state and playtime will have no recovery bundle."
                checked={confirmWithoutRecovery}
                disabled={runInFlight}
                onChange={setConfirmWithoutRecovery}
              />
            )}
          </Focusable>
        </Focusable>

        {/* The run's controls sit ABOVE the table rather than after it: focus
            follows element order, so a bar after the rows is one press per row
            away however it is drawn. */}
        <div data-testid="cleanup-bar" style={{ marginTop: "14px", padding: "10px", background: "rgba(0,0,0,0.20)" }}>
          <div>
            Selected ROM-content recovery estimate: {formatBytes(selectedBytes)} · Free at target:{" "}
            {formatBytes(freeBytes)}
          </div>
          <div style={{ color: MUTED, fontSize: "12px", marginTop: "4px" }}>
            A lower bound: the backend remeasures saves, histories, caches and Steam files before anything changes.
            Large selections are staged in bounded pages first; every checked item stays part of this run.
          </div>
          {insufficientSpace && (
            <div style={{ color: "#ff8c6a", marginTop: "4px" }}>
              {unknownSelectedSize ? "A selected installed ROM has no safe measurable size." : "Not enough free space."}
            </div>
          )}
          <ButtonRow padding="8px 0 0">
            <DialogButton style={FLAT_BUTTON} disabled={runInFlight} onClick={() => detach(refreshFreeSpace())}>
              Refresh free space
            </DialogButton>
            {items.length < total && (
              <DialogButton style={FLAT_BUTTON} disabled={loadingMore} onClick={() => detach(loadMore())}>
                {loadingMore ? "Loading..." : `Load more (${items.length} of ${total})`}
              </DialogButton>
            )}
            <DialogButton style={FLAT_BUTTON} disabled={starting && complete === null} onClick={() => closeModal?.()}>
              {runStarted || complete !== null ? "Close" : "Cancel"}
            </DialogButton>
            <DialogButton style={FLAT_BUTTON} disabled={pressBlocked} onClick={() => detach(start())}>
              {confirmButtonLabel(starting, progress)}
            </DialogButton>
          </ButtonRow>
          {!allEntriesLoaded && (
            <div style={{ color: "#ff8c6a", fontSize: "12px", marginTop: "8px" }}>
              Load every page before confirming so all potentially removed group members and installed content are
              disclosed.
            </div>
          )}
          {progress && (
            <CleanupRunProgress
              progress={progress}
              cancelling={cancelling}
              onStop={() => detach(stop(progress.run_id))}
            />
          )}
          {complete && <CleanupResult complete={complete} />}
          {resultLost && (
            <div role="status" aria-live="polite" style={{ marginTop: "10px", color: "#ff8c6a" }}>
              {RESULT_LOST_MESSAGE}
            </div>
          )}
          {status && !complete && (
            <div role="status" aria-live="polite" style={{ marginTop: "10px", color: "#ffcc66" }}>
              {status}
            </div>
          )}
          {blockedReason !== null && !runInFlight && (
            <div style={{ marginTop: "10px", color: "#ff8c6a", fontSize: "12px" }}>{blockedReason}</div>
          )}
        </div>

        <CandidateTable
          items={visibleItems}
          includedContent={includedContent}
          recovery={recovery}
          runInFlight={runInFlight}
          onInclude={toggleContent}
        />
        {/* Without this the Keep-a-copy column is empty on a library where
            nothing is downloaded, and the option reads as missing rather than as
            not applicable. Only claimed once every page is loaded — an unseen
            page could still hold one. */}
        {allEntriesLoaded && !visibleItems.some((item) => item.installed) && (
          <div style={{ padding: "10px 0", fontSize: "12px", color: MUTED }}>
            None of these versions has ROM files downloaded on this device, so there is nothing to back up.
          </div>
        )}
      </div>
    </ModalRoot>
  );
};

/**
 * Scan for removed RomM games and open the review over what it found.
 *
 * Answers whether there was anything to review. *onScanRead* is told where the
 * scan stands at every step — reading as it starts, then the count either way,
 * including zero, or failed — so a caller showing that number never has to
 * infer a state from silence.
 */
export async function openRemovedGamesCleanupModal(
  romId?: number,
  onScanRead?: (read: PageRead<number>) => void,
): Promise<boolean> {
  const scope: PruneScope = romId === undefined ? "bulk" : "rom";
  onScanRead?.({ state: "reading" });
  let result: PrunePreviewResult;
  try {
    result = await withTimeout(getPrunePreview(requestFor(scope, romId ?? null, null, 0)), PRUNE_CALLABLE_TIMEOUT_MS);
  } catch (e) {
    onScanRead?.({ state: "failed" });
    throw e;
  }
  if (!result.success) {
    onScanRead?.({ state: "failed" });
    throw new Error(result.message ?? "Cleanup scan failed.");
  }
  onScanRead?.({ state: "answered", value: result.total ?? 0 });
  if ((result.total ?? 0) === 0) return false;
  // Without a preview id nothing can admit this run's frames — surface the
  // malformed response rather than opening a modal that can never report.
  if (!result.preview_id) throw new Error("Cleanup scan response carried no preview id.");
  beginPrunePreview(result.preview_id);
  showModal(<CleanupModal initial={result} scope={scope} romId={romId ?? null} />);
  return true;
}

export const RemovedGamesCleanupSection: FC<{ onScanRead?: (read: PageRead<number>) => void }> = ({ onScanRead }) => {
  const [scanning, setScanning] = useState(false);
  const [cancelRequestedFor, setCancelRequestedFor] = useState<string | null>(null);
  const [cancelStatus, setCancelStatus] = useState<string | null>(null);
  const [syncRunning, setSyncRunning] = useState(getSyncProgress().running);
  const [pruneState, setPruneState] = useState(getPruneState());
  const [resultLost, setResultLost] = useState(isPruneResultLost());
  const lastRunIdRef = useRef(getPruneState().runId);

  useEffect(() => {
    const unsubscribeSync = onSyncProgressChange(() => setSyncRunning(getSyncProgress().running));
    const unsubscribePrune = onPruneStateChange(() => {
      const next = getPruneState();
      // A refusal message belongs to the run it was refused for; carrying it
      // into the next run would describe that run's Stop button with an
      // outcome that never happened to it. Tracked in a ref because this
      // subscription is registered once and would otherwise compare against
      // the state it closed over on mount.
      if (next.runId !== lastRunIdRef.current) {
        lastRunIdRef.current = next.runId;
        setCancelStatus(null);
      }
      setPruneState(next);
      setResultLost(isPruneResultLost());
    });
    return () => {
      unsubscribeSync();
      unsubscribePrune();
    };
  }, []);

  const scan = async (): Promise<void> => {
    setScanning(true);
    try {
      if (!(await openRemovedGamesCleanupModal(undefined, onScanRead))) {
        showToast("No removed RomM entries were found.");
      }
    } catch (e) {
      logError(`Removed-game cleanup scan failed: ${e}`);
      showToast("Could not scan removed RomM games.");
    } finally {
      setScanning(false);
    }
  };

  const progress = pruneState.progress;
  const complete = pruneState.complete;
  // A run that has started but not yet emitted its first progress frame is
  // still a run: gate on the run id, not just on progress, or the entry point
  // stays live during exactly the window where a second scan would collide.
  const runActive = complete === null && pruneState.runId !== null;
  const cancelling = runActive && cancelRequestedFor !== null && cancelRequestedFor === pruneState.runId;
  const runLabel = progress
    ? `${progress.stage.replace(/_/g, " ")} · ${progress.current} of ${progress.total} · ${progress.name}`
    : "Cleanup starting...";
  return (
    <PanelSection title="Removed RomM Games">
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={scanning || syncRunning || runActive || progress !== null}
          description={scanButtonDescription(syncRunning, runActive)}
          onClick={() => detach(scan())}
        >
          {scanning ? "Scanning…" : "Clean Up Removed RomM Games"}
        </ButtonItem>
      </PanelSectionRow>
      {resultLost && (
        <PanelSectionRow>
          <Field label={RESULT_LOST_MESSAGE} />
        </PanelSectionRow>
      )}
      {runActive && (
        <>
          <PanelSectionRow>
            {progress ? <CleanupProgress progress={progress} /> : <Field label={runLabel} description={CANCEL_HINT} />}
          </PanelSectionRow>
          {progress?.bundle_path && (
            <PanelSectionRow>
              <Field label={`Recovery sealed: ${progress.bundle_path}`} />
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={cancelling}
              description={cancelStatus ?? (cancelling ? CANCELLING_HINT : undefined)}
              onClick={() =>
                detach(
                  (async () => {
                    const runId = pruneState.runId!;
                    setCancelRequestedFor(runId);
                    const failure = await requestPruneCancel(runId);
                    setCancelStatus(failure);
                    // A refusal is the only outcome with no terminal frame
                    // behind it, so it is the only one that re-opens the button.
                    if (failure !== null) setCancelRequestedFor(null);
                  })(),
                )
              }
            >
              {cancelling ? "Stopping..." : "Stop Cleanup"}
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
      {complete && (
        <>
          <PanelSectionRow>
            <FinishedCleanupBar />
          </PanelSectionRow>
          <PanelSectionRow>
            <Field
              label={`${complete.removed_count ?? complete.removed_rom_ids.length} removed; ${complete.problem_count ?? complete.results.filter((item) => ["partial", "failed", "skipped"].includes(item.status)).length} skipped, partial, or failed`}
              description={[
                complete.message,
                ...complete.results
                  .filter((item) => item.status !== "removed" || (item.warnings?.length ?? 0) > 0)
                  .flatMap((item) => [item.message, ...(item.warnings ?? []).map((warning) => `Warning: ${warning}`)]),
              ]
                .filter(Boolean)
                .join(" · ")}
            />
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
};
