/**
 * What the glyph hands the renderer: the generated artwork, and no motion.
 *
 * happy-dom performs no layout and runs no animation, so what these cases
 * establish is what is in the tree — which is all the animation case needs,
 * because an animation that is not authored cannot run. Whether the strip draws
 * the glyph at the size and in the colour it asks for is a device question,
 * named in this cut's device list (#1946).
 */

import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { TabIcon } from "./TabIcon";
import { TAB_ICON_ARC, TAB_ICON_BARS, TAB_ICON_HOLES, TAB_ICON_HOLE_R } from "./tabIconArt";

/** The glyph's own root, so a query cannot pick up something else's SVG. */
const glyph = (container: HTMLElement) => {
  const root = container.querySelector('[data-testid="tender-tab-icon"]');
  if (!root) throw new Error("the glyph did not render");
  return root;
};

describe("TabIcon", () => {
  it("draws the generated artwork: the arc, both bars and the four holes", () => {
    const { container } = render(<TabIcon />);
    const root = glyph(container);

    const drawn = [...root.querySelectorAll("path")].map((path) => path.getAttribute("d"));
    expect(drawn).toContain(TAB_ICON_ARC.d);
    expect(drawn).toContain(TAB_ICON_BARS.a);
    expect(drawn).toContain(TAB_ICON_BARS.b);

    const holes = [...root.querySelectorAll("circle")].map((circle) => ({
      cx: Number(circle.getAttribute("cx")),
      cy: Number(circle.getAttribute("cy")),
      r: Number(circle.getAttribute("r")),
    }));
    expect(holes).toEqual(TAB_ICON_HOLES.map((hole) => ({ cx: hole.cx, cy: hole.cy, r: TAB_ICON_HOLE_R })));
  });

  it("cuts the holes out of the body instead of drawing them on it", () => {
    // A hole drawn as a filled dot would disappear into the bar it sits on, so
    // the four circles only mean anything while the body reads them as a mask.
    const root = glyph(render(<TabIcon />).container);

    const mask = root.querySelector("mask");
    if (!mask) throw new Error("the glyph defines no mask");
    expect(mask.querySelectorAll("circle")).toHaveLength(TAB_ICON_HOLES.length);

    const masked = root.querySelector(`[mask="url(#${mask.getAttribute("id")})"]`);
    if (!masked) throw new Error("nothing in the glyph is masked by it");
    expect(masked.querySelector(`path[d="${TAB_ICON_BARS.a}"]`)).not.toBeNull();
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
