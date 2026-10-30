/**
 * The desktop Play button's PLAY, and the press's progress — CHECKING SAVES...,
 * SYNCING SAVES..., LAUNCHING... — in its place.
 */

import type { CSSProperties, FC } from "react";
import type { PressState } from "./usePlayLaunch";
import { BUTTON_GROUP_STYLE, BUTTON_BASE_STYLE } from "./styles";

const PLAY_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_BASE_STYLE,
  width: "100%",
  // The stops of Steam's own PLAY gradient in the desktop client, read in
  // DevTools (Steam client build 1792618285, CachyOS), drawn across the whole
  // button; Steam's and Big Picture's Play show a stretch of it instead.
  background: "linear-gradient(to right, #70d61d 0%, #01a75b 60%)",
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

const PRESS_LABEL: Record<PressState, string> = {
  checking: "CHECKING SAVES...",
  syncing: "SYNCING SAVES...",
  launching: "LAUNCHING...",
};

interface PlayStateButtonProps {
  pressState: PressState | null;
  /** The sentence PLAY is refused with, as its title; `null` where nothing refuses it. */
  refusal: string | null;
  /** Whether the page's check of whether the game can start went wrong. */
  checkFailed: boolean;
  onPlay: () => void;
}

function playStyle(refusal: string | null, checkFailed: boolean): CSSProperties {
  if (refusal !== null) return REFUSED_PLAY_BUTTON_STYLE;
  return checkFailed ? CHECK_FAILED_PLAY_BUTTON_STYLE : PLAY_BUTTON_STYLE;
}

export const PlayStateButton: FC<PlayStateButtonProps> = ({ pressState, refusal, checkFailed, onPlay }) => (
  <div className="tender-desktop-play-btn-group" style={BUTTON_GROUP_STYLE}>
    <button
      type="button"
      className="tender-desktop-btn-play"
      disabled={pressState !== null || refusal !== null}
      style={playStyle(refusal, checkFailed)}
      {...(refusal === null ? {} : { title: refusal })}
      onClick={onPlay}
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
        <path d="M3 2L12 7L3 12V2Z" />
      </svg>
      {pressState === null ? "PLAY" : PRESS_LABEL[pressState]}
    </button>
  </div>
);
