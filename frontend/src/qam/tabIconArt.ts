/**
 * The Quick Access tab glyph's geometry — GENERATED, do not edit.
 *
 * Written by `scripts/logo/build.py --tab-icon` from `scripts/logo/tabicon.py`,
 * which draws it with the mark's own routines. Editing this file by hand is the
 * one way the strip glyph and the mark can drift apart, which is the whole
 * reason it is generated; change `tabicon.STRIP_GEOMETRY` and re-run the build.
 *
 * The arc and the bars are point-symmetric about the centre of the {@link
 * TAB_ICON_VIEW_BOX} square, so each is emitted once and drawn twice under one
 * `rotate(180)`: {@link TAB_ICON_ARC} is the upper sync arc and {@link
 * TAB_ICON_BARS} two of the body's four bars.
 */

export const TAB_ICON_VIEW_BOX = "0 0 200 200";

/** The centre both halves are reflected through, as `rotate()` takes it. */
export const TAB_ICON_CENTRE = "100 100";

/** The upper sync arc: a stroked path, capped by a filled arrowhead. */
export const TAB_ICON_ARC = {
  d: "M 30.416 74.673 A 74.050 74.050 0 0 1 169.584 74.673",
  width: 20.15,
  head: "179.58,97.71 184.55,63.76 151.57,77.90",
  headRound: 5.59,
} as const;

/** The body: two of its four bars, meeting at one hub as a capsule. */
export const TAB_ICON_BARS = {
  a: "M 93.2 99.3 L 110.6 85.5 A 15.7 15.7 0 0 0 113.2 63.4 L 113.2 63.4 A 15.7 15.7 0 0 0 91.1 60.9 L 73.7 74.7 A 15.7 15.7 0 0 0 93.2 99.3 Z",
  b: "M 73.7 74.7 L 56.2 88.5 A 15.7 15.7 0 0 0 53.7 110.6 L 53.7 110.6 A 15.7 15.7 0 0 0 75.7 113.1 L 93.2 99.3 A 15.7 15.7 0 0 0 73.7 74.7 Z",
} as const;
