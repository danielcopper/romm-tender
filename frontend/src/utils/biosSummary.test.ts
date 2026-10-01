import { describe, expect, it } from "vitest";
import {
  BIOS_SUMMARY_PHRASES,
  biosSummary,
  groupBlocks,
  type BiosSummaryRow,
  type BiosSummarySource,
} from "./biosSummary";
import { componentSources } from "../test-utils/componentSources";
import type { BiosLevel, OneOfGroupVerdict } from "../types/firmware";

const EMULATOR = "SwanStation";

function summary(source: BiosSummarySource, level: BiosLevel | null = "ok", rows: readonly BiosSummaryRow[] = []) {
  return biosSummary({ active_core_label: EMULATOR, ...source }, rows, level);
}

describe("the seven states", () => {
  it("puts the console's own demand first, and names the emulator that cannot start", () => {
    expect(summary({ system_image: "absent" })).toEqual({
      status: "Needs a BIOS image",
      sentence: "SwanStation cannot start this system without a BIOS image",
    });
  });

  it("keeps the console's demand ahead of the level's decline", () => {
    // The pair does not arrive today — the backend lands an established absence
    // on `missing` — so this pins the ORDER rather than a live payload. It is the
    // one ordering the three surfaces used to each carry for themselves.
    const both = summary({ system_image: "absent", required_withheld: 2 }, "unknown");
    expect(both.status).toBe("Needs a BIOS image");
  });

  it("says the requirement is known and the readiness is not, for one withheld row", () => {
    expect(summary({ required_withheld: 1 }, "unknown")).toEqual({
      status: "Readiness unknown",
      sentence: "One file SwanStation requires could not be checked",
    });
  });

  it("counts the withheld rows where there is more than one", () => {
    expect(summary({ required_withheld: 3 }, "unknown").sentence).toBe(
      "3 files SwanStation requires could not be checked",
    );
  });

  it("words an unsettled console demand as its own gap", () => {
    expect(summary({ system_image: "unsettled" }, "unknown")).toEqual({
      status: "Readiness unknown",
      sentence: "Whether the BIOS image SwanStation needs is in place could not be established",
    });
  });

  it("prefers the withheld row over the unsettled console — it is the half that can name a file", () => {
    expect(summary({ required_withheld: 1, system_image: "unsettled" }, "unknown").sentence).toBe(
      "One file SwanStation requires could not be checked",
    );
  });

  it("says nothing could be established where neither gap applies", () => {
    expect(summary({}, "unknown")).toEqual({
      status: "Requirement unknown",
      sentence: "Nothing could be established about what SwanStation needs",
    });
  });

  it("states the required files as held where the level says ready", () => {
    expect(summary({ required_count: 3, required_downloaded: 3 }, "ok")).toEqual({
      status: "3 / 3 required",
      sentence: "All 3 files SwanStation requires are in place",
    });
  });

  it("adds the optional gap beside a finished requirement", () => {
    const rows: BiosSummaryRow[] = [
      { wanted: "optional", required_by_active: false, used_by_active: true, downloaded: false },
      { wanted: "optional", required_by_active: false, used_by_active: true, downloaded: false },
      { wanted: "optional", required_by_active: false, used_by_active: true, downloaded: true },
    ];
    expect(summary({ required_count: 2, required_downloaded: 2 }, "ok", rows).sentence).toBe(
      "All 2 files SwanStation requires are in place (2 optional missing)",
    );
  });

  // The sentence names one emulator, so its tail counts that emulator's set.
  // `wanted` is about every installed emulator and cannot say which one.
  it("counts a file the named emulator marks optional where another emulator requires it", () => {
    const rows: BiosSummaryRow[] = [
      { wanted: "needed", required_by_active: false, used_by_active: true, downloaded: false },
    ];
    expect(summary({ required_count: 2, required_downloaded: 2 }, "ok", rows).sentence).toBe(
      "All 2 files SwanStation requires are in place (1 optional missing)",
    );
  });

  it("does not count an optional file only another emulator declares", () => {
    const rows: BiosSummaryRow[] = [
      { wanted: "optional", required_by_active: false, used_by_active: false, downloaded: false },
    ];
    expect(summary({ required_count: 2, required_downloaded: 2 }, "ok", rows).sentence).toBe(
      "All 2 files SwanStation requires are in place",
    );
  });

  it("never counts a row no emulator declares, which the launching one is said to use", () => {
    // The backend answers `used_by_active: true` for a row nothing declares.
    const rows: BiosSummaryRow[] = [
      { wanted: "not_needed", required_by_active: false, used_by_active: true, downloaded: false },
      { wanted: "unknown", required_by_active: false, used_by_active: true, downloaded: false },
    ];
    expect(summary({ required_count: 2, required_downloaded: 2 }, "ok", rows).sentence).toBe(
      "All 2 files SwanStation requires are in place",
    );
  });

  it("says ONE file rather than `All 1 files`, and keeps the optional gap behind it", () => {
    // Reachable on the reference machine rather than a theoretical count:
    // DuckStation requires exactly one image on a stock RetroDECK.
    const rows: BiosSummaryRow[] = [
      { wanted: "optional", required_by_active: false, used_by_active: true, downloaded: false },
    ];
    expect(summary({ required_count: 1, required_downloaded: 1 }, "ok", rows)).toEqual({
      status: "1 / 1 required",
      sentence: "The one file SwanStation requires is in place (1 optional missing)",
    });
  });

  it("states the shortfall where the level is not ready", () => {
    expect(summary({ required_count: 4, required_downloaded: 1 }, "partial")).toEqual({
      status: "1 / 4 required",
      sentence: "1 of 4 files SwanStation requires are in place",
    });
  });

  it("says ONE file rather than `0 of 1 files` where that one is not there", () => {
    expect(summary({ required_count: 1, required_downloaded: 0 }, "missing")).toEqual({
      status: "0 / 1 required",
      sentence: "The one file SwanStation requires is not in place",
    });
  });

  it("names the set a zero count was taken over", () => {
    expect(summary({ required_count: 0 }, "ok")).toEqual({
      status: "Nothing required",
      sentence: "SwanStation marks none of its BIOS files as required",
    });
  });
});

describe("the emulator's name", () => {
  it("falls back to the role where the pick carries no label", () => {
    expect(biosSummary({ required_count: 0 }, [], "ok").sentence).toBe(
      "The launching emulator marks none of its BIOS files as required",
    );
  });

  it("keeps the role lower case where it is not the first word", () => {
    expect(biosSummary({ required_withheld: 1 }, [], "unknown").sentence).toBe(
      "One file the launching emulator requires could not be checked",
    );
  });

  it("never recases a real label — `mGBA` is the name its own catalogue spells", () => {
    expect(biosSummary({ required_count: 0, active_core_label: "mGBA" }, [], "ok").sentence).toBe(
      "mGBA marks none of its BIOS files as required",
    );
  });
});

describe("reading the payload", () => {
  it("falls back to the rows for counts a payload omits", () => {
    const rows: BiosSummaryRow[] = [
      { required_by_active: true, downloaded: true },
      { required_by_active: true, downloaded: false },
      { required_by_active: false, downloaded: false },
    ];
    expect(summary({}, "partial", rows)).toEqual({
      status: "1 / 2 required",
      sentence: "1 of 2 files SwanStation requires are in place",
    });
  });

  it("decides readiness by the count comparison where the payload carries no level", () => {
    expect(summary({ required_count: 2, required_downloaded: 2 }, null).sentence).toBe(
      "All 2 files SwanStation requires are in place",
    );
    expect(summary({ required_count: 2, required_downloaded: 1 }, null).sentence).toBe(
      "1 of 2 files SwanStation requires are in place",
    );
  });
});

/**
 * The drift lock.
 *
 * The whole point of the module is that no surface words these states for
 * itself, and the cheapest way to undo it is to write one sentence back into a
 * component "just this once". So every component is read as SOURCE and searched
 * for the phrases the module builds its answers from — which come from the module
 * itself, so a wording change moves the lock with it rather than leaving a second
 * copy of the list here to drift.
 *
 * **The searched set is swept, never listed** (`test-utils/componentSources.ts`),
 * because a hand-kept list is what failed here: it named two components while
 * three rendered these states, and the third went on spelling an older wording
 * of them with the suite green. A surface missing from such a list cannot be
 * told from one that never drifted, so correcting the list's count would have
 * left the failure intact and merely moved it to the next surface added.
 *
 * **What this lock sees is a phrase standing in a component as text.** What it
 * does NOT see is a component inventing a NEW wording for one of these seven
 * states — a fourth spelling of "nothing is required here", written from
 * scratch, passes every search there is, and no string comparison could ever
 * catch it. That is the gap, it is permanent, and it is why a green run here is
 * evidence about copied sentences and about nothing else. Reviewing a new BIOS
 * surface still means reading it.
 */
describe("a one-of group", () => {
  // One requirement, worded off the verdict alone — the emulator's name and the
  // regions. Approved wording; the region names are `utils/biosGroup.ts`'s.
  const group = (
    state: OneOfGroupVerdict["state"],
    covered: string[],
    missing: string[],
    game_regions: string[] = [],
  ): OneOfGroupVerdict => ({
    state,
    covered,
    missing,
    unchecked: [],
    game_regions,
    regions: [...covered, ...missing],
    options: [],
  });

  it("says the image is in place for every region", () => {
    const met = summary({
      required_count: 1,
      required_downloaded: 1,
      one_of_groups: [group("met", ["ntsc-j", "ntsc-u", "pal"], [])],
    });
    expect(met).toEqual({
      status: "1 / 1 required",
      sentence: "The BIOS image SwanStation needs is in place for every region",
    });
  });

  it("names the regions a partly covered group serves and the ones whose discs will not start", () => {
    const partial = biosSummary(
      {
        active_core_label: "Beetle PSX",
        required_count: 1,
        required_downloaded: 0,
        required_partial: 1,
        one_of_groups: [group("partial", ["ntsc-u"], ["ntsc-j", "pal"])],
      } as BiosSummarySource,
      [],
      "partial",
    );
    expect(partial).toEqual({
      status: "0 / 1 required · USA only",
      sentence: "Beetle PSX has a BIOS image for USA only — Japan and Europe discs will not start",
    });
  });

  it("says the emulator cannot start the system where nothing is in place", () => {
    const unmet = biosSummary(
      {
        active_core_label: "Beetle PSX",
        required_count: 1,
        required_downloaded: 0,
        one_of_groups: [group("unmet", [], ["ntsc-j", "ntsc-u", "pal"])],
      },
      [],
      "missing",
    );
    expect(unmet.sentence).toBe("Beetle PSX cannot start this system without a BIOS image");
  });

  it("says whether it is in place could not be checked, never a colour of its own", () => {
    const unknown = biosSummary(
      {
        active_core_label: "Beetle PSX",
        required_count: 1,
        required_withheld: 1,
        one_of_groups: [{ ...group("unknown", [], []), unchecked: ["ntsc-u"] }],
      },
      [],
      "unknown",
    );
    expect(unknown).toEqual({
      status: "Readiness unknown",
      sentence: "Whether the BIOS image Beetle PSX needs is in place could not be checked",
    });
  });

  it("words the game page's verdict for the game's own region", () => {
    const covered = biosSummary(
      {
        active_core_label: "Beetle PSX",
        required_count: 1,
        required_downloaded: 1,
        one_of_groups: [group("met", ["ntsc-u"], [], ["ntsc-u"])],
      },
      [],
      "ok",
    );
    const uncovered = biosSummary(
      {
        active_core_label: "Beetle PSX",
        required_count: 1,
        required_downloaded: 0,
        one_of_groups: [group("unmet", [], ["ntsc-j"], ["ntsc-j"])],
      },
      [],
      "missing",
    );
    expect(covered.sentence).toBe("The BIOS image Beetle PSX needs for this game's region (USA) is in place");
    expect(uncovered.sentence).toBe("Beetle PSX has no BIOS image for this game's region (Japan)");
  });

  it("names the file the game's own region needs where the verdict lists it", () => {
    const beetle: OneOfGroupVerdict = {
      ...group("unmet", [], ["ntsc-u"], ["ntsc-u"]),
      regions: ["ntsc-j", "ntsc-u", "pal"],
      options: [
        { file_name: "scph5500.bin", regions: ["ntsc-j"], satisfied: true },
        { file_name: "scph5501.bin", regions: ["ntsc-u"], satisfied: false },
      ],
    };
    const missing = biosSummary(
      { active_core_label: "Beetle PSX", required_count: 1, required_downloaded: 0, one_of_groups: [beetle] },
      [],
      "missing",
    );
    expect(missing.sentence).toBe("Beetle PSX needs scph5501.bin to start this game (USA) — it is missing");
  });

  it("names every file a game of several regions could start from", () => {
    const beetle: OneOfGroupVerdict = {
      ...group("unmet", [], ["ntsc-u", "pal"], ["ntsc-u", "pal"]),
      regions: ["ntsc-j", "ntsc-u", "pal"],
      options: [
        { file_name: "scph5501.bin", regions: ["ntsc-u"], satisfied: false },
        { file_name: "scph5502.bin", regions: ["pal"], satisfied: false },
      ],
    };
    const missing = biosSummary(
      { active_core_label: "Beetle PSX", required_count: 1, required_downloaded: 0, one_of_groups: [beetle] },
      [],
      "missing",
    );
    expect(missing.sentence).toBe(
      "Beetle PSX needs scph5501.bin or scph5502.bin to start this game (USA and Europe) — neither is in place",
    );
  });

  const swanstation: OneOfGroupVerdict = {
    ...group("met", ["ntsc-u"], [], ["ntsc-u"]),
    regions: ["ntsc-j", "ntsc-u", "pal"],
    options: [{ file_name: "scph1001.bin", regions: ["ntsc-j", "ntsc-u", "pal"], satisfied: true }],
  };

  it("names the image that starts the game where the folder search found it and it serves every region", () => {
    const rows: BiosSummaryRow[] = [{ file_name: "scph1001.bin", caveats: ["firmware-image-identified"] }];
    const met = summary({ required_count: 1, required_downloaded: 1, one_of_groups: [swanstation] }, "ok", rows);
    expect(met.sentence).toBe(
      "SwanStation starts this game with scph1001.bin — found in the BIOS folder, it serves every region",
    );
  });

  it("does not say the image was found in the folder where nothing says a search found it", () => {
    // Every region's own setting can name one file; then the core opened it by
    // name and no search was made.
    const rows: BiosSummaryRow[] = [{ file_name: "scph1001.bin", caveats: [] }];
    const met = summary({ required_count: 1, required_downloaded: 1, one_of_groups: [swanstation] }, "ok", rows);
    expect(met.sentence).toBe("SwanStation starts this game with scph1001.bin, which serves every region");
  });

  it("names three files with none of them in place", () => {
    const three: OneOfGroupVerdict = {
      ...group("unmet", [], ["ntsc-j", "ntsc-u", "pal"], ["ntsc-j", "ntsc-u", "pal"]),
      options: [
        { file_name: "scph5500.bin", regions: ["ntsc-j"], satisfied: false },
        { file_name: "scph5501.bin", regions: ["ntsc-u"], satisfied: false },
        { file_name: "scph5502.bin", regions: ["pal"], satisfied: false },
      ],
    };
    expect(summary({ required_count: 1, required_downloaded: 0, one_of_groups: [three] }, "missing").sentence).toBe(
      "SwanStation needs scph5500.bin, scph5501.bin or scph5502.bin to start this game (Japan, USA and Europe) — none of them is in place",
    );
  });

  it("never lets a named file stand for a region that has no image at all", () => {
    // USA has no option; only Europe's file can be named, and only for Europe.
    const mixed: OneOfGroupVerdict = {
      ...group("unmet", [], ["ntsc-u", "pal"], ["ntsc-u", "pal"]),
      options: [{ file_name: "scph5502.bin", regions: ["pal"], satisfied: false }],
    };
    expect(summary({ required_count: 1, required_downloaded: 0, one_of_groups: [mixed] }, "missing").sentence).toBe(
      "SwanStation needs scph5502.bin to start this game (Europe) — it is missing, and there is no BIOS image for USA",
    );
  });

  it("heads each group's block with the emulator, or with its role where the pick has no name", () => {
    const named = groupBlocks({ active_core_label: "Beetle PSX", one_of_groups: [swanstation, swanstation] });
    const unnamed = groupBlocks({ active_core_label: null, one_of_groups: [{ ...swanstation, options: [] }] });

    expect(named).toHaveLength(2);
    expect(unnamed[0]?.heading).toBe("The launching emulator · one image per disc region");
  });

  it("words a group of any console the same way, naming regions it has no name for in their own spelling", () => {
    const invented = biosSummary(
      {
        active_core_label: "Arcadia",
        required_count: 1,
        required_downloaded: 0,
        one_of_groups: [group("partial", ["north"], ["south", "east"])],
      },
      [],
      "partial",
    );
    expect(invented).toEqual({
      status: "0 / 1 required · NORTH only",
      sentence: "Arcadia has a BIOS image for NORTH only — SOUTH and EAST discs will not start",
    });
  });

  it("leaves the headline to a required file that is missing beside a met group", () => {
    // The group sentence says everything is in place; with a plain required
    // file absent beside it, that is not what the dot means.
    const both = summary(
      { required_count: 2, required_downloaded: 1, one_of_groups: [group("met", ["ntsc-u"], [])] },
      "partial",
    );
    expect(both).toEqual({
      status: "1 / 2 required",
      sentence: "1 of 2 files SwanStation requires are in place",
    });
  });

  it("leaves the headline to a required file nothing could judge beside a partial group", () => {
    const both = summary(
      {
        required_count: 2,
        required_downloaded: 0,
        required_withheld: 1,
        required_partial: 1,
        one_of_groups: [group("partial", ["ntsc-u"], ["pal"])],
      } as BiosSummarySource,
      "unknown",
    );
    expect(both.sentence).toBe("One file SwanStation requires could not be checked");
  });

  it("words a missing plain file beside an unknown group as the missing file it is", () => {
    // The withheld count here is the group alone; the plain file was checked
    // and is absent, so "could not be checked" would be untrue of it.
    const both = summary(
      {
        required_count: 2,
        required_downloaded: 0,
        required_withheld: 1,
        one_of_groups: [{ ...group("unknown", [], []), unchecked: ["ntsc-u"] }],
      },
      "unknown",
    );
    expect(both).toEqual({ status: "0 / 2 required", sentence: "0 of 2 files SwanStation requires are in place" });
  });

  it("counts only the plain files nothing could judge beside an unknown group", () => {
    const both = summary(
      {
        required_count: 2,
        required_downloaded: 0,
        required_withheld: 2,
        one_of_groups: [{ ...group("unknown", [], []), unchecked: ["ntsc-u"] }],
      },
      "unknown",
    );
    expect(both.sentence).toBe("One file SwanStation requires could not be checked");
  });

  it("keeps an unmet group's sentence beside a missing plain file", () => {
    const both = summary(
      { required_count: 2, required_downloaded: 0, one_of_groups: [group("unmet", [], ["ntsc-u"])] },
      "missing",
    );
    expect(both.sentence).toBe("SwanStation cannot start this system without a BIOS image");
  });

  it("speaks for the group ahead of the console's coarser reading", () => {
    // The backend never sends the two together; the order is a guard.
    const both = summary({
      system_image: "absent",
      required_count: 1,
      required_downloaded: 1,
      one_of_groups: [group("met", ["ntsc-u"], [])],
    });
    expect(both.sentence).toBe("The BIOS image SwanStation needs is in place for every region");
  });
});

describe("no surface words a summary itself", () => {
  it.each(componentSources())("$path carries no summary phrase of its own", ({ source }) => {
    const found = BIOS_SUMMARY_PHRASES.filter((phrase) => source.includes(phrase));
    expect(found).toEqual([]);
  });

  it("searches for something — an empty phrase list would pass over anything", () => {
    expect(BIOS_SUMMARY_PHRASES.length).toBeGreaterThan(0);
    for (const phrase of BIOS_SUMMARY_PHRASES) expect(phrase.length).toBeGreaterThan(8);
  });

  it("searches the surfaces that render these states, not an empty sweep", () => {
    // The sweep throws when a pattern finds nothing, so this pins the other
    // half: that it really reaches the three components rendering a BIOS state
    // today. A `bigpicture/` pattern narrowed to one of its subdirectories would
    // still find files and would silently stop covering the rest.
    const paths = componentSources().map((entry) => entry.path);
    expect(paths).toEqual(
      expect.arrayContaining([
        "bigpicture/BiosTab.tsx",
        "bigpicture/library/PlatformDetail.tsx",
        "bigpicture/library/PlatformsTab.tsx",
      ]),
    );
  });

  it("searches shared/ as well as bigpicture/", () => {
    const paths = componentSources().map((entry) => entry.path);
    expect(paths.filter((path) => path.startsWith("shared/"))).not.toEqual([]);
  });
});
