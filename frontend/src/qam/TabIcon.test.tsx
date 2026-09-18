/**
 * What the glyph asks the renderer for in each of its four states.
 *
 * The motion is SMIL elements rendered conditionally, so "which animation is
 * running" is a DOM question rather than a style question — which is the whole
 * reason it is testable here at all. happy-dom performs no layout and runs no
 * animation, so what these cases establish is the SELECTION: a state either puts
 * the element in the tree or leaves the path's own resting `d` standing alone.
 * Whether Steam's tab strip then runs it is a device question, named in this
 * cut's device list.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import { TabIcon } from "./TabIcon";
import { TAB_ICON_FOLD, TAB_ICON_REST } from "./tabIconArt";
import { setSyncProgress, resetSyncProgressStoreForTests } from "../utils/syncProgress";
import type { SyncProgress } from "../types";

const running = (): SyncProgress => ({ running: true, stage: "applying", runId: "r1" });

/** The glyph's own root, so a query cannot pick up something else's SVG. */
const glyph = (container: HTMLElement) => {
  const root = container.querySelector('[data-testid="tender-tab-icon"]');
  if (!root) throw new Error("the glyph did not render");
  return root;
};

const ringTurns = (container: HTMLElement) => glyph(container).querySelectorAll("animateTransform").length;
const crossFolds = (container: HTMLElement) => glyph(container).querySelectorAll("animate").length;

describe("TabIcon", () => {
  beforeEach(() => {
    resetSyncProgressStoreForTests();
  });

  it("rests as the cross with the ring level, asking for no animation at all", () => {
    const { container } = render(<TabIcon syncing={false} active={false} />);

    expect(ringTurns(container)).toBe(0);
    expect(crossFolds(container)).toBe(0);
    // Both bars stand at the resting pose, which is what an engine that animates
    // nothing draws — there is no second description of it to drift.
    const bars = [...glyph(container).querySelectorAll("path")].map((path) => path.getAttribute("d"));
    expect(bars).toContain(TAB_ICON_REST.a);
    expect(bars).toContain(TAB_ICON_REST.b);
  });

  it("turns the ring while a sync runs, and leaves the cross standing", () => {
    const { container } = render(<TabIcon syncing active={false} />);

    expect(ringTurns(container)).toBe(1);
    expect(crossFolds(container)).toBe(0);
  });

  it("folds both bars while the entry is active, and leaves the ring level", () => {
    const { container } = render(<TabIcon syncing={false} active />);

    expect(ringTurns(container)).toBe(0);
    expect(crossFolds(container)).toBe(2);
  });

  it("runs both when both", () => {
    const { container } = render(<TabIcon syncing active />);

    expect(ringTurns(container)).toBe(1);
    expect(crossFolds(container)).toBe(2);
  });

  it("takes the ring's turn from the sync store when the caller states nothing", () => {
    const { container } = render(<TabIcon active={false} />);
    expect(ringTurns(container)).toBe(0);

    act(() => setSyncProgress(running()));

    expect(ringTurns(container)).toBe(1);
  });

  it("takes the fold from the menu being open when the caller states nothing", () => {
    // The suite's `@decky/ui` stub answers `useQuickAccessVisible` with true, so
    // an unstated `active` is the menu-open case — the default every other case
    // here overrides, and the one the strip actually renders with.
    const { container } = render(<TabIcon syncing={false} />);

    expect(crossFolds(container)).toBe(2);
    expect(ringTurns(container)).toBe(0);
  });

  it("folds while the pointer is on the glyph, and stops when it leaves", () => {
    const { container } = render(<TabIcon syncing={false} active={false} />);

    fireEvent.pointerEnter(glyph(container));
    expect(crossFolds(container)).toBe(2);

    fireEvent.pointerLeave(glyph(container));
    expect(crossFolds(container)).toBe(0);
  });

  it("drives the fold from the pose list, first stop to last", () => {
    const { container } = render(<TabIcon syncing={false} active />);

    const fold = glyph(container).querySelector("animate");
    // The value and time lists have to be the same length or the browser drops
    // the animation, and the loop has to close on the pose it opened with.
    const values = fold?.getAttribute("values")?.split(";") ?? [];
    const times = fold?.getAttribute("keyTimes")?.split(";") ?? [];
    expect(values).toEqual(TAB_ICON_FOLD.map((stop) => stop.a));
    expect(times).toEqual(TAB_ICON_FOLD.map((stop) => stop.at.toFixed(4)));
    expect(values.length).toBeGreaterThan(2);
    expect(values[0]).toBe(TAB_ICON_REST.a);
    expect(values[values.length - 1]).toBe(TAB_ICON_REST.a);
    expect(times[0]).toBe("0.0000");
    expect(times[times.length - 1]).toBe("1.0000");
  });
});
