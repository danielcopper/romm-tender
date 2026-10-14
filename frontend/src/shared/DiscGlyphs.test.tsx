import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import type { DiscSelection } from "../api/backend";
import { buildDiscOptions, DiscStack, DiscWithNumber, DISC_GREY, DISC_ACCENT } from "./DiscGlyphs";

const m3uSelection: DiscSelection = {
  multi_disc: true,
  discs: [
    { filename: "ff7 (Disc 1).cue", label: "Disc 1", index: 1 },
    { filename: "ff7 (Disc 2).cue", label: "Disc 2", index: 2 },
    { filename: "ff7 (Disc 3).cue", label: "Disc 3", index: 3 },
  ],
  selected: null,
  default: { kind: "m3u", label: "All discs (m3u)", filename: "ff7.m3u" },
};

const discDefaultSelection: DiscSelection = {
  multi_disc: true,
  discs: [
    { filename: "game (Disc 1).chd", label: "Disc 1", index: 1 },
    { filename: "game (Disc 2).chd", label: "Disc 2", index: 2 },
  ],
  selected: "game (Disc 2).chd",
  default: { kind: "disc", label: "Disc 1", filename: "game (Disc 1).chd" },
};

describe("buildDiscOptions", () => {
  it("builds options including m3u default when present", () => {
    const options = buildDiscOptions(m3uSelection);
    expect(options).toHaveLength(4);
    expect(options[0]).toMatchObject({ data: null, text: "All discs (m3u)" });
    expect(options[1]).toMatchObject({ data: "ff7 (Disc 1).cue", text: "Disc 1" });
    expect(options[2]).toMatchObject({ data: "ff7 (Disc 2).cue", text: "Disc 2" });
    expect(options[3]).toMatchObject({ data: "ff7 (Disc 3).cue", text: "Disc 3" });
  });

  it("builds options without m3u entry when default is a disc", () => {
    const options = buildDiscOptions(discDefaultSelection);
    expect(options).toHaveLength(2);
    expect(options[0]).toMatchObject({ data: "game (Disc 1).chd", text: "Disc 1" });
    expect(options[1]).toMatchObject({ data: "game (Disc 2).chd", text: "Disc 2" });
  });

  it("returns empty array when discs or default are missing", () => {
    expect(buildDiscOptions({ multi_disc: true } as unknown as DiscSelection)).toEqual([]);
  });
});

describe("the disc glyphs", () => {
  it("defines DISC_GREY and DISC_ACCENT constants", () => {
    expect(DISC_GREY).toBe("#dcdedf");
    expect(DISC_ACCENT).toBe("#59b6ff");
  });

  it("renders DiscStack with two icons", () => {
    const { container } = render(<DiscStack size={20} color={DISC_GREY} />);
    const svgs = container.querySelectorAll("svg");
    expect(svgs).toHaveLength(2);
  });

  it("renders DiscWithNumber with number label", () => {
    const { container } = render(<DiscWithNumber size={20} color={DISC_ACCENT} num="2" />);
    expect(container.textContent).toContain("2");
    expect(container.querySelectorAll("svg")).toHaveLength(1);
  });
});
