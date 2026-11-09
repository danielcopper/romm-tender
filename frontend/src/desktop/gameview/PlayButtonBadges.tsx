/**
 * The badges beside Tender's Play button on a RomM game's page in the desktop
 * client. What each reads is docs/architecture/desktop-dom-architecture.md's.
 */

import { useEffect, useReducer, useState, type CSSProperties, type FC, type ReactNode } from "react";
import { debugLog, getSaveSetupInfo, getSaveSyncAvailability, isSaveTrackingConfigured } from "../../api/backend";
import type { SaveSetupInfo, SaveStatus } from "../../types";
import { BIOS_MISSING_RED } from "../../utils/biosColor";
import { detach } from "../../utils/detach";
import { formatBytes, formatTimeAgo } from "../../utils/formatters";
import type { GameDetailState } from "../../utils/gameDetailStore";
import { useGamePlaytime } from "../../utils/playtimeReconcile";
import { saveSyncBadge, type SaveSyncBadgeTone } from "../../utils/saveSyncBadge";

interface PlayButtonBadgesProps {
  appId: number;
  detail: GameDetailState;
  /** This ROM's save status, or `null`: `PlayButton`'s `saveStatusOf`, never the one in `detail`. */
  saveStatus: SaveStatus | null;
  isOffline: boolean;
}

// The row's type and spacing are those of Steam's own play bar; the doc above says where they were measured.
const ROW_STYLE: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  height: "46px",
  minWidth: "0",
  marginLeft: "10px",
  overflow: "hidden",
};

const CELL_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flexShrink: 0,
  boxSizing: "border-box",
  marginInline: "5px",
  padding: "6px",
  paddingInlineStart: "11px",
  userSelect: "none",
  whiteSpace: "nowrap",
};

const HEADER_STYLE: CSSProperties = {
  fontSize: "14px",
  fontWeight: 400,
  letterSpacing: "1px",
  textTransform: "uppercase",
  color: "rgba(255, 255, 255, 0.52)",
  lineHeight: "16px",
};

const VALUE_STYLE: CSSProperties = {
  fontSize: "13px",
  fontWeight: 400,
  color: "rgba(255, 255, 255, 0.32)",
  lineHeight: "normal",
  display: "flex",
  alignItems: "center",
  gap: "6px",
};

const DOT_STYLE: CSSProperties = {
  display: "inline-block",
  width: "8px",
  height: "8px",
  borderRadius: "50%",
  flexShrink: 0,
};

const TONE_COLOR: Record<SaveSyncBadgeTone, string> = {
  off: "#8f98a0",
  ok: "#5ba32b",
  attention: "#d4a72c",
  unavailable: BIOS_MISSING_RED,
};

const Badge: FC<{ name: string; header: string; color?: string; children: ReactNode }> = ({
  name,
  header,
  color,
  children,
}) => (
  <div className={`tender-desktop-badge-item tender-desktop-${name}`} style={CELL_STYLE}>
    <div style={HEADER_STYLE}>{header}</div>
    <div style={color === undefined ? VALUE_STYLE : { ...VALUE_STYLE, color }}>{children}</div>
  </div>
);

const Dot: FC<{ color: string }> = ({ color }) => (
  <span className="tender-desktop-badge-dot" style={{ ...DOT_STYLE, backgroundColor: color }} />
);

type DataChange = WindowEventMap["romm_data_changed"]["detail"];

function changesSaveAnswers(change: DataChange, romId: number): boolean {
  if (change.type === "save_sync") return change.rom_id === undefined || change.rom_id === romId;
  return change.type === "core_changed" || change.type === "emulator_sources";
}

/**
 * One read about the ROM shown: asked while it is wanted and again on a change
 * that may move it, ordered by a sequence, and tagged with its ROM so another
 * version's answer reads as none. A read that fails holds no answer rather than
 * the one from before, which may be about another emulator. `ask` is module-level.
 */
function useRomAnswer<T>(
  romId: number | null,
  wanted: boolean,
  what: string,
  ask: (romId: number) => Promise<T>,
): T | null {
  const [answer, setAnswer] = useState<{ romId: number; value: T } | null>(null);

  useEffect(() => {
    if (romId === null || !wanted) return;
    let cancelled = false;
    let latestRead = 0;
    const read = async () => {
      latestRead += 1;
      const thisRead = latestRead;
      try {
        const value = await ask(romId);
        if (!cancelled && thisRead === latestRead) setAnswer({ romId, value });
      } catch (e) {
        detach(debugLog(`DesktopPlayButtonBadges: ${what} read failed: ${e}`));
        if (!cancelled && thisRead === latestRead) setAnswer(null);
      }
    };
    detach(read());
    const onDataChanged = ({ detail }: WindowEventMap["romm_data_changed"]) => {
      if (changesSaveAnswers(detail, romId)) detach(read());
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => {
      cancelled = true;
      globalThis.removeEventListener("romm_data_changed", onDataChanged);
    };
  }, [romId, wanted, what, ask]);

  return answer !== null && answer.romId === romId ? answer.value : null;
}

/**
 * The setup answer while a slot is still to be set up, else `null`. It asks in
 * the launch gates' two steps because the second runs the backend's retry ladder
 * against RomM, which a page that merely opened has no reason to wait on or start.
 */
async function readSlotSetup(romId: number): Promise<SaveSetupInfo | null> {
  const tracking = await isSaveTrackingConfigured(romId);
  return tracking.setup_needed ? await getSaveSetupInfo(romId) : null;
}

/** Re-renders each minute while `active`, so a time-ago stays true on a page left open. */
function useMinuteTick(active: boolean): void {
  const [, tick] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(tick, 60_000);
    return () => clearInterval(timer);
  }, [active]);
}

export const PlayButtonBadges: FC<PlayButtonBadgesProps> = ({ appId, detail, saveStatus, isOffline }) => {
  const { romId, installed, downloadSizeBytes, saveSyncEnabled, saveSyncStatus, saveSyncLabel, saveSyncCheckedAt } =
    detail;
  const playtime = useGamePlaytime(appId, romId, "DesktopPlayButtonBadges");
  const readsSaves = saveSyncEnabled && installed && !isOffline;
  const setupInfo = useRomAnswer(romId, readsSaves, "save setup", readSlotSetup);
  const saveSyncAnswer = useRomAnswer(romId, readsSaves, "save sync availability", getSaveSyncAvailability);
  useMinuteTick(saveSyncEnabled);
  const label = saveSyncCheckedAt === null ? saveSyncLabel : (formatTimeAgo(saveSyncCheckedAt) ?? saveSyncLabel);
  const saveSync = saveSyncBadge({
    enabled: saveSyncEnabled,
    offline: isOffline,
    installed,
    status: saveSyncStatus,
    label,
    saveStatus,
    setupInfo,
    saveSyncAnswer: installed ? saveSyncAnswer : null,
  });
  const saveSyncColor = TONE_COLOR[saveSync.tone];

  return (
    <div className="tender-desktop-badges" style={ROW_STYLE}>
      {!installed && downloadSizeBytes !== null && downloadSizeBytes > 0 && (
        <Badge name="space-required" header="SPACE REQUIRED">
          {formatBytes(downloadSizeBytes)}
        </Badge>
      )}
      {playtime.lastPlayed && (
        <Badge name="last-played" header="LAST PLAYED">
          {playtime.lastPlayed}
        </Badge>
      )}
      {playtime.steamPlaytime && (
        <Badge name="playtime" header="PLAY TIME">
          {playtime.steamPlaytime}
        </Badge>
      )}
      <Badge name="save-sync" header="SAVE SYNC" color={saveSyncColor}>
        <Dot color={saveSyncColor} />
        <span>{saveSync.label}</span>
      </Badge>
      {detail.biosRequiredMissing && (
        <Badge name="bios" header="BIOS" color={BIOS_MISSING_RED}>
          <Dot color={BIOS_MISSING_RED} />
          <span>{detail.biosLabel}</span>
        </Badge>
      )}
    </div>
  );
};
