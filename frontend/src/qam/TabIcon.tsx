/**
 * Tender's glyph in Steam's Quick Access tab strip.
 *
 * The geometry is generated — `tabIconArt.ts`, written by `scripts/logo` from
 * the mark's own drawing routines — so the strip glyph cannot drift away from
 * the mark. Nothing is decided here but how those pieces are assembled: the arc
 * and the bars are drawn twice under one `rotate(180)`, and the four button
 * positions are punched out of the finished body by a mask.
 *
 * **It does not animate, and that is a measurement rather than a taste.** It
 * shipped with a turning ring and a folding body; read over CDP on the
 * QuickAccess target in 6-second windows, the fold alone cost 1.726 s of task
 * time against 0.0077 s idle and ran layout and style recalc 720 times each —
 * twice a frame at 60 Hz, because animating a path's `d` forces layout every
 * frame. Roughly 29% of one core, for as long as the menu is open, for a glyph
 * that was rendering at 24 px. An animation added back here costs that again:
 * `docs/architecture/qam-panel.md` holds the reading in full.
 *
 * **Two things about this are UNMEASURED**, both on the device list for this cut
 * and neither guessed at here. What size the strip draws it at — `size` below
 * asks for 28 px and nothing establishes what the strip does with that. And
 * whether the glyph takes the colour of the selected tab: it asks for
 * `currentColor` and nothing here establishes what that inherits.
 */

import type { FC } from "react";
import {
  TAB_ICON_ARC,
  TAB_ICON_BARS,
  TAB_ICON_CENTRE,
  TAB_ICON_HOLE_R,
  TAB_ICON_HOLES,
  TAB_ICON_VIEW_BOX,
} from "./tabIconArt";

/**
 * Where the glyph's halves are defined, for the `<use>` that draws each one a
 * second time under a `rotate(180)`, and where the holes are cut.
 *
 * Constants rather than `useId()`: one entry exists per Quick Access menu, and a
 * generated id would carry React's own punctuation into a URL fragment for no
 * gain. Two glyphs in one document would both reflect the first one's halves and
 * both cut the first one's holes — which is the same picture, since nothing
 * about the glyph varies between instances.
 */
const RING_ID = "tender-tab-icon-ring";
const BODY_ID = "tender-tab-icon-body";
const HOLES_ID = "tender-tab-icon-holes";

/**
 * 28 px, at the em-to-pixel mapping measured on the device rather than derived
 * from the parent's `font-size`: `1.4em` under a 16 px parent drew a 24 px box,
 * not the 22.4 px that font size states. An em keeps the glyph scaling with
 * Steam's UI where a pixel length would pin it.
 */
const GLYPH_SIZE = "1.633em";

export interface TabIconProps {
  /** Edge length. See {@link GLYPH_SIZE} for what the default is and why. */
  size?: string;
}

/**
 * One sync arc with its arrowhead. Drawn twice — the pair is point-symmetric
 * about the centre, which is why the generator emits one of them.
 */
const Arc: FC = () => (
  <g id={RING_ID}>
    <path d={TAB_ICON_ARC.d} fill="none" stroke="currentColor" strokeWidth={TAB_ICON_ARC.width} strokeLinecap="round" />
    <polygon
      points={TAB_ICON_ARC.head}
      fill="currentColor"
      stroke="currentColor"
      strokeWidth={TAB_ICON_ARC.headRound}
      strokeLinejoin="round"
    />
  </g>
);

export const TabIcon: FC<TabIconProps> = ({ size = GLYPH_SIZE }) => {
  const turn = `rotate(180 ${TAB_ICON_CENTRE})`;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={TAB_ICON_VIEW_BOX}
      width={size}
      height={size}
      // Decorative: the strip entry carries the accessible name in its title,
      // so the glyph must not announce itself a second time.
      aria-hidden="true"
      style={{ display: "block" }}
      data-testid="tender-tab-icon"
    >
      <g>
        <Arc />
        <use href={`#${RING_ID}`} transform={turn} />
      </g>
      {/*
       * Luminance masking: white keeps the bar, black takes it away. The mask
       * covers the finished body rather than one half of it, which is why it
       * names all four positions where the bars name two — a `<use>` clones the
       * bars, not what was cut out of them. The rect's percentages resolve
       * against the view box, so it covers the square whatever `size` asks for.
       */}
      <mask id={HOLES_ID}>
        <rect x="0" y="0" width="100%" height="100%" fill="#fff" />
        {TAB_ICON_HOLES.map((hole) => (
          <circle key={`${hole.cx},${hole.cy}`} cx={hole.cx} cy={hole.cy} r={TAB_ICON_HOLE_R} fill="#000" />
        ))}
      </mask>
      <g mask={`url(#${HOLES_ID})`} fill="currentColor">
        <g id={BODY_ID}>
          <path d={TAB_ICON_BARS.a} />
          <path d={TAB_ICON_BARS.b} />
        </g>
        <use href={`#${BODY_ID}`} transform={turn} />
      </g>
    </svg>
  );
};
