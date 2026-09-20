/**
 * What the glyph hands the renderer: the generated artwork, and no motion.
 *
 * happy-dom performs no layout and runs no animation, so what these cases
 * establish is what is in the tree — which is all the animation case needs,
 * because an animation that is not authored cannot run. Whether the strip draws
 * the glyph at the size and in the colour it asks for is a device question, and
 * nothing in this suite reaches it.
 */

import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { TabIcon } from "./TabIcon";
import { TAB_ICON_ARC, TAB_ICON_BARS } from "./tabIconArt";

/** The glyph's own root, so a query cannot pick up something else's SVG. */
const glyph = (container: HTMLElement) => {
  const root = container.querySelector('[data-testid="tender-tab-icon"]');
  if (!root) throw new Error("the glyph did not render");
  return root;
};

describe("TabIcon", () => {
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
    // query names every SMIL animation element; motion driven from CSS
    // `@keyframes` or a rAF loop is authored nowhere in this tree and passes it
    // untouched.
    const root = glyph(render(<TabIcon />).container);

    expect(root.querySelectorAll("animate, animateTransform, animateMotion, animateColor, set, discard")).toHaveLength(
      0,
    );
  });

  it("carries its own edge length, and takes the caller's over it", () => {
    const { container } = render(<TabIcon />);
    expect(glyph(container).getAttribute("width")).toBe("1.633em");

    const { container: sized } = render(<TabIcon size="3em" />);
    expect(glyph(sized).getAttribute("width")).toBe("3em");
    expect(glyph(sized).getAttribute("height")).toBe("3em");
  });
});
