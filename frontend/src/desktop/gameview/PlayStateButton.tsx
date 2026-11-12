/**
 * The desktop Play button's PLAY, and the press's progress — PREPARING...,
 * SYNCING SAVES..., LAUNCHING... — in its place, with the chevron that opens the
 * game's menu (Uninstall) beside it.
 */

import { useCallback, useRef, useState, type CSSProperties, type FC } from "react";
import { useOutsideClick } from "../../utils/useOutsideClick";
import { PLAY_GRADIENT } from "../../utils/playGradient";
import type { PressState } from "./usePlayLaunch";
import { BUTTON_GROUP_STYLE, BUTTON_BASE_STYLE, MENU_ITEM_STYLE, MENU_PANEL_STYLE, SIDE_ACTION_STYLE } from "./styles";
import { SvgThrobber } from "./SvgThrobber";

const PLAY_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_BASE_STYLE,
  width: "100%",
  background: PLAY_GRADIENT,
};

const MENU_STYLE: CSSProperties = {
  ...MENU_PANEL_STYLE,
  left: 0,
  right: 0,
  boxSizing: "border-box",
};

const UNINSTALL_ITEM_STYLE: CSSProperties = {
  ...MENU_ITEM_STYLE,
  background: "transparent",
  color: "#ff6b6b",
  cursor: "pointer",
};

const REFUSED_PLAY_BUTTON_STYLE: CSSProperties = {
  ...PLAY_BUTTON_STYLE,
  cursor: "not-allowed",
  opacity: 0.5,
};

// Grey and still pressable, as Big Picture's PLAY is where the page's check of
// whether the game can start went wrong.
const CHECK_FAILED_PLAY_BUTTON_STYLE: CSSProperties = {
  ...PLAY_BUTTON_STYLE,
  background: "linear-gradient(to right, #6b7b6b 0%, #5a6a5a 60%)",
};

const UNINSTALL_ELSEWHERE_TITLE = "Another version of this game is being uninstalled";

const PRESS_LABEL: Record<PressState, string> = {
  checking: "PREPARING...",
  syncing: "SYNCING SAVES...",
  launching: "LAUNCHING...",
};

interface PlayStateButtonProps {
  pressState: PressState | null;
  /** The sentence PLAY is refused with, as its title; `null` where nothing refuses it. */
  refusal: string | null;
  /** Whether the page's check of whether the game can start went wrong. */
  checkFailed: boolean;
  /** Whether a version switch is under way. */
  switching?: boolean;
  /** Whether a removal the page started for another version still runs. */
  uninstallUnderWay?: boolean;
  onPlay: () => void;
  onUninstall: () => void;
}

function playStyle(refusal: string | null, checkFailed: boolean, switching: boolean): CSSProperties {
  if (refusal !== null) return REFUSED_PLAY_BUTTON_STYLE;
  const style = checkFailed ? CHECK_FAILED_PLAY_BUTTON_STYLE : PLAY_BUTTON_STYLE;
  return switching ? { ...style, cursor: "default" } : style;
}

export const PlayStateButton: FC<PlayStateButtonProps> = ({
  pressState,
  refusal,
  checkFailed,
  switching = false,
  uninstallUnderWay = false,
  onPlay,
  onUninstall,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const groupRef = useRef<HTMLDivElement>(null);
  const closeMenu = useCallback(() => setMenuOpen(false), []);
  useOutsideClick(groupRef, closeMenu, menuOpen);
  // No Uninstall mid-start or mid-switch, nor while another version's removal runs,
  // which takes no second one.
  const menuRefused = pressState !== null || switching || uninstallUnderWay;
  if (menuRefused && menuOpen) setMenuOpen(false);
  const menuTitle = uninstallUnderWay ? UNINSTALL_ELSEWHERE_TITLE : "Game Options";

  return (
    <div
      ref={groupRef}
      className="tender-desktop-play-btn-group"
      style={{ ...BUTTON_GROUP_STYLE, zIndex: menuOpen ? 1000 : BUTTON_GROUP_STYLE.zIndex }}
    >
      <button
        type="button"
        className="tender-desktop-btn-play"
        disabled={pressState !== null || refusal !== null || switching}
        style={{ ...playStyle(refusal, checkFailed, switching), borderRadius: "2px 0 0 2px" }}
        {...(refusal === null ? {} : { title: refusal })}
        onClick={() => {
          setMenuOpen(false);
          onPlay();
        }}
      >
        {switching ? (
          <SvgThrobber />
        ) : (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
            <path d="M3 2L12 7L3 12V2Z" />
          </svg>
        )}
        {pressState === null ? "PLAY" : PRESS_LABEL[pressState]}
      </button>
      <button
        type="button"
        className="tender-desktop-menu-toggle"
        title={menuTitle}
        aria-label="Game Options"
        aria-expanded={menuOpen}
        disabled={menuRefused}
        style={menuRefused ? { ...SIDE_ACTION_STYLE, cursor: "default" } : SIDE_ACTION_STYLE}
        onClick={() => setMenuOpen((prev) => !prev)}
      >
        <svg width="10" height="6" viewBox="0 0 10 6" fill="none" aria-hidden="true">
          <path d="M1 1L5 5L9 1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {menuOpen && (
        <div className="tender-desktop-play-menu" role="menu" style={MENU_STYLE}>
          <button
            type="button"
            role="menuitem"
            className="tender-desktop-menu-item-uninstall"
            style={UNINSTALL_ITEM_STYLE}
            onClick={() => {
              setMenuOpen(false);
              onUninstall();
            }}
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              aria-hidden="true"
            >
              <path d="M1.5 3H10.5M4 3V1.5H8V3M4.5 5.5V9.5M7.5 5.5V9.5" strokeLinecap="round" />
              <path d="M2.5 3L3.2 10.2C3.25 10.65 3.65 11 4.1 11H7.9C8.35 11 8.75 10.65 8.8 10.2L9.5 3" />
            </svg>
            Uninstall
          </button>
        </div>
      )}
    </div>
  );
};
