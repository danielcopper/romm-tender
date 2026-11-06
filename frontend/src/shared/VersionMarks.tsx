/**
 * The marks a version row carries after the version's name in either version
 * picker — its badges and its availability hint — and the accent and the grey
 * both pickers draw in. Their words are `utils/versionPickerWording.ts`'s.
 */

import type { FC } from "react";
import { DISC_ACCENT, DISC_GREY } from "./DiscGlyphs";

// The disc picker's palette, so the game-detail pickers read as one system.
export const ACTIVE_ACCENT = DISC_ACCENT;
export const NEUTRAL_GREY = DISC_GREY;

const BADGE_COLORS: Record<"accent" | "muted" | "good", { bg: string; fg: string }> = {
  accent: { bg: "rgba(89, 182, 255, 0.18)", fg: ACTIVE_ACCENT },
  good: { bg: "rgba(91, 163, 43, 0.22)", fg: "#7ac74f" },
  muted: { bg: "rgba(255, 255, 255, 0.10)", fg: "rgba(255, 255, 255, 0.55)" },
};

/** The italic availability hint a picker draws beside a version. */
export const AvailabilityHint: FC<{ text: string }> = ({ text }) => (
  <span style={{ marginLeft: "8px", fontSize: "11px", fontStyle: "italic", color: NEUTRAL_GREY }}>{text}</span>
);

/** A small pill badge (Default / Downloaded / not synced) shown after a row's label. */
export const Badge: FC<{ text: string; tone: "accent" | "muted" | "good" }> = ({ text, tone }) => {
  const { bg, fg } = BADGE_COLORS[tone];
  return (
    <span
      style={{
        marginLeft: "8px",
        padding: "1px 7px",
        borderRadius: "10px",
        fontSize: "11px",
        fontWeight: 600,
        backgroundColor: bg,
        color: fg,
      }}
    >
      {text}
    </span>
  );
};
