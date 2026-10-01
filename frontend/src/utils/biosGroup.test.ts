import { describe, expect, it } from "vitest";
import { BIOS_GROUP_PHRASES, groupBlock, oneOfRowLine, oneOfWords, regionNames } from "./biosGroup";
import { componentSources } from "../test-utils/componentSources";
import type { OneOfGroupVerdict } from "../types";

describe("the region names", () => {
  it("names the three regions the resolver states the way RomM and the BIOS descriptions do", () => {
    expect(regionNames(["ntsc-j", "ntsc-u", "pal"])).toBe("Japan, USA and Europe");
  });

  it("joins two with 'and' and leaves one alone", () => {
    expect(regionNames(["ntsc-j", "pal"])).toBe("Japan and Europe");
    expect(regionNames(["ntsc-u"])).toBe("USA");
  });

  it("prints a region it has no name for in the resolver's own spelling", () => {
    expect(regionNames(["ntsc-k"])).toBe("NTSC-K");
    expect(regionNames(["pal-m", "ntsc-u"])).toBe("PAL-M and USA");
  });
});

describe("a file's place in a group", () => {
  it("names the discs it is for on an emulator's line", () => {
    expect(oneOfWords({ regions: ["ntsc-u"], every_region: false })).toBe("for USA discs");
    expect(oneOfWords({ regions: ["ntsc-j", "pal"], every_region: false })).toBe("for Japan and Europe discs");
  });

  it("says the emulator boots it for every region where it serves them all", () => {
    expect(oneOfWords({ regions: ["ntsc-j", "ntsc-u", "pal"], every_region: true })).toBe("boots it for every region");
  });

  it("adds whether it is in place on the row's line, and nothing where that is not settled", () => {
    const membership = { regions: ["ntsc-u"], every_region: false };
    expect(oneOfRowLine(membership, true)).toBe("for USA discs · ✓ in place");
    expect(oneOfRowLine(membership, false)).toBe("for USA discs · ✗ missing");
    expect(oneOfRowLine(membership, null)).toBe("for USA discs");
    expect(oneOfRowLine({ regions: ["ntsc-u", "pal"], every_region: true }, true)).toBe(
      "for every region · ✓ in place",
    );
  });
});

const beetle = (game: string[], missing: string[]): OneOfGroupVerdict => ({
  state: game.length > 0 ? "unmet" : "partial",
  covered: game.length > 0 ? [] : ["ntsc-u"],
  missing,
  unchecked: [],
  game_regions: game,
  regions: ["ntsc-j", "ntsc-u", "pal"],
  options: [
    { file_name: "scph5500.bin", regions: ["ntsc-j"], satisfied: false },
    { file_name: "scph5501.bin", regions: ["ntsc-u"], satisfied: true },
    { file_name: "scph5502.bin", regions: ["pal"], satisfied: false },
  ],
});

describe("the group's block on the game page", () => {
  it("lists one line per option under the emulator's heading, marking the game's region", () => {
    const block = groupBlock(beetle(["ntsc-j"], ["ntsc-j"]), "Beetle PSX");

    expect(block.heading).toBe("Beetle PSX · one image per disc region");
    expect(block.lines).toEqual([
      { text: "Japan · scph5500.bin · missing ← this game's region", tone: "missing" },
      { text: "USA · scph5501.bin · in place", tone: "here" },
      { text: "Europe · scph5502.bin · missing", tone: "other" },
    ]);
  });

  it("puts the game's region first and the others after it in the group's order", () => {
    const block = groupBlock(beetle(["pal"], ["pal"]), "Beetle PSX");

    expect(block.lines.map((line) => line.text)).toEqual([
      "Europe · scph5502.bin · missing ← this game's region",
      "Japan · scph5500.bin · missing",
      "USA · scph5501.bin · in place",
    ]);
  });

  it("keeps the other regions in the group's order, never sorted by name", () => {
    // An invented group whose order is neither alphabetical by file nor by region.
    const unsorted: OneOfGroupVerdict = {
      state: "partial",
      covered: ["mid"],
      missing: ["zeta", "alpha"],
      unchecked: [],
      game_regions: ["mid"],
      regions: ["zeta", "mid", "alpha"],
      options: [
        { file_name: "zeta.rom", regions: ["zeta"], satisfied: false },
        { file_name: "mid.rom", regions: ["mid"], satisfied: true },
        { file_name: "alpha.rom", regions: ["alpha"], satisfied: false },
      ],
    };

    expect(groupBlock(unsorted, "Arcadia").lines.map((line) => line.text)).toEqual([
      "MID · mid.rom · in place ← this game's region",
      "ZETA · zeta.rom · missing",
      "ALPHA · alpha.rom · missing",
    ]);
  });

  it("lays out a group of any console the same way, from the verdict alone", () => {
    // No such console exists: every word comes off the data.
    const invented: OneOfGroupVerdict = {
      state: "met",
      covered: ["east"],
      missing: [],
      unchecked: [],
      game_regions: ["east"],
      regions: ["north", "south", "east"],
      options: [
        { file_name: "north.rom", regions: ["north"], satisfied: false },
        { file_name: "south-east.rom", regions: ["south", "east"], satisfied: true },
      ],
    };

    expect(groupBlock(invented, "Arcadia")).toEqual({
      heading: "Arcadia · one image per disc region",
      lines: [
        { text: "SOUTH and EAST · south-east.rom · in place ← this game's region", tone: "here" },
        { text: "NORTH · north.rom · missing", tone: "other" },
      ],
    });
  });

  it("marks every region missing where the verdict is the platform's", () => {
    const block = groupBlock(beetle([], ["ntsc-j", "pal"]), "Beetle PSX");

    expect(block.lines.map((line) => line.tone)).toEqual(["missing", "here", "missing"]);
    expect(block.lines.some((line) => line.text.includes("this game"))).toBe(false);
  });

  it("is a single line under its heading for one image that serves every region", () => {
    const found: OneOfGroupVerdict = {
      state: "met",
      covered: ["ntsc-u"],
      missing: [],
      unchecked: [],
      game_regions: ["ntsc-u"],
      regions: ["ntsc-j", "ntsc-u", "pal"],
      options: [{ file_name: "scph1001.bin", regions: ["ntsc-j", "ntsc-u", "pal"], satisfied: true }],
    };

    expect(groupBlock(found, "SwanStation")).toEqual({
      heading: "SwanStation · one image per disc region",
      lines: [{ text: "every region · scph1001.bin · in place ← this game's region", tone: "here" }],
    });
  });

  it("says an option nothing could read was not checked", () => {
    const unread: OneOfGroupVerdict = {
      ...beetle(["ntsc-u"], []),
      state: "unknown",
      unchecked: ["ntsc-u"],
      options: [{ file_name: "scph5501.bin", regions: ["ntsc-u"], satisfied: null }],
    };

    expect(groupBlock(unread, "Beetle PSX").lines[0]).toEqual({
      text: "USA · scph5501.bin · not checked ← this game's region",
      tone: "unchecked",
    });
  });
});

/**
 * The twin of `biosSummary.test.ts`'s lock, over this module's words: no
 * component may spell a group's membership or block for itself. The region
 * names are ordinary words and are not searched. What it cannot see is a
 * component inventing a NEW wording.
 */
describe("no surface words a group itself", () => {
  it.each(componentSources())("$path carries no group phrase of its own", ({ source }) => {
    const found = BIOS_GROUP_PHRASES.filter((phrase) => source.includes(phrase));
    expect(found).toEqual([]);
  });

  it("searches for something — an empty phrase list would pass over anything", () => {
    expect(BIOS_GROUP_PHRASES.length).toBeGreaterThan(0);
    for (const phrase of BIOS_GROUP_PHRASES) expect(phrase.length).toBeGreaterThan(8);
  });
});
