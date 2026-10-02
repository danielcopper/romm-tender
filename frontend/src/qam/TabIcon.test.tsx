/**
 * What the glyph hands the renderer: the generated artwork, the update dot
 * while the "is available" card would show and its release was not seen, no
 * motion at rest, and one fade when the release is seen.
 *
 * happy-dom performs no layout and runs no animation, so what these cases
 * establish is what is in the tree — which is all the motion cases need,
 * because an animation that is not authored cannot run. Whether the strip draws
 * the glyph at the size and in the colour it asks for, and how the fade looks,
 * are device questions, and nothing in this suite reaches them.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { TabIcon } from "./TabIcon";
import { TAB_ICON_ARC, TAB_ICON_BARS } from "./tabIconArt";
import { UPDATE_AVAILABLE_COLOR } from "../utils/updateAvailableView";
import { DOT_FADE_MS } from "../utils/updateDot";
import {
  resetUpdateNoticeStoreForTests,
  setUpdateNoticeState,
  type UpdateNoticeState,
} from "../utils/updateNoticeStore";
import {
  resetUpdateOutcomeStoreForTests,
  setUpdateOutcomeState,
  type UpdateOutcomeState,
} from "../utils/updateOutcomeStore";
import { resetStoppedUpdateStoreForTests, takePushedStoppedAttempt } from "../utils/stoppedUpdateStore";

const AVAILABLE: UpdateNoticeState = {
  available: true,
  newer: true,
  latestVersion: "1.1.0",
  currentVersion: "1.0.0",
  enabled: true,
  installedProgram: true,
  toastOwed: false,
  seen: false,
};

/** Move the fake clock on by *ms*, inside act so what the timers set is rendered. */
const pass = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });

const dotOf = (root: Element) => root.querySelector('[data-testid="tender-update-dot"]');

/** The glyph's own root, so a query cannot pick up something else's SVG. */
const glyph = (container: HTMLElement) => {
  const root = container.querySelector('[data-testid="tender-tab-icon"]');
  if (!root) throw new Error("the glyph did not render");
  return root;
};

describe("TabIcon", () => {
  beforeEach(() => {
    resetUpdateNoticeStoreForTests();
    resetUpdateOutcomeStoreForTests();
    resetStoppedUpdateStoreForTests();
  });

  it("draws the generated artwork: the arc and both bars", () => {
    const { container } = render(<TabIcon />);
    const root = glyph(container);

    const drawn = [...root.querySelectorAll("path")].map((path) => path.getAttribute("d"));
    expect(drawn).toContain(TAB_ICON_ARC.d);
    expect(drawn).toContain(TAB_ICON_BARS.a);
    expect(drawn).toContain(TAB_ICON_BARS.b);
  });

  it("asks for the strip's own colour where the bars are filled", () => {
    // The bars declare no fill, so an ancestor decides what they are painted
    // in — and one declaring none leaves them at SVG's initial `fill`, black on
    // the strip's dark ground. happy-dom resolves no colour and computes no
    // style, so the attribute is the whole of what can be held from here.
    const root = glyph(render(<TabIcon />).container);

    const bar = root.querySelector(`path[d="${TAB_ICON_BARS.a}"]`);
    if (!bar) throw new Error("the glyph draws no bars");
    expect(bar.closest("[fill]")?.getAttribute("fill")).toBe("currentColor");
  });

  it("authors none of SMIL's animation elements", () => {
    // This is a performance decision, not a preference: the fold this replaced
    // ran layout twice a frame for as long as the menu was open. A later change
    // that brings SMIL back fails here rather than on someone's battery. The
    // query names every SMIL animation element; motion driven from CSS is the
    // next case's, and a rAF loop is authored nowhere in this tree and passes
    // both untouched. The dot is drawn, so it is held to the same.
    setUpdateNoticeState(AVAILABLE);
    const root = glyph(render(<TabIcon />).container);
    expect(dotOf(root)).not.toBeNull();

    expect(root.querySelectorAll("animate, animateTransform, animateMotion, animateColor, set, discard")).toHaveLength(
      0,
    );
  });

  it("authors no CSS motion at rest: no stylesheet, no class one could reach, no animation or transition inline", () => {
    setUpdateNoticeState(AVAILABLE);
    const root = glyph(render(<TabIcon />).container);
    expect(dotOf(root)).not.toBeNull();

    const elements = [root, ...root.querySelectorAll("*")];
    expect(root.querySelectorAll("style")).toHaveLength(0);
    expect(elements.filter((el) => el.hasAttribute("class"))).toEqual([]);
    expect(elements.filter((el) => /animation|transition/i.test(el.getAttribute("style") ?? ""))).toEqual([]);
  });

  describe("the update dot", () => {
    it("is not drawn before any store has answered, as on the start-up failure page", () => {
      expect(dotOf(glyph(render(<TabIcon />).container))).toBeNull();
    });

    it("is drawn inside the glyph, in the card's colour, while the card would show", () => {
      setUpdateNoticeState(AVAILABLE);

      const dot = dotOf(glyph(render(<TabIcon />).container));

      expect(dot?.tagName.toLowerCase()).toBe("circle");
      expect(dot?.getAttribute("fill")).toBe(UPDATE_AVAILABLE_COLOR);
    });

    it("is drawn with the check switched off too, as the card is", () => {
      setUpdateNoticeState({ ...AVAILABLE, enabled: false });

      expect(dotOf(glyph(render(<TabIcon />).container))).not.toBeNull();
    });

    it("comes and goes with the card as the stores change", () => {
      const { container } = render(<TabIcon />);
      expect(dotOf(glyph(container))).toBeNull();

      act(() => setUpdateNoticeState(AVAILABLE));
      expect(dotOf(glyph(container))).not.toBeNull();

      act(() => setUpdateNoticeState({ ...AVAILABLE, available: false }));
      expect(dotOf(glyph(container))).toBeNull();
    });

    it("goes while a failure record for the same release stands", () => {
      setUpdateNoticeState(AVAILABLE);
      const { container } = render(<TabIcon />);

      act(() =>
        setUpdateOutcomeState({
          announcement: null,
          failure: {
            attemptedVersion: "1.1.0",
            restoredVersion: "1.0.0",
            rolledBackAt: "2026-09-29T10:00:00Z",
            kind: "rollback",
          },
          failureDismissed: true,
        }),
      );

      expect(dotOf(glyph(container))).toBeNull();
    });

    it("goes while a stopped attempt at the same release stands", () => {
      setUpdateNoticeState(AVAILABLE);
      const { container } = render(<TabIcon />);

      act(() =>
        takePushedStoppedAttempt({
          attempted_version: "1.1.0",
          from_version: "1.0.0",
          started_at: "2026-09-29T10:00:00Z",
          toast_owed: false,
        }),
      );

      expect(dotOf(glyph(container))).toBeNull();
    });

    it("is not drawn once its release was seen, and the card stays", () => {
      setUpdateNoticeState({ ...AVAILABLE, seen: true });

      expect(dotOf(glyph(render(<TabIcon />).container))).toBeNull();
    });

    it("draws no dot, and does not throw, over a store state the answer cannot be worked out from", () => {
      setUpdateNoticeState(AVAILABLE);
      setUpdateOutcomeState(null as unknown as UpdateOutcomeState);

      const root = glyph(render(<TabIcon />).container);

      expect(dotOf(root)).toBeNull();
    });
  });

  describe("the fade", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    const motionOf = (el: Element | null) => el?.getAttribute("style") ?? "";

    it("plays once when the release is seen: the dot grows and fades out about its own centre, then is gone", () => {
      setUpdateNoticeState(AVAILABLE);
      const { container } = render(<TabIcon />);
      const atRest = dotOf(glyph(container));
      expect(motionOf(atRest)).toBe("");

      act(() => setUpdateNoticeState({ ...AVAILABLE, seen: true }));

      const fading = dotOf(glyph(container));
      // The same element, so the transition has the dot at rest to run from.
      expect(fading).toBe(atRest);
      const style = motionOf(fading);
      expect(style).toMatch(/transition: transform 450ms ease-out, opacity 450ms ease-out/);
      expect(style).toMatch(/transform: scale\(2\.2\)/);
      expect(style).toMatch(/opacity: 0/);
      expect(style).toMatch(/transform-origin: center/);
      expect(style).not.toMatch(/animation/);

      pass(DOT_FADE_MS);
      expect(dotOf(glyph(container))).toBeNull();

      pass(DOT_FADE_MS * 4);
      expect(dotOf(glyph(container))).toBeNull();
    });

    it("does not play when the card is dismissed", () => {
      setUpdateNoticeState(AVAILABLE);
      const { container } = render(<TabIcon />);

      act(() => setUpdateNoticeState({ ...AVAILABLE, available: false }));

      expect(dotOf(glyph(container))).toBeNull();
    });

    it("does not play when the release is installed", () => {
      setUpdateNoticeState(AVAILABLE);
      const { container } = render(<TabIcon />);

      act(() => setUpdateNoticeState({ ...AVAILABLE, available: false, newer: false, currentVersion: "1.1.0" }));

      expect(dotOf(glyph(container))).toBeNull();
    });
  });

  it("carries its own edge length, and takes the caller's over it", () => {
    const { container } = render(<TabIcon />);
    expect(glyph(container).getAttribute("width")).toBe("1.633em");

    const { container: sized } = render(<TabIcon size="3em" />);
    expect(glyph(sized).getAttribute("width")).toBe("3em");
    expect(glyph(sized).getAttribute("height")).toBe("3em");
  });
});
