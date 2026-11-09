/**
 * The desktop Play button's place while the game downloads: the transfer's bar
 * and the presses beside it.
 */

import type { CSSProperties, FC, MouseEvent } from "react";
import { BUTTON_GROUP_STYLE, BUTTON_BASE_STYLE, SIDE_ACTION_STYLE } from "./styles";
import {
  getDownloadBaseBackground,
  getDownloadFillGradient,
  getDownloadPulseColor,
} from "../../utils/downloadProgress";

interface DownloadingButtonProps {
  /** Downloaded over total, from 0 to 1. */
  progressRatio: number;
  isExtracting: boolean;
  isPaused: boolean;
  isResumable: boolean;
  onPauseResume: (e: MouseEvent<HTMLButtonElement>) => void;
  onCancel: (e: MouseEvent<HTMLButtonElement>) => void;
}

function sideActionStyle(width: string, last: boolean): CSSProperties {
  return {
    ...SIDE_ACTION_STYLE,
    width,
    minWidth: width,
    maxWidth: width,
    flex: `0 0 ${width}`,
    ...(last ? {} : { borderRadius: 0 }),
  };
}

function progressLabel(percent: number, isExtracting: boolean, isPaused: boolean): string {
  if (isExtracting) return `Extracting… ${percent}%`;
  if (isPaused) return `Paused (${percent}%)`;
  return `${percent}%`;
}

export const DownloadingButton: FC<DownloadingButtonProps> = ({
  progressRatio,
  isExtracting,
  isPaused,
  isResumable,
  onPauseResume,
  onCancel,
}) => {
  const percent = Math.round(progressRatio * 100);
  const phase = { extracting: isExtracting, downloading: true };
  const glow = getDownloadPulseColor(progressRatio, { ...phase, paused: isPaused }, "desktop");
  const pausable = isResumable && !isExtracting;

  return (
    <div
      className="tender-desktop-play-btn-group"
      style={{ ...BUTTON_GROUP_STYLE, boxShadow: `0 0 10px ${glow}, 0 1px 4px rgba(0, 0, 0, 0.4)` }}
    >
      <div
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        style={{
          ...BUTTON_BASE_STYLE,
          position: "relative",
          overflow: "hidden",
          background: getDownloadBaseBackground(progressRatio, { ...phase, isOffline: false }),
          borderRadius: isExtracting ? "2px" : "2px 0 0 2px",
          padding: "0 10px",
        }}
      >
        <div
          className="tender-desktop-dl-fill"
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            bottom: 0,
            width: `${percent}%`,
            background: getDownloadFillGradient(progressRatio, phase, "desktop"),
            transition: "width 0.25s ease-out, background 0.25s ease-out",
          }}
        />
        <span
          style={{
            position: "relative",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            fontSize: "13px",
          }}
        >
          {progressLabel(percent, isExtracting, isPaused)}
        </span>
      </div>

      {pausable && (
        <button
          type="button"
          className="tender-desktop-dl-pause"
          title={isPaused ? "Resume download" : "Pause download"}
          aria-label={isPaused ? "Resume download" : "Pause download"}
          style={sideActionStyle("32px", false)}
          onClick={onPauseResume}
        >
          {isPaused ? (
            <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
              <path d="M2 1.5L10 6L2 10.5V1.5Z" />
            </svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
              <rect x="2" y="2" width="3" height="8" rx="0.5" />
              <rect x="7" y="2" width="3" height="8" rx="0.5" />
            </svg>
          )}
        </button>
      )}

      {!isExtracting && (
        <button
          type="button"
          className="tender-desktop-dl-cancel"
          title="Cancel download"
          aria-label="Cancel download"
          style={sideActionStyle(pausable ? "32px" : "36px", true)}
          onClick={onCancel}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <line x1="2" y1="2" x2="10" y2="10" strokeLinecap="round" />
            <line x1="10" y1="2" x2="2" y2="10" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </div>
  );
};
