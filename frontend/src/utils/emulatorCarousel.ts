/**
 * Where the emulator selector's carousel puts each entry, and how it gets there
 * from where it was.
 *
 * The entries stand in stepping order around a ring: the shown one in the
 * middle, the ones L2 reaches to its left and the ones R2 reaches to its right,
 * so the ring has no start and no end. Every entry is one element in a fixed
 * order and is only ever moved, never re-rendered elsewhere, which is what lets
 * a step slide the carousel rather than redraw it.
 */

/** One arrangement of the ring: which entry is shown, each entry's place relative
 *  to it, and where each entry's left edge sits in px from the carousel's centre. */
export interface RingLayout {
  shown: number;
  offsets: number[];
  xs: number[];
}

/**
 * Each entry's place relative to the shown one, in stepping order: `0` for the
 * shown one, `-1` for the one L2 reaches, `1` for the one R2 reaches. Where the
 * others do not split evenly, the extra one stands on the right.
 */
export function ringOffsets(count: number, shown: number): number[] {
  const before = Math.floor((count - 1) / 2);
  return Array.from({ length: count }, (_, i) => ((((i - shown + before) % count) + count) % count) - before);
}

/** The ring with *shown* in the middle, laid out over the entries' own *widths*. */
export function ringLayout(widths: readonly number[], gap: number, shown: number): RingLayout {
  const offsets = ringOffsets(widths.length, shown);
  const byOffset = new Map(offsets.map((offset, i) => [offset, i]));
  const xs = widths.map(() => 0);
  const half = (widths[shown] ?? 0) / 2;
  xs[shown] = -half;
  let right = half + gap;
  for (let offset = 1, i = byOffset.get(offset); i !== undefined; offset += 1, i = byOffset.get(offset)) {
    xs[i] = right;
    right += (widths[i] ?? 0) + gap;
  }
  let left = -half - gap;
  for (let offset = -1, i = byOffset.get(offset); i !== undefined; offset -= 1, i = byOffset.get(offset)) {
    left -= widths[i] ?? 0;
    xs[i] = left;
    left -= gap;
  }
  return { shown, offsets, xs };
}

/**
 * Where each entry starts its slide into *next*, or `null` where nothing slides:
 * a first layout, a ring that gained or lost an entry, or one whose shown entry
 * stayed — those are placed where they belong at once.
 *
 * An entry that keeps its side of the ring slides from where it stood. One that
 * crosses from one end to the other would otherwise fly across the whole
 * carousel against the direction everything else moves; it starts instead
 * beyond the end it arrives at, by as far as the newly shown entry travels, and
 * slides in with the rest from under the faded edge.
 */
export function slideFrom(previous: RingLayout | null, next: RingLayout): number[] | null {
  if (previous?.xs.length !== next.xs.length || previous.shown === next.shown) return null;
  const shown = next.shown;
  const shift = (next.offsets[shown] ?? 0) - (previous.offsets[shown] ?? 0);
  const travel = (next.xs[shown] ?? 0) - (previous.xs[shown] ?? 0);
  return next.xs.map((x, i) =>
    (next.offsets[i] ?? 0) - (previous.offsets[i] ?? 0) === shift ? (previous.xs[i] ?? x) : x - travel,
  );
}
