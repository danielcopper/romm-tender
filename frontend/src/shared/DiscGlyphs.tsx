/**
 * The faces of a multi-disc picker, and the options its menu lists — what both
 * surfaces' disc pickers draw over `utils/discSelection.ts`.
 */

import type { FC, ReactNode } from "react";
import { FaCompactDisc } from "react-icons/fa";
import type { DiscSelection } from "../api/backend";
import type { DiscOptionData } from "../utils/discSelection";

// Neutral grey for the m3u default; Steam accent blue when a specific disc is
// pinned — an instant "this isn't the default" read.
export const DISC_GREY = "#dcdedf";
export const DISC_ACCENT = "#59b6ff";

/**
 * Two CDs stacked top-left -> bottom-right: the front (opaque) disc at the
 * top-left, one behind it trailing down-right and faded — the m3u "all discs"
 * face. The back disc renders first so the front one is on top.
 */
export const DiscStack: FC<{ size: number; color: string }> = ({ size, color }) => {
  const step = Math.round(size * 0.3);
  return (
    <span style={{ position: "relative", display: "inline-block", width: size + step, height: size + step, color }}>
      <FaCompactDisc size={size} style={{ position: "absolute", left: step, top: step, opacity: 0.55 }} />
      <FaCompactDisc size={size} style={{ position: "absolute", left: 0, top: 0, opacity: 1 }} />
    </span>
  );
};

/** One CD + its number — the "Disc N" face. */
export const DiscWithNumber: FC<{ size: number; color: string; num: string }> = ({ size, color, num }) => (
  <span style={{ display: "inline-flex", alignItems: "center", gap: "4px", color }}>
    <FaCompactDisc size={size} />
    <span style={{ fontWeight: 600, fontSize: `${Math.round(size * 0.6)}px` }}>{num}</span>
  </span>
);

export interface DiscOptionItem {
  data: DiscOptionData;
  icon: ReactNode;
  text: string;
}

/**
 * Builds the list of selectable disc options: the m3u "all discs" entry (when m3u default
 * is present) followed by each disc in the set.
 */
export function buildDiscOptions(selection: DiscSelection): DiscOptionItem[] {
  if (!selection.discs || !selection.default) return [];

  const { discs, default: dflt } = selection;
  const isM3u = dflt.kind === "m3u";
  const options: DiscOptionItem[] = [];

  if (isM3u) {
    options.push({
      data: null,
      icon: <DiscStack size={16} color={DISC_GREY} />,
      text: dflt.label,
    });
  }

  for (const disc of discs) {
    options.push({
      data: disc.filename,
      icon: <FaCompactDisc size={16} />,
      text: disc.label,
    });
  }

  return options;
}
