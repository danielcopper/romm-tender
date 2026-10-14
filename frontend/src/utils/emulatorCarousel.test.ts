import { describe, expect, it } from "vitest";
import { ringLayout, ringOffsets, slideFrom } from "./emulatorCarousel";

describe("where the carousel puts each emulator", () => {
  it("puts the shown one in the middle and the others on either side in stepping order", () => {
    // Five entries, the third shown: L2 reaches the second, R2 the fourth.
    expect(ringOffsets(5, 2)).toEqual([-2, -1, 0, 1, 2]);
  });

  it("wraps around, so the shown one has neighbours on both sides wherever it stands", () => {
    // The first shown: the last two stand on its left, where L2 goes.
    expect(ringOffsets(5, 0)).toEqual([0, 1, 2, -2, -1]);
    expect(ringOffsets(5, 4)).toEqual([1, 2, -2, -1, 0]);
  });

  it("puts the extra one on the right where the others do not split evenly", () => {
    expect(ringOffsets(4, 0)).toEqual([0, 1, 2, -1]);
    expect(ringOffsets(2, 0)).toEqual([0, 1]);
    expect(ringOffsets(2, 1)).toEqual([1, 0]);
  });

  it("centres the shown entry and lines the others up beside it by their own widths", () => {
    // Widths 40, 20, 30; the second shown, a gap of 4.
    const { xs } = ringLayout([40, 20, 30], 4, 1);
    expect(xs[1]).toBe(-10);
    // Its right edge at 10, then the gap: the third starts at 14.
    expect(xs[2]).toBe(14);
    // Its left edge at -10, the gap, then the first's 40: the first starts at -54.
    expect(xs[0]).toBe(-54);
  });
});

describe("how a step slides the carousel", () => {
  const widths = [30, 30, 30, 30, 30];

  it("places a first layout, and one whose shown entry stayed, without a slide", () => {
    const layout = ringLayout(widths, 2, 0);
    expect(slideFrom(null, layout)).toBeNull();
    expect(slideFrom(layout, ringLayout(widths, 2, 0))).toBeNull();
  });

  it("slides every entry that keeps its side from where it stood", () => {
    const before = ringLayout(widths, 2, 2);
    const after = ringLayout(widths, 2, 3);
    const from = slideFrom(before, after) ?? [];
    expect([1, 2, 3, 4].map((i) => from[i])).toEqual([1, 2, 3, 4].map((i) => before.xs[i]));
  });

  it("brings the entry that crosses to the other end in from beyond that end, not across the carousel", () => {
    const before = ringLayout(widths, 2, 2);
    const after = ringLayout(widths, 2, 3);
    const from = slideFrom(before, after) ?? [];
    // Stepping right moves everything one place left; the first entry leaves the
    // left end and arrives at the right end. It starts one place beyond that end
    // and moves left by as far as everything else does.
    const travel = (after.xs[3] ?? 0) - (before.xs[3] ?? 0);
    expect(travel).toBeLessThan(0);
    expect(from[0]).toBe((after.xs[0] ?? 0) - travel);
    expect(from[0]).toBeGreaterThan(Math.max(...after.xs));
  });

  it("lays out afresh, without a slide, when an emulator comes or goes", () => {
    expect(slideFrom(ringLayout(widths, 2, 0), ringLayout([30, 30, 30], 2, 1))).toBeNull();
  });
});
