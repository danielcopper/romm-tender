import "@testing-library/jest-dom/vitest";
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { SvgThrobber } from "./SvgThrobber";
import { throbberIn } from "../../test-utils/throbber";

describe("Desktop SvgThrobber", () => {
  it("draws romm-throbber's ring: 18 px, a 2 px stroke at 0.3 white, with a white quarter", () => {
    const { container } = render(<SvgThrobber />);

    const svg = throbberIn(container) as SVGElement;
    expect(svg).toHaveAttribute("width", "18");
    expect(svg).toHaveAttribute("height", "18");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    const track = svg.querySelector("circle");
    expect(track).toHaveAttribute("r", "8");
    expect(track).toHaveAttribute("stroke", "rgba(255,255,255,0.3)");
    expect(track).toHaveAttribute("stroke-width", "2");
    const quarter = svg.querySelector("path");
    expect(quarter).toHaveAttribute("d", "M9 1a8 8 0 0 1 8 8");
    expect(quarter).toHaveAttribute("stroke", "#fff");
    expect(quarter).toHaveAttribute("stroke-width", "2");
  });

  it("turns the quarter once per 0.8 s about the ring's centre, for as long as it is drawn", () => {
    const { container } = render(<SvgThrobber />);

    const turn = container.querySelector("path animateTransform");
    expect(turn).toHaveAttribute("attributeName", "transform");
    expect(turn).toHaveAttribute("type", "rotate");
    expect(turn).toHaveAttribute("from", "0 9 9");
    expect(turn).toHaveAttribute("to", "360 9 9");
    expect(turn).toHaveAttribute("dur", "0.8s");
    expect(turn).toHaveAttribute("repeatCount", "indefinite");
  });

  it("adds no stylesheet and carries no class one could reach", () => {
    const sheets = () => document.querySelectorAll("style, link[rel=stylesheet]").length;
    const before = sheets();

    const { container } = render(<SvgThrobber />);

    expect(sheets()).toBe(before);
    expect(container.querySelectorAll("[class]")).toHaveLength(0);
  });

  it("does not shrink beside a label, and takes the style its place asks for", () => {
    const { container } = render(<SvgThrobber style={{ margin: "1px" }} />);

    expect(throbberIn(container)).toHaveStyle({ flexShrink: "0", margin: "1px" });
  });
});
