import { describe, expect, it } from "vitest";
import { BIOS_GROUP_PHRASES, oneOfRowLine, oneOfWords, regionsFull, regionsShort } from "./biosGroup";
import { componentSources } from "../test-utils/componentSources";

describe("the region names", () => {
  it("names the three regions the resolver states", () => {
    expect(regionsFull(["ntsc-j", "ntsc-u", "pal"])).toBe("Japan (NTSC-J), North America (NTSC-U) and Europe (PAL)");
    expect(regionsShort(["ntsc-j", "ntsc-u", "pal"])).toBe("Japan, North America and Europe");
  });

  it("joins two with 'and' and leaves one alone", () => {
    expect(regionsFull(["ntsc-j", "pal"])).toBe("Japan (NTSC-J) and Europe (PAL)");
    expect(regionsShort(["ntsc-u"])).toBe("North America");
  });

  it("prints a region it has no name for in the resolver's own spelling", () => {
    expect(regionsFull(["ntsc-k"])).toBe("NTSC-K");
    expect(regionsShort(["pal-m", "ntsc-u"])).toBe("PAL-M and North America");
  });
});

describe("a file's place in a group", () => {
  it("names the regions it serves", () => {
    expect(oneOfWords({ regions: ["ntsc-j"], every_region: false })).toBe("one of these · Japan (NTSC-J)");
  });

  it("says every region where it serves them all", () => {
    expect(oneOfWords({ regions: ["ntsc-j", "ntsc-u", "pal"], every_region: true })).toBe(
      "one of these · every region",
    );
  });

  it("adds whether it is in place on the row's line, and nothing where that is not settled", () => {
    const membership = { regions: ["ntsc-u"], every_region: false };
    expect(oneOfRowLine(membership, true)).toBe("one of these · North America (NTSC-U) · ✓ in place");
    expect(oneOfRowLine(membership, false)).toBe("one of these · North America (NTSC-U) · ✗ missing");
    expect(oneOfRowLine(membership, null)).toBe("one of these · North America (NTSC-U)");
  });
});

/**
 * The twin of `biosSummary.test.ts`'s lock, over this module's words: no
 * component may spell a region name or a group membership for itself. What it
 * cannot see is a component inventing a NEW wording for either.
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
