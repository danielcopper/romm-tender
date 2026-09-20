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
 * **It does not animate, and that is a measurement rather than a taste.** It
 * shipped with a turning ring and a folding body, and the fold alone cost
 * roughly 29% of one core for as long as the menu was open — measured on the
 * device over CDP. An animation added back here costs that again:
 * `docs/architecture/qam-panel.md` holds the reading in full.
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
    </svg>
  );
};
