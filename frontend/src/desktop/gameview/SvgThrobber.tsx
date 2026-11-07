/**
 * The ring `romm-throbber` draws, turned by SMIL rather than by that class's
 * keyframes, for the buttons in Steam's document on the desktop client. Why:
 * `docs/architecture/qam-panel.md`, "Waiting".
 */

import type { CSSProperties, FC } from "react";

export const SvgThrobber: FC<{ style?: CSSProperties }> = ({ style }) => (
  <svg
    data-testid="spinner"
    width="18"
    height="18"
    viewBox="0 0 18 18"
    fill="none"
    aria-hidden="true"
    style={{ flexShrink: 0, ...style }}
  >
    <circle cx="9" cy="9" r="8" stroke="rgba(255,255,255,0.3)" strokeWidth="2" />
    <path d="M9 1a8 8 0 0 1 8 8" stroke="#fff" strokeWidth="2">
      <animateTransform
        attributeName="transform"
        type="rotate"
        from="0 9 9"
        to="360 9 9"
        dur="0.8s"
        repeatCount="indefinite"
      />
    </path>
  </svg>
);
