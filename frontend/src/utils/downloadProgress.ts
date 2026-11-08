/**
 * The download button's progress colours, its "x / y MB" text, and how long
 * Ready! stands once a download completes.
 */

import { PLAY_LEFT_STOP, PLAY_RIGHT_STOP, PLAY_RIGHT_STOP_AT, rgb, type RGB } from "./playGradient";

/** How long a game page's Play button reads Ready! once its download completes, in milliseconds. */
export const READY_FLASH_MS = 1100;

function lerpColor(a: RGB, b: RGB, t: number): string {
  const r = Math.round(a[0] + (b[0] - a[0]) * t);
  const g = Math.round(a[1] + (b[1] - a[1]) * t);
  const bl = Math.round(a[2] + (b[2] - a[2]) * t);
  return `rgb(${r}, ${g}, ${bl})`;
}

// Download button blue gradient stops
const BLUE_LEFT: RGB = [26, 159, 255]; // #1a9fff
const BLUE_RIGHT: RGB = [0, 120, 212]; // #0078d4
// Play button visible green (computed from gradient + backgroundSize 330% + backgroundPosition 25%)
const GREEN_LEFT: RGB = [80, 200, 47]; // #50c82f
const GREEN_RIGHT: RGB = [24, 177, 78]; // #18b14e

/** Which Play button the bar is drawn in: Big Picture's, or the desktop client's. */
type DownloadSurface = "bigpicture" | "desktop";

export interface DownloadPhase {
  extracting: boolean;
  downloading: boolean;
}

/** The filled part's gradient: blue turning green as `t` (downloaded / total) grows. */
export function getDownloadFillGradient(
  t: number,
  { extracting, downloading }: DownloadPhase,
  surface: DownloadSurface = "bigpicture",
): string {
  if (surface === "desktop" && (extracting || downloading)) {
    // The desktop fill ends on the desktop PLAY's own gradient.
    const left = lerpColor(BLUE_LEFT, PLAY_LEFT_STOP, extracting ? 1 : t);
    const right = lerpColor(BLUE_RIGHT, PLAY_RIGHT_STOP, extracting ? 1 : t);
    return `linear-gradient(to right, ${left} 0%, ${right} ${PLAY_RIGHT_STOP_AT})`;
  }
  if (extracting) {
    return `linear-gradient(to right, rgb(${GREEN_LEFT.join(",")}), rgb(${GREEN_RIGHT.join(",")}))`;
  }
  if (downloading) {
    return `linear-gradient(to right, ${lerpColor(BLUE_LEFT, GREEN_LEFT, t)}, ${lerpColor(BLUE_RIGHT, GREEN_RIGHT, t)})`;
  }
  return "linear-gradient(to right, #1a9fff, #0078d4)";
}

/**
 * The glow's colour — Big Picture's pulse, the desktop client's still glow:
 * amber while paused, otherwise following the fill.
 */
export function getDownloadPulseColor(
  t: number,
  { paused, extracting, downloading }: DownloadPhase & { paused: boolean },
  surface: DownloadSurface = "bigpicture",
): string {
  const green = surface === "desktop" ? PLAY_LEFT_STOP : GREEN_LEFT;
  if (paused) return "rgba(212,167,44,0.7)";
  if (extracting) return rgb(green);
  if (downloading) return lerpColor(BLUE_LEFT, green, t);
  return "rgba(26,159,255,0.7)";
}

/** The unfilled part's background. */
export function getDownloadBaseBackground(
  t: number,
  { isOffline, extracting, downloading }: DownloadPhase & { isOffline: boolean },
): string {
  if (isOffline) return "linear-gradient(to right, #6b7b8b, #5a6a7a)";
  if (extracting) return "linear-gradient(to right, #1a4d1a, #0f3320)";
  if (downloading) {
    return `linear-gradient(to right, ${lerpColor([10, 50, 90], [5, 35, 65], t)}, ${lerpColor([5, 35, 65], [5, 50, 30], t)})`;
  }
  return "linear-gradient(to right, #1a9fff, #0078d4)";
}

export function formatProgress(downloaded: number, total: number): string {
  // Show "x / y MB" with unit only on the total
  if (total < 1024) return `${downloaded} / ${total} B`;
  if (total < 1024 * 1024) return `${(downloaded / 1024).toFixed(1)} / ${(total / 1024).toFixed(1)} KB`;
  if (total < 1024 * 1024 * 1024)
    return `${(downloaded / (1024 * 1024)).toFixed(1)} / ${(total / (1024 * 1024)).toFixed(1)} MB`;
  return `${(downloaded / (1024 * 1024 * 1024)).toFixed(2)} / ${(total / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
