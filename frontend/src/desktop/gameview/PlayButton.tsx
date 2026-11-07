/**
 * Tender's Play button on a RomM game's page in the desktop client, drawn in
 * place of Steam's own. What it draws, and whose each press is, is
 * docs/architecture/desktop-dom-architecture.md's.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type FC,
  type MouseEvent,
} from "react";
import { addEventListener, removeEventListener } from "../../api/host";
import { cancelDownload, debugLog, getCachedGameDetail, pauseDownload } from "../../api/backend";
import { useGameDetail, type GameDetailState } from "../../utils/gameDetailStore";
import { useDownloads } from "../../utils/downloadStore";
import { useRommConnectionState } from "../../utils/connectionState";
import { isBoundVanished, onBoundAvailabilityChange } from "../../utils/vanishedBinding";
import { runDownloadWithAdoption } from "../../utils/adoptFlow";
import { executeDownloadResume } from "../../utils/downloadResume";
import { handleButtonDownloadFailure } from "../../utils/downloadFailure";
import { NO_FILE_ON_ROMM_LABEL } from "../../utils/noFileOnRommWording";
import { readGameRunning } from "../../utils/sessionManager";
import { hasAnySaveConflict } from "../../utils/saveStatus";
import { usePruneLeaseOwner } from "../../utils/pruneLease";
import { useStrandedAnswer } from "../../utils/strandedPanelStore";
import { startRefusal } from "../../utils/emulatorStart";
import { detach } from "../../utils/detach";
import type {
  DownloadCompleteEvent,
  DownloadFailedEvent,
  DownloadItem,
  DownloadProgressEvent,
  SaveStatus,
  UninstallProgressEvent,
} from "../../types";
import type { AskDialog } from "./dialogs/askDialog";
import { desktopAdoptionDialogs } from "./dialogs/desktopDialogs";
import { useDialogHost } from "./dialogs/useDialogHost";
import { CONTAINER_STYLE, BUTTON_GROUP_STYLE, BUTTON_BASE_STYLE } from "./styles";
import { DiscSelector } from "./DiscSelector";
import { DownloadingButton } from "./DownloadingButton";
import { PLAY_GRADIENT, PlayStateButton } from "./PlayStateButton";
import { SvgThrobber } from "./SvgThrobber";
import { usePlayLaunch, type ActionState, type HeldState, type PressState } from "./usePlayLaunch";

interface PlayButtonProps {
  appId: number;
}

/**
 * A verdict this button reached itself — a conflict left unresolved, one just
 * resolved, a game just downloaded, adopted or uninstalled — held only until the
 * shared detail moves on. The ROM and the detail it was reached against are kept
 * with it, so a version switch, or the next save status or install state the
 * store folds in, replaces the verdict rather than sitting under it.
 */
interface HeldVerdict {
  state: HeldState;
  romId: number | null;
  saveStatus: SaveStatus | null;
  installed: boolean;
}

/**
 * What the cached detail answered for this ROM, as last read or proven: what
 * lies on this device where the download would go, and whether RomM holds a file.
 */
interface CachedAnswers {
  romId: number | null;
  targetOccupied: boolean;
  candidatePresent: boolean;
  /** RomM holds no file for the ROM, so there is nothing to download. */
  noFileOnRomm: boolean;
}

const NO_ANSWERS: CachedAnswers = { romId: null, targetOccupied: false, candidatePresent: false, noFileOnRomm: false };

/** What a press or a download's completion put on the button, and the ROM it is for; another ROM's reads as none. */
interface PressedAction {
  state: ActionState;
  romId: number;
}

/** How far a multi-file removal has got, as Big Picture's Uninstalling count reads it. */
interface UninstallCount {
  romId: number;
  removed: number;
  total: number;
}

/** How long READY! stands once a download completes; Big Picture's Ready! stands as long. */
const READY_MS = 1100;

const STOP_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_BASE_STYLE,
  width: "100%",
  borderRadius: "2px",
  background: "linear-gradient(90deg, #47bfff 0%, #1a44c2 100%)",
};

const CONFLICT_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_BASE_STYLE,
  width: "100%",
  borderRadius: "2px",
  background: "linear-gradient(90deg, #d4a017 0%, #b8860b 100%)",
  fontSize: "13px",
  whiteSpace: "nowrap",
};

const PLACEHOLDER_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_BASE_STYLE,
  width: "100%",
  borderRadius: "2px",
  background: "linear-gradient(90deg, #4a5968 0%, #3a4754 100%)",
  cursor: "not-allowed",
  opacity: 0.7,
};

const READY_BUTTON_STYLE: CSSProperties = {
  ...PLACEHOLDER_BUTTON_STYLE,
  background: PLAY_GRADIENT,
  cursor: "default",
  opacity: 1,
  filter: "brightness(1.2)",
};

const UNINSTALLING_BUTTON_STYLE: CSSProperties = {
  ...PLACEHOLDER_BUTTON_STYLE,
  background: "#2a3f5a",
  color: "#8fa3b8",
  opacity: 1,
};

const DOWNLOAD_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_BASE_STYLE,
  width: "100%",
  borderRadius: "2px",
  background: "linear-gradient(90deg, #1a9fff 0%, #0078d4 100%)",
};

const DOWNLOAD_BUTTON_BLOCKED_STYLE: CSSProperties = {
  ...DOWNLOAD_BUTTON_STYLE,
  cursor: "not-allowed",
  opacity: 0.7,
};

const DOWNLOAD_BUTTON_OFFLINE_STYLE: CSSProperties = {
  ...DOWNLOAD_BUTTON_BLOCKED_STYLE,
  background: "linear-gradient(90deg, #4a5968 0%, #3a4754 100%)",
};

// A version switch keeps the previous version's save status until the new
// one's lands, so a status for another ROM counts as none yet.
function saveStatusOf(d: GameDetailState): SaveStatus | null {
  return d.saveStatus?.rom_id === d.romId ? d.saveStatus : null;
}

function actionStateFor(action: PressedAction | null, romId: number | null): ActionState | null {
  return action !== null && action.romId === romId ? action.state : null;
}

// The answers belong to the ROM they were read for and mean nothing once it
// is installed, so a version switch or an install retires them without a
// write. The two on-disk ones stay two flags because they are two states: an
// occupied target is compared where it lies, a candidate is renamed into place.
function answersFor(answers: CachedAnswers, romId: number | null, installed: boolean): CachedAnswers {
  return answers.romId !== null && answers.romId === romId && !installed ? answers : NO_ANSWERS;
}

function isTransferActive(download: DownloadItem | undefined): boolean {
  return (
    download !== undefined &&
    (download.status === "downloading" ||
      download.status === "extracting" ||
      download.status === "queued" ||
      download.status === "paused")
  );
}

function downloadRatio(download: DownloadItem | undefined): number {
  const downloaded = download?.bytes_downloaded ?? 0;
  const total = download?.total_bytes ?? 0;
  return total > 0 ? Math.min(1, Math.max(0, downloaded / total)) : 0;
}

function uninstallingLabel(progress: UninstallCount | null, romId: number | null): string {
  return progress !== null && progress.romId === romId
    ? `UNINSTALLING ${progress.removed}/${progress.total}`
    : "UNINSTALLING...";
}

const StopButton: FC<{ pending: boolean; onStop: () => void }> = ({ pending, onStop }) => (
  <div className="tender-desktop-play-btn-group" style={BUTTON_GROUP_STYLE}>
    <button
      type="button"
      className="tender-desktop-btn-stop"
      disabled={pending}
      style={pending ? { ...STOP_BUTTON_STYLE, cursor: "default" } : STOP_BUTTON_STYLE}
      onClick={onStop}
    >
      <svg width="12" height="12" viewBox="0 0 10 10" fill="currentColor" aria-hidden="true">
        <rect width="10" height="10" rx="1" />
      </svg>
      {pending ? "STOPPING..." : "STOP"}
    </button>
  </div>
);

// The dialog host sits above the button so that the button's branches, which
// render different trees, cannot unmount an open dialog when the state moves.
export const PlayButton: FC<PlayButtonProps> = ({ appId }) => {
  const dialogs = useDialogHost();
  return (
    <>
      <PlayButtonControls appId={appId} ask={dialogs.ask} />
      {dialogs.element}
    </>
  );
};

const PlayButtonControls: FC<PlayButtonProps & { ask: AskDialog }> = ({ appId, ask }) => {
  const detail = useGameDetail(appId);
  const romId = detail.romId;
  const downloads = useDownloads();
  const stranded = useStrandedAnswer() !== null;
  const isOffline = useRommConnectionState() === "offline";

  const [pressState, setPressState] = useState<PressState | null>(null);
  const [switching, setSwitching] = useState(false);
  // A download's, an adoption's and an uninstall's state is kept with the ROM it
  // was pressed for: docs/architecture/desktop-dom-architecture.md says what that covers.
  const [action, setAction] = useState<PressedAction | null>(null);
  const actionState = actionStateFor(action, romId);
  // Another version's press bar is dropped: a transfer that ends while that version
  // is not shown leaves no frame to end it, and on the way back the bar is drawn from
  // what the download store still holds.
  if (action !== null && action.state === "downloading" && action.romId !== romId) setAction(null);
  // The last session start or stop seen for a ROM, whatever made it. A start
  // made elsewhere (Steam's own Play, a context menu, a `steam://` link) changes
  // nothing else this button renders from, so the event is held here.
  const [sessionSeen, setSessionSeen] = useState<{ romId: number; running: boolean } | null>(null);
  const [heldVerdict, setHeldVerdict] = useState<HeldVerdict | null>(null);
  const [stopPending, setStopPending] = useState(false);
  // The ROM a download press's request, or an adoption, is in flight for.
  const [pendingRomId, setPendingRomId] = useState<number | null>(null);
  const actionPending = pendingRomId !== null && pendingRomId === romId;
  // Not `actionPending`: the flow's busy hook is false during most of its questions,
  // which is when a question from the selector would dismiss the open one.
  const [downloadFlowUnderWay, setDownloadFlowUnderWay] = useState(false);
  const [cachedAnswers, setCachedAnswers] = useState<CachedAnswers>(NO_ANSWERS);
  const [uninstallProgress, setUninstallProgress] = useState<UninstallCount | null>(null);
  const boundVanished = useSyncExternalStore(onBoundAvailabilityChange, () => isBoundVanished(appId));
  const detailRef = useRef(detail);
  useEffect(() => {
    detailRef.current = detail;
  });
  const leaseOwner = `desktop-play-button:${appId}`;
  usePruneLeaseOwner(leaseOwner);

  const startAction = useCallback((state: ActionState, rid: number) => setAction({ state, romId: rid }), []);
  const endAction = useCallback(
    (state: ActionState, rid: number) =>
      setAction((prev) => (prev !== null && prev.state === state && prev.romId === rid ? null : prev)),
    [],
  );

  const { targetOccupied, candidatePresent, noFileOnRomm } = answersFor(cachedAnswers, romId, detail.installed);
  const noteAnswers = (rid: number, update: Partial<Omit<CachedAnswers, "romId">>) => {
    setCachedAnswers((prev) => {
      const base = prev.romId === rid ? prev : { ...NO_ANSWERS, romId: rid };
      return { ...base, ...update };
    });
  };

  // The store's detail does not carry these answers; the cached detail it was
  // read from does, and re-reading it is network-free. Re-read whenever the ROM
  // or its install state changes, which is also when the store reloads.
  useEffect(() => {
    if (!romId || detail.installed) return;
    let cancelled = false;
    getCachedGameDetail(appId)
      .then((cached) => {
        if (cancelled || !cached.found || cached.rom_id !== romId || cached.installed) return;
        setCachedAnswers({
          romId,
          targetOccupied: cached.target_path_occupied === true,
          candidatePresent: cached.adoption_candidate_present === true,
          noFileOnRomm: cached.has_file_on_disk === false,
        });
      })
      .catch((e) => {
        detach(debugLog(`DesktopPlayButton: on-disk read failed: ${e}`));
      });
    return () => {
      cancelled = true;
    };
  }, [appId, romId, detail.installed]);

  const saveStatus = saveStatusOf(detail);
  // A verdict reached for a ROM the page no longer shows is not held at all.
  const holdVerdict = useCallback((state: HeldVerdict["state"], forRomId: number) => {
    const latest = detailRef.current;
    if (latest.romId !== forRomId) return;
    setHeldVerdict({ state, romId: latest.romId, saveStatus: saveStatusOf(latest), installed: latest.installed });
  }, []);
  const heldVerdictApplies =
    heldVerdict !== null &&
    heldVerdict.romId === romId &&
    heldVerdict.saveStatus === saveStatus &&
    heldVerdict.installed === detail.installed;
  // Once the detail has moved on, the verdict is given up for good: a detail
  // that later reads as it did then (installed again after an uninstall) is news
  // of its own, not the moment the verdict was reached.
  if (heldVerdict !== null && !heldVerdictApplies) setHeldVerdict(null);

  useEffect(() => {
    if (!romId) return;
    const onSessionChanged = (e: WindowEventMap["romm_session_changed"]) => {
      if (e.detail.romId !== romId) return;
      setSessionSeen({ romId, running: e.detail.running });
      // A session's end is what says a start is no longer under way.
      if (!e.detail.running) setPressState((prev) => (prev === "launching" ? null : prev));
    };
    globalThis.addEventListener("romm_session_changed", onSessionChanged);
    return () => globalThis.removeEventListener("romm_session_changed", onSessionChanged);
  }, [romId]);
  const sessionRunning =
    romId !== null && (sessionSeen?.romId === romId ? sessionSeen.running : readGameRunning(appId, romId).running);

  // The download's and the uninstall's events. A download's toasts are index.tsx's
  // global listeners'; these only move the button.
  const readyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!romId) return;
    const onComplete = addEventListener<DownloadCompleteEvent>("download_complete", (e) => {
      if (e.rom_id !== romId) return;
      holdVerdict("play", romId);
      startAction("dl_complete", romId);
      if (readyTimerRef.current !== null) clearTimeout(readyTimerRef.current);
      readyTimerRef.current = setTimeout(() => {
        readyTimerRef.current = null;
        endAction("dl_complete", romId);
      }, READY_MS);
    });
    const onProgress = addEventListener<DownloadProgressEvent>("download_progress", (e) => {
      if (e.rom_id !== romId) return;
      // Once its frames arrive the download store carries the transfer, so the
      // press's own bar gives way to the store's; a cancelled or failed frame ends it.
      endAction("downloading", romId);
      if (e.status === "cancelled" || e.status === "failed") {
        // A cancelled or failed replace-download may already have removed what was found.
        setCachedAnswers(NO_ANSWERS);
      }
    });
    const onFailed = addEventListener<DownloadFailedEvent>("download_failed", (e) =>
      handleButtonDownloadFailure(e, romId, () => {
        endAction("downloading", romId);
        setCachedAnswers(NO_ANSWERS);
      }),
    );
    const onUninstallProgress = addEventListener<UninstallProgressEvent>("uninstall_progress", (e) => {
      if (e.rom_id !== romId) return;
      setUninstallProgress({ romId, removed: e.files_removed, total: e.files_total });
    });
    return () => {
      removeEventListener("download_complete", onComplete);
      removeEventListener("download_progress", onProgress);
      removeEventListener("download_failed", onFailed);
      removeEventListener("uninstall_progress", onUninstallProgress);
    };
  }, [romId, holdVerdict, startAction, endAction]);
  useEffect(
    () => () => {
      if (readyTimerRef.current !== null) clearTimeout(readyTimerRef.current);
    },
    [],
  );

  const startCheckFailed = detail.startCheck?.state === "failed";
  const startChecking = detail.startCheck?.state === "checking";
  // The desktop game page has no emulator menu, so the sentence points at none.
  const playRefusal =
    startCheckFailed || detail.emulatorStart === null
      ? null
      : startRefusal(detail.emulatorStart, detail.gameLaunch ?? null, false);
  const { handlePlayClick, handleResolveConflictClick, handleStopClick, handleUninstallClick } = usePlayLaunch({
    appId,
    romId,
    romName: detail.romName,
    pressState,
    startCheckFailed,
    ask,
    leaseOwner,
    setPressState,
    startAction,
    endAction,
    clearSessionRunning: () => {
      if (romId !== null) setSessionSeen({ romId, running: false });
    },
    holdVerdict,
    setStopPending,
  });

  const uninstall = () => {
    setUninstallProgress(null);
    detach(handleUninstallClick());
  };

  const activeDownload = romId === null ? undefined : downloads.find((d) => d.rom_id === romId);
  const downloading = actionState === "downloading" || isTransferActive(activeDownload);

  const clearPendingFor = (rid: number) => setPendingRomId((prev) => (prev === rid ? null : prev));

  const handleDownloadClick = async () => {
    if (romId === null) return;
    const rid = romId;
    setDownloadFlowUnderWay(true);
    try {
      const outcome = await runDownloadWithAdoption({
        romId: rid,
        romName: detail.romName,
        pageSawCandidate: candidatePresent,
        logContext: "DesktopPlayButton",
        dialogs: desktopAdoptionDialogs(ask),
        hooks: {
          setBusy: (busy) => {
            // prettier-ignore
            if (busy) setPendingRomId(rid); // NOSONAR(typescript:S2301) — adoptFlow's shared hook takes one boolean
            else clearPendingFor(rid);
          },
          setTargetOccupied: (occupied) => noteAnswers(rid, { targetOccupied: occupied }),
          setCandidatePresent: (present) => noteAnswers(rid, { candidatePresent: present }),
          onAdopted: () => holdVerdict("play", rid),
        },
      });
      if (outcome === "download_started") {
        startAction("downloading", rid);
        clearPendingFor(rid);
      }
    } finally {
      setDownloadFlowUnderWay(false);
    }
  };

  // Fire-and-forget, as Big Picture's: the backend's cancelled frame is what
  // takes the transfer out of the download store.
  const handleCancelClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (!romId) return;
    detach(cancelDownload(romId).catch((err) => detach(debugLog(`DesktopPlayButton: cancel failed: ${err}`))));
    endAction("downloading", romId);
    setCachedAnswers(NO_ANSWERS);
  };

  const handlePauseResumeClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (!romId || !activeDownload) return;
    if (activeDownload.status === "paused") detach(executeDownloadResume(romId));
    else detach(pauseDownload(romId).catch((err) => detach(debugLog(`DesktopPlayButton: pause failed: ${err}`))));
  };

  if (romId === null) return null;
  // Nothing on a stranded panel: docs/architecture/invariants/panel.md's stranded-panel entry says why.
  if (stranded) return null;

  const placeholder = (label: string, title?: string, style: CSSProperties = PLACEHOLDER_BUTTON_STYLE) => (
    <div className="tender-desktop-play-btn-group" style={BUTTON_GROUP_STYLE}>
      <button type="button" disabled style={style} {...(title === undefined ? {} : { title })}>
        {label}
      </button>
    </div>
  );

  const renderDownloadButton = () => {
    // A bound version gone from RomM, or a ROM RomM holds no file for, has
    // nothing to download, as on Big Picture's.
    const blocked = boundVanished || noFileOnRomm;
    const usesExisting = !isOffline && !actionPending && !noFileOnRomm && (targetOccupied || candidatePresent);
    let label = "DOWNLOAD";
    if (isOffline) label = "OFFLINE";
    else if (actionPending) label = "STARTING...";
    else if (noFileOnRomm) label = NO_FILE_ON_ROMM_LABEL.toUpperCase();
    else if (usesExisting) label = "USE EXISTING FILES";

    let style = DOWNLOAD_BUTTON_STYLE;
    if (isOffline) style = DOWNLOAD_BUTTON_OFFLINE_STYLE;
    else if (actionPending || blocked) style = DOWNLOAD_BUTTON_BLOCKED_STYLE;
    else if (switching) style = { ...DOWNLOAD_BUTTON_STYLE, cursor: "default" };

    return (
      <div className="tender-desktop-play-btn-group" style={BUTTON_GROUP_STYLE}>
        <button
          type="button"
          className="tender-desktop-btn-download"
          disabled={isOffline || actionPending || blocked || switching}
          style={usesExisting ? { ...style, fontSize: "13px" } : style}
          onClick={() => detach(handleDownloadClick())}
        >
          {switching ? (
            <SvgThrobber />
          ) : (
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <path
                d="M7 1V9M7 9L3.5 5.5M7 9L10.5 5.5M1 11H13M1 13H13"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          )}
          {label}
        </button>
      </div>
    );
  };

  const renderButtonGroup = () => {
    // No RESUME: docs/architecture/desktop-dom-architecture.md says why.
    if (sessionRunning) return <StopButton pending={stopPending} onStop={() => detach(handleStopClick())} />;

    if (pressState !== null) {
      return (
        <PlayStateButton
          pressState={pressState}
          refusal={null}
          checkFailed={false}
          onPlay={() => detach(handlePlayClick())}
          onUninstall={uninstall}
        />
      );
    }

    if (actionState === "uninstalling") {
      return placeholder(uninstallingLabel(uninstallProgress, romId), undefined, UNINSTALLING_BUTTON_STYLE);
    }
    if (actionState === "dl_complete") return placeholder("READY!", undefined, READY_BUTTON_STYLE);
    if (downloading) {
      return (
        <DownloadingButton
          progressRatio={downloadRatio(activeDownload)}
          isExtracting={activeDownload?.status === "extracting"}
          isPaused={activeDownload?.status === "paused"}
          isResumable={activeDownload?.resumable ?? false}
          onPauseResume={handlePauseResumeClick}
          onCancel={handleCancelClick}
        />
      );
    }

    const held = heldVerdictApplies ? heldVerdict.state : null;
    if (held === "download" || (!detail.installed && held !== "play")) return renderDownloadButton();
    if (detail.fileMissingAt !== null) return placeholder("FILE MISSING", detail.fileMissingAt);

    const conflict = held === null ? saveStatus !== null && hasAnySaveConflict(saveStatus) : held === "conflict";
    if (conflict) {
      return (
        <div className="tender-desktop-play-btn-group" style={BUTTON_GROUP_STYLE}>
          <button
            type="button"
            className="tender-desktop-btn-conflict"
            disabled={switching}
            style={switching ? { ...CONFLICT_BUTTON_STYLE, cursor: "default" } : CONFLICT_BUTTON_STYLE}
            onClick={() => detach(handleResolveConflictClick())}
          >
            {switching && <SvgThrobber />}
            RESOLVE CONFLICT
          </button>
        </div>
      );
    }

    if (startChecking) return placeholder("CHECKING...");

    return (
      <PlayStateButton
        pressState={null}
        refusal={playRefusal}
        checkFailed={startCheckFailed}
        switching={switching}
        onPlay={() => detach(handlePlayClick())}
        onUninstall={uninstall}
      />
    );
  };

  return (
    <div className="tender-desktop-play-btn-container" style={CONTAINER_STYLE}>
      {renderButtonGroup()}
      <DiscSelector
        appId={appId}
        ask={ask}
        fileMissing={detail.fileMissingAt !== null}
        pressUnderWay={(pressState !== null && pressState !== "launching") || downloadFlowUnderWay}
        onSwitchingChange={setSwitching}
      />
    </div>
  );
};
