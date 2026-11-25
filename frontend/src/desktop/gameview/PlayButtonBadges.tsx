/**
 * The badges beside Tender's Play button on a RomM game's page in the desktop
 * client. What each reads is docs/architecture/desktop-dom-architecture.md's.
 */

import { useEffect, useReducer, type CSSProperties, type FC, type ReactNode } from "react";
import { getSaveSetupInfo, getSaveSyncAvailability, isSaveTrackingConfigured } from "../../api/backend";
import type { SaveSetupInfo, SaveStatus } from "../../types";
import { BIOS_MISSING_RED } from "../../utils/biosColor";
import { formatBytes } from "../../utils/formatters";
import type { GameDetailState } from "../../utils/gameDetailStore";
import { useGamePlaytime } from "../../utils/playtimeReconcile";
import { saveSyncBadge, type SaveSyncBadgeTone } from "../../utils/saveSyncBadge";
import { ATTENTION_AMBER, DOT_STYLE, PLAY_BAR_ROW_HEIGHT, SYNCED_GREEN } from "./styles";
import { useRomAnswer } from "./useRomAnswer";

interface PlayButtonBadgesProps {
  appId: number;
  detail: Omit<GameDetailState, "saveStatus">;
  /** This ROM's save status, or `null` (`PlayButton`'s `saveStatusOf`). */
  saveStatus: SaveStatus | null;
  isOffline: boolean;
}

// The row's type and spacing are those of Steam's own play bar; the doc above says where they were measured.
const ROW_STYLE: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  height: PLAY_BAR_ROW_HEIGHT,
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

const BUTTON_CELL_STYLE: CSSProperties = {
  ...CELL_STYLE,
  backgroundColor: "transparent",
  border: "none",
  font: "inherit",
  textAlign: "left",
  cursor: "pointer",
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

const TONE_COLOR: Record<SaveSyncBadgeTone, string> = {
  off: "#8f98a0",
  ok: SYNCED_GREEN,
  attention: ATTENTION_AMBER,
  unavailable: BIOS_MISSING_RED,
};

const Badge: FC<{
  name: string;
  header: string;
  color?: string;
  onClick?: () => void;
  children: ReactNode;
}> = ({ name, header, color, onClick, children }) => {
  const className = `tender-desktop-badge-item tender-desktop-${name}`;
  const content = (
    <>
      <div style={HEADER_STYLE}>{header}</div>
      <div style={color === undefined ? VALUE_STYLE : { ...VALUE_STYLE, color }}>{children}</div>
    </>
  );
  if (onClick !== undefined) {
    return (
      <button type="button" className={className} style={BUTTON_CELL_STYLE} onClick={onClick}>
        {content}
      </button>
    );
  }
  return (
    <div className={className} style={CELL_STYLE}>
      {content}
    </div>
  );
};

const Dot: FC<{ color: string }> = ({ color }) => (
  <span className="tender-desktop-badge-dot" style={{ ...DOT_STYLE, backgroundColor: color }} />
);

const BADGES_LOG_OWNER = "DesktopPlayButtonBadges";

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
  const setupInfo = useRomAnswer(romId, readsSaves, readSlotSetup, { owner: BADGES_LOG_OWNER, what: "save setup" });
  const saveSyncAnswer = useRomAnswer(romId, readsSaves, getSaveSyncAvailability, {
    owner: BADGES_LOG_OWNER,
    what: "save sync availability",
  });
  useMinuteTick(saveSyncEnabled);
  const saveSync = saveSyncBadge({
    enabled: saveSyncEnabled,
    offline: isOffline,
    installed,
    status: saveSyncStatus,
    label: saveSyncLabel,
    checkedAt: saveSyncCheckedAt,
    saveStatus,
    setupInfo,
    saveSyncAnswer,
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
      <Badge
        name="save-sync"
        header="SAVE SYNC"
        color={saveSyncColor}
        onClick={() => {
          globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "saves" } }));
        }}
      >
        <Dot color={saveSyncColor} />
        <span>{saveSync.label}</span>
      </Badge>
      {detail.biosRequiredMissing && (
        <Badge
          name="bios"
          header="BIOS"
          color={BIOS_MISSING_RED}
          onClick={() => {
            globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "bios" } }));
          }}
        >
          <Dot color={BIOS_MISSING_RED} />
          <span>{detail.biosLabel}</span>
        </Badge>
      )}
    </div>
  );
};
