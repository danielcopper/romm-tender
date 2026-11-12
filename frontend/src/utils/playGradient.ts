/**
 * Steam's own PLAY gradient in the desktop client, which the desktop Play
 * button draws across its whole width and a desktop download's fill ends on.
 */

export type RGB = [number, number, number];

/** A colour as CSS's `rgb()`. */
export function rgb([r, g, b]: RGB): string {
  return `rgb(${r}, ${g}, ${b})`;
}

// The gradient's stops, read in DevTools on CachyOS running Steam client build
// 1792618285; Steam's and Big Picture's Play show a stretch of it instead.
export const PLAY_LEFT_STOP: RGB = [112, 214, 29]; // #70d61d
export const PLAY_RIGHT_STOP: RGB = [1, 167, 91]; // #01a75b
export const PLAY_RIGHT_STOP_AT = "60%";

export const PLAY_GRADIENT = `linear-gradient(to right, ${rgb(PLAY_LEFT_STOP)} 0%, ${rgb(PLAY_RIGHT_STOP)} ${PLAY_RIGHT_STOP_AT})`;
