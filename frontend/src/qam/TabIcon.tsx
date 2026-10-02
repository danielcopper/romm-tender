/**
 * Tender's glyph in Steam's Quick Access tab strip.
 *
 * The geometry is generated — `tabIconArt.ts`, written by `scripts/logo` from
 * the mark's own drawing routines — so the strip glyph cannot drift away from
 * the mark. What is decided here is how those pieces are assembled: the arc and
 * the bars are each drawn twice under one `rotate(180)`, the bars as solid
 * capsules with nothing marking the button positions. The one number of its own
 * is the default edge length, {@link GLYPH_SIZE}.
 *
 * It carries one piece of state: a dot, drawn while the "is available" card on
 * Main would show and its release was not yet seen (`utils/updateDot.ts`). The
 * stores are read through `useSyncExternalStore`, which subscribes on mount and
 * lets go on unmount, so the glyph binds nothing that outlives the Quick Access
 * view it renders in.
 *
 * **Nothing about it moves at rest, and that is a measurement rather than a
 * taste**. It shipped with a turning ring and a folding body, and the fold alone
 * cost roughly 29% of one core for as long as the menu was open — measured on
 * the device over CDP. An animation added back here costs that again:
 * `docs/architecture/qam-panel.md`, "The glyph", holds the reading in full, and
 * the one motion left — the dot's fade when its release is seen.
 *
 * **Two things about this are UNMEASURED**, and neither is guessed at here.
 * Whether `size` below lands at the 28 px it is aiming for: its `1.633em` is
 * scaled off a mapping measured at `1.4em`, and neither that the mapping holds
 * at this value nor that the strip takes the length it is handed rather than
 * clamping it has been established. And whether the glyph takes the colour of
 * the selected tab: it asks for `currentColor` and nothing here establishes what
 * that inherits.
 */

import type { FC } from "react";
import { TAB_ICON_ARC, TAB_ICON_BARS, TAB_ICON_CENTRE, TAB_ICON_VIEW_BOX } from "./tabIconArt";
import { UPDATE_AVAILABLE_COLOR } from "../utils/updateAvailableView";
import { DOT_FADE_STYLE, useUpdateDot } from "../utils/updateDot";

/**
 * Where the glyph's halves are defined, for the `<use>` that draws each one a
 * second time under a `rotate(180)`.
 *
 * Constants rather than `useId()`: one entry exists per Quick Access menu, and a
 * generated id would carry React's own punctuation into a URL fragment for no
 * gain. Two glyphs in one document would both reflect the first one's halves —
 * which is the same picture, since nothing about the glyph varies between
 * instances.
 */
const RING_ID = "tender-tab-icon-ring";
const BODY_ID = "tender-tab-icon-body";

/**
 * 28 px, at the em-to-pixel mapping measured on the device: `1.4em` drew a 24 px
 * box, which is 17.14 px to the em. That is not the 16 px parent `font-size`
 * read beside it, so plain CSS em resolution cannot be the whole story and what
 * sits between the two was never established. The derivation needs only the
 * ratio — 28 / 17.14 = 1.633 — and holds whatever produces it. An em keeps the
 * glyph scaling with Steam's UI where a pixel length would pin it.
 */
const GLYPH_SIZE = "1.633em";

/**
 * The dot, in the 200-unit square: in the top-right corner, over the arc's end
 * and reaching past the square's edge, which the glyph's `overflow: visible`
 * lets show. A dot clear of the arc fits only up to a radius of about 23, which
 * read too small on the device's strip.
 */
const DOT = { cx: 180, cy: 20, r: 32 } as const;

/**
 * The fade, about the dot's own centre: an SVG element's transform is
 * otherwise taken about the view box's origin, and the dot would grow away
 * from where it sat.
 */
const DOT_FADE = { ...DOT_FADE_STYLE, transformBox: "fill-box", transformOrigin: "center" } as const;

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
  const dot = useUpdateDot();

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={TAB_ICON_VIEW_BOX}
      width={size}
      height={size}
      // Decorative: the strip entry carries the accessible name in its title,
      // so the glyph must not announce itself a second time.
      aria-hidden="true"
      style={{ display: "block", overflow: "visible" }}
      data-testid="tender-tab-icon"
    >
      <g>
        <Arc />
        <use href={`#${RING_ID}`} transform={turn} />
      </g>
      {/*
       * The bars declare no fill, so this group is the only thing between them
       * and SVG's initial `fill` — black, and a black glyph on the strip's dark
       * ground is one nobody can see. Moving this attribute off is not a
       * tidy-up, whatever else the group is carrying at the time.
       */}
      <g fill="currentColor">
        <g id={BODY_ID}>
          <path d={TAB_ICON_BARS.a} />
          <path d={TAB_ICON_BARS.b} />
        </g>
        <use href={`#${BODY_ID}`} transform={turn} />
      </g>
      {dot !== "none" && (
        <circle
          cx={DOT.cx}
          cy={DOT.cy}
          r={DOT.r}
          fill={UPDATE_AVAILABLE_COLOR}
          style={dot === "fading" ? DOT_FADE : undefined}
          data-testid="tender-update-dot"
        />
      )}
    </svg>
  );
};
