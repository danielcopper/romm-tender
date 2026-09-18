/**
 * The Quick Access tab glyph's geometry — GENERATED, do not edit.
 *
 * Written by `scripts/logo/build.py --tab-icon` from `scripts/logo/tabicon.py`,
 * which draws it with the mark's own routines. Editing this file by hand is the
 * one way the strip glyph and the mark can drift apart, which is the whole
 * reason it is generated; change `tabicon.STRIP_GEOMETRY` and re-run the build.
 *
 * Everything here is point-symmetric about the centre of the {@link
 * TAB_ICON_VIEW_BOX} square, so each half is emitted once and drawn twice under
 * one `rotate(180)`: {@link TAB_ICON_ARC} is the upper sync arc and {@link
 * TAB_ICON_FOLD} carries two of the body's four bars.
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

/** One pose of the body: two of its four bars, at a fraction of the loop. */
export interface TabIconFoldStop {
  readonly at: number;
  readonly a: string;
  readonly b: string;
}

/**
 * The pose the glyph sits in: the D-pad cross, ring level.
 *
 * This is what a renderer puts in each path's own `d`, so an engine that
 * animates nothing draws exactly this. It is the fold's first stop and its
 * last, by reference rather than by a second copy of the data.
 */
export const TAB_ICON_REST: TabIconFoldStop = {
  at: 0.0,
  a: "M 114.1 100.0 L 114.1 69.7 A 11.3 11.3 0 0 0 102.8 58.4 L 97.2 58.4 A 11.3 11.3 0 0 0 85.9 69.7 L 85.9 100.0 A 14.1 14.1 0 0 0 114.1 100.0 Z",
  b: "M 100.0 85.9 L 69.7 85.9 A 11.3 11.3 0 0 0 58.4 97.2 L 58.4 102.8 A 11.3 11.3 0 0 0 69.7 114.1 L 100.0 114.1 A 14.1 14.1 0 0 0 100.0 85.9 Z",
};

/**
 * The fold, first stop first, opening and closing on {@link TAB_ICON_REST}.
 *
 * Every stop's path data carries the same command sequence, so one
 * interpolates into the next. Stops where the pose does not move are not
 * emitted — three spans, because the rest hold is split by the loop boundary.
 */
export const TAB_ICON_FOLD: readonly TabIconFoldStop[] = [
  TAB_ICON_REST,
  {
    at: 0.1667,
    a: "M 113.8 99.9 L 114.3 70.0 A 11.4 11.4 0 0 0 103.0 58.4 L 97.5 58.3 A 11.4 11.4 0 0 0 85.9 69.5 L 85.5 99.4 A 14.2 14.2 0 0 0 113.8 99.9 Z",
    b: "M 99.4 85.5 L 69.4 85.9 A 11.4 11.4 0 0 0 58.1 97.5 L 58.2 103.0 A 11.4 11.4 0 0 0 69.8 114.3 L 99.9 113.8 A 14.2 14.2 0 0 0 99.4 85.5 Z",
  },
  {
    at: 0.1944,
    a: "M 112.7 99.5 L 114.7 71.1 A 11.8 11.8 0 0 0 103.8 58.5 L 98.8 58.1 A 11.8 11.8 0 0 0 86.2 69.0 L 84.1 97.4 A 14.3 14.3 0 0 0 112.7 99.5 Z",
    b: "M 97.4 84.1 L 68.2 86.1 A 11.8 11.8 0 0 0 57.2 98.7 L 57.6 103.7 A 11.8 11.8 0 0 0 70.1 114.7 L 99.3 112.7 A 14.3 14.3 0 0 0 97.4 84.1 Z",
  },
  {
    at: 0.2222,
    a: "M 110.3 99.1 L 115.3 73.5 A 12.5 12.5 0 0 0 105.4 58.8 L 101.3 58.0 A 12.5 12.5 0 0 0 86.6 67.9 L 81.7 93.5 A 14.6 14.6 0 0 0 110.3 99.1 Z",
    b: "M 93.6 81.9 L 66.2 86.4 A 12.5 12.5 0 0 0 55.9 100.8 L 56.6 104.9 A 12.5 12.5 0 0 0 70.9 115.2 L 98.4 110.7 A 14.6 14.6 0 0 0 93.6 81.9 Z",
  },
  {
    at: 0.25,
    a: "M 106.6 99.1 L 115.3 77.0 A 13.4 13.4 0 0 0 107.7 59.6 L 104.9 58.5 A 13.4 13.4 0 0 0 87.5 66.1 L 78.8 88.1 A 14.9 14.9 0 0 0 106.6 99.1 Z",
    b: "M 88.4 79.3 L 63.5 86.9 A 13.4 13.4 0 0 0 54.6 103.6 L 55.4 106.4 A 13.4 13.4 0 0 0 72.2 115.4 L 97.0 107.9 A 14.9 14.9 0 0 0 88.4 79.3 Z",
  },
  {
    at: 0.2778,
    a: "M 101.7 99.3 L 114.1 80.9 A 14.3 14.3 0 0 0 110.2 60.9 L 108.8 60.0 A 14.3 14.3 0 0 0 88.8 63.9 L 76.4 82.3 A 15.2 15.2 0 0 0 101.7 99.3 Z",
    b: "M 82.6 77.1 L 60.6 87.4 A 14.3 14.3 0 0 0 53.8 106.5 L 54.5 108.1 A 14.3 14.3 0 0 0 73.6 115.0 L 95.6 104.6 A 15.2 15.2 0 0 0 82.6 77.1 Z",
  },
  {
    at: 0.3056,
    a: "M 97.0 99.5 L 112.3 83.7 A 15.1 15.1 0 0 0 112.1 62.3 L 111.5 61.8 A 15.1 15.1 0 0 0 90.1 62.1 L 74.8 77.8 A 15.5 15.5 0 0 0 97.0 99.5 Z",
    b: "M 77.6 75.6 L 58.1 88.0 A 15.1 15.1 0 0 0 53.6 108.9 L 54.0 109.5 A 15.1 15.1 0 0 0 74.8 114.1 L 94.3 101.7 A 15.5 15.5 0 0 0 77.6 75.6 Z",
  },
  {
    at: 0.3333,
    a: "M 94.1 99.4 L 111.0 85.1 A 15.6 15.6 0 0 0 113.0 63.2 L 112.8 63.1 A 15.6 15.6 0 0 0 90.9 61.1 L 73.9 75.4 A 15.6 15.6 0 0 0 94.1 99.4 Z",
    b: "M 74.6 74.9 L 56.7 88.4 A 15.6 15.6 0 0 0 53.6 110.2 L 53.7 110.3 A 15.6 15.6 0 0 0 75.5 113.4 L 93.4 99.9 A 15.6 15.6 0 0 0 74.6 74.9 Z",
  },
  {
    at: 0.3611,
    a: "M 93.2 99.3 L 110.6 85.5 A 15.7 15.7 0 0 0 113.2 63.4 L 113.2 63.4 A 15.7 15.7 0 0 0 91.1 60.9 L 73.7 74.7 A 15.7 15.7 0 0 0 93.2 99.3 Z",
    b: "M 73.7 74.7 L 56.2 88.5 A 15.7 15.7 0 0 0 53.7 110.6 L 53.7 110.6 A 15.7 15.7 0 0 0 75.7 113.1 L 93.2 99.3 A 15.7 15.7 0 0 0 73.7 74.7 Z",
  },
  {
    at: 0.6667,
    a: "M 94.1 99.4 L 111.0 85.1 A 15.6 15.6 0 0 0 113.0 63.2 L 112.8 63.1 A 15.6 15.6 0 0 0 90.9 61.1 L 73.9 75.4 A 15.6 15.6 0 0 0 94.1 99.4 Z",
    b: "M 74.6 74.9 L 56.7 88.4 A 15.6 15.6 0 0 0 53.6 110.2 L 53.7 110.3 A 15.6 15.6 0 0 0 75.5 113.4 L 93.4 99.9 A 15.6 15.6 0 0 0 74.6 74.9 Z",
  },
  {
    at: 0.6944,
    a: "M 97.0 99.5 L 112.3 83.7 A 15.1 15.1 0 0 0 112.1 62.3 L 111.5 61.8 A 15.1 15.1 0 0 0 90.1 62.1 L 74.8 77.8 A 15.5 15.5 0 0 0 97.0 99.5 Z",
    b: "M 77.6 75.6 L 58.1 88.0 A 15.1 15.1 0 0 0 53.6 108.9 L 54.0 109.5 A 15.1 15.1 0 0 0 74.8 114.1 L 94.3 101.7 A 15.5 15.5 0 0 0 77.6 75.6 Z",
  },
  {
    at: 0.7222,
    a: "M 101.7 99.3 L 114.1 80.9 A 14.3 14.3 0 0 0 110.2 60.9 L 108.8 60.0 A 14.3 14.3 0 0 0 88.8 63.9 L 76.4 82.3 A 15.2 15.2 0 0 0 101.7 99.3 Z",
    b: "M 82.6 77.1 L 60.6 87.4 A 14.3 14.3 0 0 0 53.8 106.5 L 54.5 108.1 A 14.3 14.3 0 0 0 73.6 115.0 L 95.6 104.6 A 15.2 15.2 0 0 0 82.6 77.1 Z",
  },
  {
    at: 0.75,
    a: "M 106.6 99.1 L 115.3 77.0 A 13.4 13.4 0 0 0 107.7 59.6 L 104.9 58.5 A 13.4 13.4 0 0 0 87.5 66.1 L 78.8 88.1 A 14.9 14.9 0 0 0 106.6 99.1 Z",
    b: "M 88.4 79.3 L 63.5 86.9 A 13.4 13.4 0 0 0 54.6 103.6 L 55.4 106.4 A 13.4 13.4 0 0 0 72.2 115.4 L 97.0 107.9 A 14.9 14.9 0 0 0 88.4 79.3 Z",
  },
  {
    at: 0.7778,
    a: "M 110.3 99.1 L 115.3 73.5 A 12.5 12.5 0 0 0 105.4 58.8 L 101.3 58.0 A 12.5 12.5 0 0 0 86.6 67.9 L 81.7 93.5 A 14.6 14.6 0 0 0 110.3 99.1 Z",
    b: "M 93.6 81.9 L 66.2 86.4 A 12.5 12.5 0 0 0 55.9 100.8 L 56.6 104.9 A 12.5 12.5 0 0 0 70.9 115.2 L 98.4 110.7 A 14.6 14.6 0 0 0 93.6 81.9 Z",
  },
  {
    at: 0.8056,
    a: "M 112.7 99.5 L 114.7 71.1 A 11.8 11.8 0 0 0 103.8 58.5 L 98.8 58.1 A 11.8 11.8 0 0 0 86.2 69.0 L 84.1 97.4 A 14.3 14.3 0 0 0 112.7 99.5 Z",
    b: "M 97.4 84.1 L 68.2 86.1 A 11.8 11.8 0 0 0 57.2 98.7 L 57.6 103.7 A 11.8 11.8 0 0 0 70.1 114.7 L 99.3 112.7 A 14.3 14.3 0 0 0 97.4 84.1 Z",
  },
  {
    at: 0.8333,
    a: "M 113.8 99.9 L 114.3 70.0 A 11.4 11.4 0 0 0 103.0 58.4 L 97.5 58.3 A 11.4 11.4 0 0 0 85.9 69.5 L 85.5 99.4 A 14.2 14.2 0 0 0 113.8 99.9 Z",
    b: "M 99.4 85.5 L 69.4 85.9 A 11.4 11.4 0 0 0 58.1 97.5 L 58.2 103.0 A 11.4 11.4 0 0 0 69.8 114.3 L 99.9 113.8 A 14.2 14.2 0 0 0 99.4 85.5 Z",
  },
  {
    at: 0.8611,
    a: "M 114.1 100.0 L 114.1 69.7 A 11.3 11.3 0 0 0 102.8 58.4 L 97.2 58.4 A 11.3 11.3 0 0 0 85.9 69.7 L 85.9 100.0 A 14.1 14.1 0 0 0 114.1 100.0 Z",
    b: "M 100.0 85.9 L 69.7 85.9 A 11.3 11.3 0 0 0 58.4 97.2 L 58.4 102.8 A 11.3 11.3 0 0 0 69.7 114.1 L 100.0 114.1 A 14.1 14.1 0 0 0 100.0 85.9 Z",
  },
  { at: 1.0, a: TAB_ICON_REST.a, b: TAB_ICON_REST.b },
];

/** Seconds one loop takes — the mark's own 72 frames at 25 fps. */
export const TAB_ICON_LOOP_SECONDS = 2.88;
