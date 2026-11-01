/**
 * Tender's Play button on a RomM game's page in the desktop client, drawn in
 * place of Steam's own. Its presses are `usePlayLaunch`'s.
 */

import { useEffect, useRef, useState, type CSSProperties, type FC } from "react";
import { useGameDetail } from "../../utils/gameDetailStore";
import { useDownloads } from "../../utils/downloadStore";
import { readGameRunning } from "../../utils/sessionManager";
import { hasAnySaveConflict } from "../../utils/saveStatus";
import { usePruneLeaseOwner } from "../../utils/pruneLease";
import { useStrandedAnswer } from "../../utils/strandedPanelStore";
import { startRefusal } from "../../utils/emulatorStart";
import { detach } from "../../utils/detach";
import type { SaveStatus } from "../../types";
import type { AskDialog } from "./dialogs/askDialog";
import { useDialogHost } from "./dialogs/useDialogHost";
import { CONTAINER_STYLE, BUTTON_GROUP_STYLE, BUTTON_BASE_STYLE } from "./styles";
import { PlayStateButton } from "./PlayStateButton";
import { usePlayLaunch, type PressState } from "./usePlayLaunch";

interface PlayButtonProps {
  appId: number;
}

/**
 * A verdict this button reached itself — a conflict left unresolved, one just
 * resolved — held only until the shared detail moves on. The ROM and the detail
 * it was reached against are kept with it, so a version switch, or the next save
 * status or install state the store folds in, replaces the verdict rather than
 * sitting under it.
 */
interface HeldVerdict {
  state: "play" | "conflict";
  romId: number | null;
  saveStatus: SaveStatus | null;
  installed: boolean;
}

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
};

const PLACEHOLDER_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_BASE_STYLE,
  width: "100%",
  borderRadius: "2px",
  background: "linear-gradient(90deg, #4a5968 0%, #3a4754 100%)",
  cursor: "not-allowed",
  opacity: 0.7,
};

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

  const [pressState, setPressState] = useState<PressState | null>(null);
  // The last session start or stop seen for a ROM, whatever made it. A start
  // made elsewhere (Steam's own Play, a context menu, a `steam://` link) changes
  // nothing else this button renders from, so the event is held here.
  const [sessionSeen, setSessionSeen] = useState<{ romId: number; running: boolean } | null>(null);
  const [heldVerdict, setHeldVerdict] = useState<HeldVerdict | null>(null);
  const [stopPending, setStopPending] = useState(false);
  const detailRef = useRef(detail);
  useEffect(() => {
    detailRef.current = detail;
  });
  const leaseOwner = `desktop-play-button:${appId}`;
  usePruneLeaseOwner(leaseOwner);

  // A version switch keeps the previous version's save status until the new
  // one's lands, so a status for another ROM counts as none yet.
  const saveStatusOf = (d: typeof detail) => (d.saveStatus?.rom_id === d.romId ? d.saveStatus : null);
  const saveStatus = saveStatusOf(detail);
  const holdVerdict = (state: HeldVerdict["state"]) => {
    const latest = detailRef.current;
    setHeldVerdict({ state, romId: latest.romId, saveStatus: saveStatusOf(latest), installed: latest.installed });
  };
  const heldVerdictApplies =
    heldVerdict !== null &&
    heldVerdict.romId === romId &&
    heldVerdict.saveStatus === saveStatus &&
    heldVerdict.installed === detail.installed;

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

  const startCheckFailed = detail.startCheck?.state === "failed";
  const startChecking = detail.startCheck?.state === "checking";
  // The desktop game page has no emulator menu, so the sentence points at none.
  const playRefusal =
    startCheckFailed || detail.emulatorStart === null
      ? null
      : startRefusal(detail.emulatorStart, detail.gameLaunch ?? null, false);
  const { handlePlayClick, handleResolveConflictClick, handleStopClick } = usePlayLaunch({
    appId,
    romId,
    pressState,
    startCheckFailed,
    ask,
    leaseOwner,
    setPressState,
    clearSessionRunning: () => {
      if (romId !== null) setSessionSeen({ romId, running: false });
    },
    holdVerdict,
    setStopPending,
  });

  if (romId === null) return null;
  // PLAY, STOP and RESOLVE CONFLICT each need the backend to do what they say,
  // and a stranded panel cannot reach it.
  if (stranded) return null;

  const placeholder = (label: string, title?: string) => (
    <div className="tender-desktop-play-btn-group" style={BUTTON_GROUP_STYLE}>
      <button type="button" disabled style={PLACEHOLDER_BUTTON_STYLE} {...(title === undefined ? {} : { title })}>
        {label}
      </button>
    </div>
  );

  const transferActive = downloads.some(
    (d) =>
      d.rom_id === romId &&
      (d.status === "downloading" || d.status === "extracting" || d.status === "queued" || d.status === "paused"),
  );

  const renderButtonGroup = () => {
    // No RESUME: docs/architecture/desktop-dom-architecture.md says why.
    if (sessionRunning) {
      return (
        <div className="tender-desktop-play-btn-group" style={BUTTON_GROUP_STYLE}>
          <button
            type="button"
            className="tender-desktop-btn-stop"
            disabled={stopPending}
            style={stopPending ? { ...STOP_BUTTON_STYLE, cursor: "default" } : STOP_BUTTON_STYLE}
            onClick={() => detach(handleStopClick())}
          >
            <svg width="12" height="12" viewBox="0 0 10 10" fill="currentColor" aria-hidden="true">
              <rect width="10" height="10" rx="1" />
            </svg>
            {stopPending ? "STOPPING..." : "STOP"}
          </button>
        </div>
      );
    }

    if (pressState !== null) {
      return (
        <PlayStateButton
          pressState={pressState}
          refusal={null}
          checkFailed={false}
          onPlay={() => detach(handlePlayClick())}
        />
      );
    }

    if (transferActive) return placeholder("DOWNLOADING...");
    if (!detail.installed) return placeholder("NOT INSTALLED");
    if (detail.fileMissingAt !== null) return placeholder("FILE MISSING", detail.fileMissingAt);

    const conflict = heldVerdictApplies
      ? heldVerdict.state === "conflict"
      : saveStatus !== null && hasAnySaveConflict(saveStatus);
    if (conflict) {
      return (
        <div className="tender-desktop-play-btn-group" style={BUTTON_GROUP_STYLE}>
          <button
            type="button"
            className="tender-desktop-btn-conflict"
            style={CONFLICT_BUTTON_STYLE}
            onClick={() => detach(handleResolveConflictClick())}
          >
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
        onPlay={() => detach(handlePlayClick())}
      />
    );
  };

  return (
    <div className="tender-desktop-play-btn-container" style={CONTAINER_STYLE}>
      {renderButtonGroup()}
    </div>
  );
};
