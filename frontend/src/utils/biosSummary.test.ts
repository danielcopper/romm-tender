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

const EMULATOR = "Example Emulator";

function summary(source: BiosSummarySource, level: BiosLevel | null = "ok", rows: readonly BiosSummaryRow[] = []) {
  return biosSummary({ active_core_label: EMULATOR, locating: "by-name-then-content", ...source }, rows, level);
}

// SwanStation's five PlayStation images, none of them in place — all optional, as a
// libretro declaration has to mark them.
const FIVE_IMAGES: BiosSummaryRow[] = ["scph5500", "scph5501", "scph5502", "scph1001", "scph7001"].map((name) => ({
  file_name: `${name}.bin`,
  wanted: "optional",
  required_by_active: false,
  used_by_active: true,
  downloaded: false,
  satisfied: false,
}));

describe("the states", () => {
  it("says the console does not start without an image, and none of the emulator's is in place", () => {
    expect(
      biosSummary(
        { active_core_label: "SwanStation", console_needs_image: true },
        FIVE_IMAGES,
        "missing",
        "PlayStation",
      ),
    ).toEqual({
      status: "Needs a BIOS image",
      sentence: "PlayStation does not start without a BIOS image, and none of the five SwanStation knows is in place",
    });
  });

  it("names the system as itself where the caller has no name for it", () => {
    expect(
      summary({ active_core_label: "SwanStation", console_needs_image: true }, "missing", FIVE_IMAGES).sentence,
    ).toBe("This system does not start without a BIOS image, and none of the five SwanStation knows is in place");
  });

  it("states the console's demand only under a verdict that is not met", () => {
    expect(summary({ console_needs_image: true }, "unknown", FIVE_IMAGES).status).not.toBe("Needs a BIOS image");
  });

  it("does not state the console's demand where one of the emulator's images is in place", () => {
    const held = FIVE_IMAGES.map((row, index) => (index === 0 ? { ...row, satisfied: true, downloaded: true } : row));
    expect(summary({ console_needs_image: true }, "missing", held).status).not.toBe("Needs a BIOS image");
  });

  it("says the requirement is known and the readiness is not, for one withheld row", () => {
    expect(summary({ required_withheld: 1 }, "unknown")).toEqual({
      status: "Readiness unknown",
      sentence: "One file Example Emulator requires could not be checked",
    });
  });

  it("counts the withheld rows where there is more than one", () => {
    expect(summary({ required_withheld: 3 }, "unknown").sentence).toBe(
      "3 files Example Emulator requires could not be checked",
    );
  });

  it("names a refused required declaration with the resolver's reason", () => {
    const refused = [{ declared: "../outside.bin", required: true, reason: "firmware-path-escapes-root" }];
    expect(summary({ refused }, "unknown")).toEqual({
      status: "Readiness unknown",
      sentence:
        "Example Emulator requires ../outside.bin, which lies outside the BIOS folder — " +
        "so whether it is ready could not be established",
    });
  });

  it("says nothing of a refused optional declaration, which changes no verdict", () => {
    const refused = [{ declared: "../outside.bin", required: false, reason: "firmware-path-escapes-root" }];
    expect(summary({ refused, required_count: 1, required_downloaded: 1 }, "ok").sentence).toBe(
      "The one file Example Emulator requires is in place",
    );
  });

  it("says no BIOS is chosen in the emulator's settings, in the resolver's words", () => {
    const notes = [{ kind: "bios-unchosen" as const, setting: "Filenames/BIOS", tied: null, regions: [] }];
    expect(biosSummary({ active_core_label: "PCSX2", emulator_notes: notes }, [], "unknown")).toEqual({
      status: "No BIOS chosen",
      sentence: "No BIOS is chosen in PCSX2's settings, and no game boots until one is",
    });
  });

  it("says no BIOS is chosen on the answer PCSX2 (Standalone) really sends, where it was not read", () => {
    // A packaged card with no rows and an open verdict counts as not read; the
    // empty setting is WHY nothing could be established, so it is what is said.
    const answer = {
      active_core_label: "PCSX2 (Standalone)",
      emulator_read: false,
      locating: "unestablished" as const,
      emulator_declaration: "packaged" as const,
      console_needs_image: false,
      refused: [],
      emulator_notes: [{ kind: "bios-unchosen" as const, setting: "Filenames/BIOS", tied: null, regions: [] }],
      content_search: null,
      required_count: 0,
      required_downloaded: 0,
      required_withheld: 0,
    };
    expect(biosSummary(answer, [], "unknown")).toEqual({
      status: "No BIOS chosen",
      sentence: "No BIOS is chosen in PCSX2 (Standalone)'s settings, and no game boots until one is",
    });
  });

  it("adds a tie between two images without moving the verdict", () => {
    const notes = [{ kind: "images-tied" as const, setting: null, tied: 2, regions: ["pal"] }];
    const words = summary({ emulator_notes: notes, required_count: 1, required_downloaded: 1 }, "ok");
    expect(words.sentence).toBe(
      "The one file Example Emulator requires is in place — " +
        "for Europe two images are in place; which one Example Emulator uses is not established",
    );
    expect(words.status).toBe("1 / 1 required");
  });

  it("says nothing could be established about an emulator nobody read, ahead of everything else", () => {
    expect(summary({ emulator_read: false, required_withheld: 1 }, "unknown")).toEqual({
      status: "Requirement unknown",
      sentence: "Nothing could be established about what Example Emulator needs",
    });
  });

  it("never reads a launch with no identified emulator as needing nothing", () => {
    // What the backend answers where no launching emulator was identified: no
    // row is its requirement, so the required set is empty — and that empty set
    // is "could not be established", not "nothing required".
    const answer = { required_count: 0, required_downloaded: 0, required_withheld: 0, emulator_read: false };
    const words = biosSummary({ ...answer, active_core_label: null }, [], "unknown");
    expect(words).toEqual({
      status: "Requirement unknown",
      sentence: "Nothing could be established about what the launching emulator needs",
    });
    expect(words.status).not.toBe("Nothing required");
    expect(words.sentence).not.toContain("marks none");
    expect(words.sentence).not.toContain("are in place");
  });

  it("says nothing could be established where neither gap applies", () => {
    expect(summary({}, "unknown")).toEqual({
      status: "Requirement unknown",
      sentence: "Nothing could be established about what Example Emulator needs",
    });
  });

  it("states the required files as held where the level says ready", () => {
    expect(summary({ required_count: 3, required_downloaded: 3 }, "ok")).toEqual({
      status: "3 / 3 required",
      sentence: "All 3 files Example Emulator requires are in place",
    });
  });

  it("adds the optional gap beside a finished requirement", () => {
    const rows: BiosSummaryRow[] = [
      { wanted: "optional", required_by_active: false, used_by_active: true, downloaded: false },
      { wanted: "optional", required_by_active: false, used_by_active: true, downloaded: false },
      { wanted: "optional", required_by_active: false, used_by_active: true, downloaded: true },
    ];
    expect(summary({ required_count: 2, required_downloaded: 2 }, "ok", rows).sentence).toBe(
      "All 2 files Example Emulator requires are in place (2 optional missing)",
    );
  });

  // The sentence names one emulator, so its tail counts that emulator's set.
  // `wanted` alone does not say which emulator's need it is.
  it("counts a file the named emulator marks optional where another emulator requires it", () => {
    const rows: BiosSummaryRow[] = [
      { wanted: "needed", required_by_active: false, used_by_active: true, downloaded: false },
    ];
    expect(summary({ required_count: 2, required_downloaded: 2 }, "ok", rows).sentence).toBe(
      "All 2 files Example Emulator requires are in place (1 optional missing)",
    );
  });

  it("does not count an optional file only another emulator declares", () => {
    const rows: BiosSummaryRow[] = [
      { wanted: "optional", required_by_active: false, used_by_active: false, downloaded: false },
    ];
    expect(summary({ required_count: 2, required_downloaded: 2 }, "ok", rows).sentence).toBe(
      "All 2 files Example Emulator requires are in place",
    );
  });

  it("never counts a row no emulator declares, which the launching one is said to use", () => {
    // The backend answers `used_by_active: true` for a row nothing declares.
    const rows: BiosSummaryRow[] = [
      { wanted: "not_needed", required_by_active: false, used_by_active: true, downloaded: false },
      { wanted: "unknown", required_by_active: false, used_by_active: true, downloaded: false },
    ];
    expect(summary({ required_count: 2, required_downloaded: 2 }, "ok", rows).sentence).toBe(
      "All 2 files Example Emulator requires are in place",
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
      sentence: "The one file Example Emulator requires is in place (1 optional missing)",
    });
  });

  it("states the shortfall where the level is not ready", () => {
    expect(summary({ required_count: 4, required_downloaded: 1 }, "partial")).toEqual({
      status: "1 / 4 required",
      sentence: "1 of 4 files Example Emulator requires are in place",
    });
  });

  it("says ONE file rather than `0 of 1 files` where that one is not there", () => {
    expect(summary({ required_count: 1, required_downloaded: 0 }, "missing")).toEqual({
      status: "0 / 1 required",
      sentence: "The one file Example Emulator requires is not in place",
    });
  });

  it("names the set a zero count was taken over", () => {
    expect(summary({ required_count: 0 }, "ok")).toEqual({
      status: "Nothing required",
      sentence: "Example Emulator marks none of its BIOS files as required",
    });
  });
});

describe("the game page's own region", () => {
  const forGame = (options: OneOfGroupVerdict["options"], unchecked: string[]): OneOfGroupVerdict => ({
    state: "unknown",
    covered: [],
    missing: [],
    unchecked,
    game_regions: ["ntsc-u"],
    regions: ["ntsc-j", "ntsc-u"],
    options,
  });

  it("names the region whose image could not be checked", () => {
    const group = forGame(
      [
        { file_name: "scph5500.bin", regions: ["ntsc-j"], satisfied: true },
        { file_name: "scph5501.bin", regions: ["ntsc-u"], satisfied: null },
      ],
      ["ntsc-u"],
    );
    expect(
      summary({ one_of_groups: [group], required_count: 1, required_downloaded: 0, required_withheld: 1 }, "unknown")
        .sentence,
    ).toBe(
      "Whether the BIOS image Example Emulator needs for this game's region (USA) is in place could not be checked",
    );
  });

  it("names the region no image is stated for", () => {
    const group = forGame([{ file_name: "scph5500.bin", regions: ["ntsc-j"], satisfied: true }], ["ntsc-u"]);
    expect(
      summary({ one_of_groups: [group], required_count: 1, required_downloaded: 0, required_withheld: 1 }, "unknown"),
    ).toEqual({
      status: "Readiness unknown",
      sentence:
        "Example Emulator states no BIOS image for this game's region (USA), so whether it starts could not be established",
    });
  });
});

describe("how the emulator finds its firmware words the answer", () => {
  const required = (name: string, satisfied: boolean | null): BiosSummaryRow => ({
    file_name: name,
    wanted: "needed",
    required_by_active: true,
    used_by_active: true,
    downloaded: satisfied === true,
    satisfied,
  });

  it("counts what an emulator that opens its files by name has in place, under a red verdict", () => {
    const rows = [required("bios7.bin", true), required("bios9.bin", true), required("firmware.bin", false)];
    expect(
      biosSummary(
        { active_core_label: "melonDS", locating: "by-name", required_count: 3, required_downloaded: 2 },
        rows,
        "missing",
      ),
    ).toEqual({ status: "2 / 3 required", sentence: "2 of 3 files melonDS opens by name are in place" });
  });

  it("says every file it opens by name is in place under a met verdict", () => {
    expect(
      summary({ active_core_label: "melonDS", locating: "by-name", required_count: 3, required_downloaded: 3 }, "ok")
        .sentence,
    ).toBe("All 3 files melonDS opens by name are in place");
  });

  it("says what an emulator nobody read lists, with the note that how it finds its BIOS is not established", () => {
    const rows = [required("mpr-17933.bin", true), required("saturn_bios.bin", true)];
    expect(
      biosSummary(
        { active_core_label: "Beetle Saturn", locating: "unestablished", required_count: 2, required_downloaded: 2 },
        rows,
        "ok",
      ).sentence,
    ).toBe(
      "All 2 files Beetle Saturn lists as required are in place. How Beetle Saturn finds its BIOS has not been established",
    );
  });

  it("names the missing file such an emulator lists, red, with the same note", () => {
    expect(
      biosSummary(
        { active_core_label: "gpSP", locating: "unestablished", required_count: 1, required_downloaded: 0 },
        [required("gba_bios.bin", false)],
        "missing",
      ).sentence,
    ).toBe(
      "gba_bios.bin, which gpSP lists as required, is not in place. How gpSP finds its BIOS has not been established",
    );
  });

  it("keeps an emulator that marks nothing required at Nothing required, without the note", () => {
    expect(summary({ locating: "unestablished", required_count: 0 }, "ok")).toEqual({
      status: "Nothing required",
      sentence: "Example Emulator marks none of its BIOS files as required",
    });
  });

  it("says Tender cannot tell what an emulator the resolver does not cover needs", () => {
    expect(biosSummary({ active_core_label: "PPSSPP", emulator_declaration: "unsupported" }, [], "unknown")).toEqual({
      status: "Requirement unknown",
      sentence: "Tender cannot tell what PPSSPP needs yet — the emulator's firmware is not covered by the resolver yet",
    });
  });

  it("names a missing file and one nothing could check together, red", () => {
    const rows = [required("bios7.bin", false), required("bios9.bin", null), required("firmware.bin", true)];
    expect(
      summary(
        {
          active_core_label: "melonDS",
          locating: "by-name",
          required_count: 3,
          required_downloaded: 1,
          required_withheld: 1,
        },
        "missing",
        rows,
      ),
    ).toEqual({
      status: "1 / 3 required · 1 not checked",
      sentence: "One file melonDS opens by name is not in place, and one could not be checked",
    });
  });

  it("states what a search by content found rather than counting it", () => {
    expect(
      biosSummary(
        {
          active_core_label: "DuckStation",
          locating: "by-name-then-content",
          content_search: "found",
          required_count: 1,
          required_downloaded: 1,
        },
        [required("scph5500.bin", true)],
        "ok",
      ),
    ).toEqual({ status: "Found by content", sentence: "DuckStation recognises scph5500.bin in the BIOS folder" });
  });

  // A settled search of the folder that reached no image is the resolver's own
  // statement (`firmware-path-names-no-file` with `dir` and `candidates`),
  // carried as the emulator's note — never read off the verdict.
  const foundNothing = (candidates: number | null) => ({
    kind: "search-found-nothing" as const,
    setting: null,
    tied: null,
    dir: "/home/deck/retrodeck/bios",
    candidates,
    regions: ["ntsc-j", "ntsc-u", "pal"],
  });
  const searcher = (label: string, candidates: number | null) => ({
    active_core_label: label,
    locating: "by-name-then-content" as const,
    required_count: 1,
    required_downloaded: 0,
    emulator_notes: [foundNothing(candidates)],
  });

  it.each(["DuckStation", "SwanStation"])("says %s found no image in the folder it searched", (label) => {
    expect(biosSummary(searcher(label, 0), [required("scph5501.bin", false)], "missing")).toEqual({
      status: "No BIOS image found",
      sentence: `${label} found no BIOS image in bios/ for a Japan, USA and Europe console`,
    });
  });

  it.each([
    [2, "bios/ holds 2 files of a size DuckStation accepts, and it recognises none of them"],
    [1, "bios/ holds 1 file of a size DuckStation accepts, and it does not recognise it"],
  ])("says the folder holds %i candidates the emulator recognises none of", (candidates, sentence) => {
    expect(biosSummary(searcher("DuckStation", candidates), [required("scph5501.bin", false)], "missing")).toEqual({
      status: "No image recognised",
      sentence,
    });
  });

  // The shape a libretro core's search arrives in (SwanStation's): no requirement
  // rows, nothing required, and a statement of the search with its count — all
  // there is to word.
  const searchOnly = (candidates: number, label = "SwanStation") => ({
    active_core_label: label,
    locating: "by-name-then-content" as const,
    required_count: 0,
    required_downloaded: 0,
    emulator_notes: [foundNothing(candidates)],
  });

  it.each([
    [0, "No BIOS image found", "SwanStation found no BIOS image in bios/ for a Japan, USA and Europe console"],
    [2, "No image recognised", "bios/ holds 2 files of a size SwanStation accepts, and it recognises none of them"],
  ])(
    "words the search ahead of the open verdict, on an answer that holds nothing but the search (%i)",
    (candidates, status, sentence) => {
      expect(biosSummary(searchOnly(candidates), [], "unknown")).toEqual({ status, sentence });
    },
  );

  it("leaves the search unsaid where a one-of group speaks for it", () => {
    const group: OneOfGroupVerdict = {
      state: "met",
      covered: ["ntsc-j", "ntsc-u", "pal"],
      missing: [],
      unchecked: [],
      game_regions: [],
      regions: ["ntsc-j", "ntsc-u", "pal"],
      options: [{ file_name: "scph1001.bin", regions: ["ntsc-j", "ntsc-u", "pal"], satisfied: true }],
    };
    const words = biosSummary(
      { ...searcher("SwanStation", 0), required_count: 1, required_downloaded: 1, one_of_groups: [group] },
      [],
      "ok",
    );
    expect(words.sentence).toBe("SwanStation has a BIOS image for every region");
  });

  // The answer DuckStation (Standalone) really sends over an empty BIOS folder:
  // no row, read as not read, a verdict of not met, and a search that states its
  // folder but no count.
  const emptyFolder = {
    active_core_label: "DuckStation (Standalone)",
    emulator_read: false,
    locating: "by-name-then-content" as const,
    emulator_declaration: "packaged" as const,
    console_needs_image: true,
    refused: [],
    emulator_notes: [foundNothing(null)],
    content_search: null,
    required_count: 0,
    required_downloaded: 0,
    required_withheld: 0,
  };

  it("states the console's need and then the search, where the emulator names no image", () => {
    expect(biosSummary(emptyFolder, [], "missing", "PlayStation")).toEqual({
      status: "Needs a BIOS image",
      sentence:
        "PlayStation does not start without a BIOS image — DuckStation (Standalone) found no BIOS image in bios/",
    });
  });

  it("never counts zero images the emulator knows", () => {
    expect(biosSummary({ ...emptyFolder, emulator_notes: [] }, [], "missing", "PlayStation").sentence).toBe(
      "PlayStation does not start without a BIOS image",
    );
  });

  it.each(["missing", "ok"] as const)(
    "words a verdict the resolver stated over an emulator read as not read (%s)",
    (level) => {
      expect(biosSummary(emptyFolder, [], level, "PlayStation").sentence).not.toContain("Nothing could be established");
    },
  );

  it("states the console's need first and the search after it, where both apply", () => {
    const words = biosSummary(
      {
        active_core_label: "SwanStation",
        locating: "by-name-then-content",
        console_needs_image: true,
        emulator_notes: [foundNothing(2)],
      },
      FIVE_IMAGES,
      "missing",
      "PlayStation",
    );
    expect(words).toEqual({
      status: "Needs a BIOS image",
      sentence:
        "PlayStation does not start without a BIOS image, and none of the five SwanStation knows is in place — " +
        "bios/ holds 2 files of a size SwanStation accepts, and it recognises none of them",
    });
  });

  // Each way of finding firmware with an emulator the resolver answers it for,
  // and a file that emulator declares; the refusal is the code's input alone.
  it.each([
    ["by-name", "melonDS", "bios7.bin", "opens by name"],
    ["unestablished", "gpSP", "gba_bios.bin", "lists as required"],
    ["by-name-then-content", "DuckStation", "scph5501.bin", "requires"],
  ] as const)(
    "words a file nothing could check and a refused one with the count's own verb (%s)",
    (locating, label, file, verb) => {
      const named = { active_core_label: label, locating };
      expect(biosSummary({ ...named, required_withheld: 1 }, [], "unknown").sentence).toBe(
        `One file ${label} ${verb} could not be checked`,
      );
      const refused = [{ declared: `../${file}`, required: true, reason: "firmware-path-escapes-root" }];
      expect(biosSummary({ ...named, refused }, [], "unknown").sentence).toBe(
        `${label} ${verb} ../${file}, which lies outside the BIOS folder — so whether it is ready could not be established`,
      );
    },
  );

  it("never says a searching emulator opens by name where the resolver stated nothing of its search", () => {
    const words = biosSummary(
      { active_core_label: "DuckStation", locating: "by-name-then-content", required_count: 1, required_downloaded: 0 },
      [required("scph5501.bin", false)],
      "missing",
    );
    expect(words.sentence).not.toContain("opens by name");
    expect(words.sentence).toBe("The one file DuckStation requires is not in place");
  });

  it("words neither sentence over a search note that names no folder", () => {
    // The backend makes no such note — an empty setting with no folder (xemu's)
    // is no search — so one that arrives states nothing the panel can word.
    const note = { ...foundNothing(0), dir: null };
    const words = biosSummary(
      { ...searcher("xemu", 0), emulator_notes: [note] },
      [required("mcpx_1.0.bin", false)],
      "missing",
    );
    expect(words.sentence).not.toContain("found no BIOS image");
    expect(words.sentence).not.toContain("of a size");
    expect(words.status).not.toBe("No BIOS chosen");
  });

  it("words a search that states no count by its folder alone", () => {
    // DuckStation's search: the folder, and no count of candidates.
    expect(
      biosSummary({ ...searchOnly(0, "DuckStation"), emulator_notes: [foundNothing(null)] }, [], "unknown"),
    ).toEqual({
      status: "No BIOS image found",
      sentence: "DuckStation found no BIOS image in bios/",
    });
  });

  // The folder is named by the last segment of the resolver's `dir`, however
  // many slashes end it or sit inside it; a `dir` with no segment at all names
  // the BIOS folder instead.
  it.each([
    ["/home/deck/retrodeck/bios", "bios/"],
    ["/home/deck/retrodeck/bios/", "bios/"],
    ["/home/deck/retrodeck/bios///", "bios/"],
    ["/home/deck//retrodeck//bios", "bios/"],
    ["bios", "bios/"],
    ["/", "the BIOS folder"],
    ["", "the BIOS folder"],
  ])("names the folder searched at %j as %s", (dir, folder) => {
    const source = { ...searchOnly(0, "DuckStation"), emulator_notes: [{ ...foundNothing(null), dir }] };
    expect(biosSummary(source, [], "unknown").sentence).toBe(`DuckStation found no BIOS image in ${folder}`);
  });

  it.each([
    ["incomplete", [], "DuckStation's BIOS folder could not be read completely"],
    [
      "unreadable",
      [{ ...required("scph5501.bin", null), checked: "unread" as const }],
      "scph5501.bin could not be read",
    ],
  ] as const)("words a search that found nothing by what it found: %s", (search, rows, sentence) => {
    expect(
      biosSummary(
        { active_core_label: "DuckStation", locating: "by-name-then-content", content_search: search },
        rows,
        "unknown",
      ),
    ).toEqual({ status: "Readiness unknown", sentence });
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

  describe("an emulator listed under two labels", () => {
    // RetroDECK lists pcsx2_libretro.so as LRPS2 and as PCSX2. The selector
    // names it "LRPS2 / PCSX2" whichever label was picked, and so does every
    // sentence about it — never the one picked label alone.
    const twoLabels = {
      active_core_label: "PCSX2",
      emulator_tabs: [
        { emulator: "pcsx2_libretro.so", label: "LRPS2 / PCSX2", launching: true, requirements_met: true },
      ],
    };

    it("names it by both in the headline", () => {
      expect(biosSummary({ ...twoLabels, required_count: 2, required_downloaded: 2 }, [], "ok").sentence).toBe(
        "All 2 files LRPS2 / PCSX2 requires are in place",
      );
    });

    it("names it by both in a tie between two images", () => {
      const notes = [{ kind: "images-tied" as const, setting: null, tied: 2, regions: ["pal"] }];
      const words = biosSummary(
        { ...twoLabels, emulator_notes: notes, required_count: 1, required_downloaded: 1 },
        [],
        "ok",
      );
      expect(words.sentence).toContain("which one LRPS2 / PCSX2 uses is not established");
    });

    it("names it by both over each one-of group", () => {
      const group: OneOfGroupVerdict = {
        state: "met",
        covered: ["ntsc-u"],
        missing: [],
        unchecked: [],
        game_regions: [],
        regions: ["ntsc-u"],
        options: [],
      };
      expect(groupBlocks({ ...twoLabels, one_of_groups: [group] })[0]?.heading).toBe(
        "LRPS2 / PCSX2 · one image per disc region",
      );
    });

    it("takes the picked label where the payload carries no entries", () => {
      expect(biosSummary({ active_core_label: "PCSX2", required_count: 0 }, [], "ok").sentence).toBe(
        "PCSX2 marks none of its BIOS files as required",
      );
    });
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
      sentence: "1 of 2 files Example Emulator requires are in place",
    });
  });

  it("decides readiness by the count comparison where the payload carries no level", () => {
    expect(summary({ required_count: 2, required_downloaded: 2 }, null).sentence).toBe(
      "All 2 files Example Emulator requires are in place",
    );
    expect(summary({ required_count: 2, required_downloaded: 1 }, null).sentence).toBe(
      "1 of 2 files Example Emulator requires are in place",
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
 * **The searched set is swept, never listed** (`test-utils/componentSources.ts`,
 * which says which set and why).
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

  it("says the emulator has a BIOS image for every region", () => {
    const met = summary({
      required_count: 1,
      required_downloaded: 1,
      one_of_groups: [group("met", ["ntsc-j", "ntsc-u", "pal"], [])],
    });
    expect(met).toEqual({
      status: "3 / 3 regions",
      sentence: "Example Emulator has a BIOS image for every region",
    });
  });

  it("names the regions a partly covered group serves and the ones whose discs will not start", () => {
    const partial = biosSummary(
      {
        active_core_label: "Beetle PSX",
        required_count: 1,
        required_downloaded: 0,
        one_of_groups: [group("partial", ["ntsc-u"], ["ntsc-j", "pal"])],
      } as BiosSummarySource,
      [],
      "partial",
    );
    expect(partial).toEqual({
      status: "1 / 3 regions · USA only",
      sentence: "Beetle PSX has a BIOS image for USA only — Japan and Europe discs will not start",
    });
  });

  // A group carrying options for Japan and USA (or Japan alone), and nothing for
  // the rest of its route's regions — the resolver's `unstated`.
  const unstated = (
    state: OneOfGroupVerdict["state"],
    covered: string[],
    missing: string[],
    unstatedRegions: string[],
    game_regions: string[] = [],
  ): OneOfGroupVerdict => ({
    ...group(state, covered, missing, game_regions),
    unchecked: unstatedRegions,
    unstated: unstatedRegions,
    regions: [...covered, ...missing, ...unstatedRegions],
  });
  const beetle = (one: OneOfGroupVerdict, level: BiosLevel) =>
    biosSummary(
      {
        active_core_label: "Beetle PSX",
        required_count: 1,
        required_downloaded: 0,
        // The payload counts an unknown group among the requirements nothing could judge.
        required_withheld: one.state === "unknown" ? 1 : 0,
        one_of_groups: [one],
      },
      [],
      level,
    );

  it("names the regions a group states nothing for, beside the ones it covers", () => {
    expect(beetle(unstated("unknown", ["ntsc-j"], [], ["ntsc-u", "pal"]), "unknown")).toEqual({
      status: "1 / 3 regions · regions not checked",
      sentence:
        "Beetle PSX has a BIOS image for Japan — for USA and Europe discs Beetle PSX states no BIOS image, " +
        "so whether they start could not be established",
    });
  });

  it("names them after a partly covered group's own sentence", () => {
    expect(beetle(unstated("partial", ["ntsc-j"], ["pal"], ["ntsc-u"]), "unknown")).toEqual({
      status: "1 / 3 regions · Japan only",
      sentence:
        "Beetle PSX has a BIOS image for Japan only — Europe discs will not start — for USA discs Beetle PSX " +
        "states no BIOS image, so whether they start could not be established",
    });
  });

  it("names them after the unchecked sentence where an image could not be read as well", () => {
    const open = { ...unstated("unknown", [], [], ["pal"]), unchecked: ["ntsc-j", "pal"] };
    expect(beetle(open, "unknown")).toEqual({
      status: "Readiness unknown",
      sentence:
        "Whether the BIOS image Beetle PSX needs is in place could not be checked — for Europe discs Beetle PSX " +
        "states no BIOS image, so whether they start could not be established",
    });
  });

  it("keeps the unchecked sentence where a region it covers nothing for was not checked either", () => {
    // Japan in place; USA unchecked by a search nobody read the bytes of;
    // Europe stated nothing for. Only Europe is named after the sentence.
    const open = { ...unstated("unknown", ["ntsc-j"], [], ["pal"]), unchecked: ["ntsc-u", "pal"] };
    expect(beetle({ ...open, regions: ["ntsc-j", "ntsc-u", "pal"] }, "unknown")).toEqual({
      status: "Readiness unknown",
      sentence:
        "Whether the BIOS image Beetle PSX needs is in place could not be checked — for Europe discs Beetle PSX " +
        "states no BIOS image, so whether they start could not be established",
    });
  });

  it("names them after an unmet group's sentence", () => {
    expect(beetle(unstated("unmet", [], ["ntsc-j", "ntsc-u"], ["pal"]), "missing")).toEqual({
      status: "0 / 3 regions",
      sentence:
        "Beetle PSX cannot start this system without a BIOS image — for Europe discs Beetle PSX states no BIOS " +
        "image, so whether they start could not be established",
    });
  });

  it("leaves them to the game page's own sentence there", () => {
    const words = beetle(unstated("unknown", [], [], ["pal"], ["pal"]), "unknown");
    expect(words.sentence).not.toContain("so whether they start");
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
    expect(unmet).toEqual({
      status: "0 / 3 regions",
      sentence: "Beetle PSX cannot start this system without a BIOS image",
    });
  });

  it("never counts a region nobody checked as covered", () => {
    const unread = (state: OneOfGroupVerdict["state"], covered: string[]): OneOfGroupVerdict => ({
      ...group(state, covered, ["pal"]),
      unchecked: ["ntsc-j"],
      regions: ["ntsc-j", ...covered, "pal"],
    });
    const statusOf = (verdict: OneOfGroupVerdict) =>
      summary({ required_count: 1, required_downloaded: 0, one_of_groups: [verdict] }, "partial").status;

    expect(statusOf(unread("partial", ["ntsc-u"]))).toBe("1 / 3 regions · USA only");
    expect(statusOf(unread("unmet", []))).toBe("0 / 2 regions");
  });

  it("gives a group nothing could fully check no count even where some regions are covered", () => {
    // A plain file is missing, so the plain files word the line. The group
    // beside them has one region covered and two nobody checked: "1 / 3
    // regions" would read like a partial group, so it gets no count at all.
    const both = summary(
      {
        required_count: 2,
        required_downloaded: 0,
        required_withheld: 1,
        one_of_groups: [
          { ...group("unknown", ["north"], []), unchecked: ["south", "east"], regions: ["north", "south", "east"] },
        ],
      },
      "unknown",
    );
    expect(both).toEqual({
      status: "0 / 1 required · regions not checked",
      sentence: "The one file Example Emulator requires is not in place",
    });
  });

  it("says a core has an image for every region however many images it takes", () => {
    // A core may open one image for Japan and another for USA and Europe.
    const several: OneOfGroupVerdict = {
      ...group("met", ["ntsc-j", "ntsc-u", "pal"], []),
      options: [
        { file_name: "scph5500.bin", regions: ["ntsc-j"], satisfied: true },
        { file_name: "scph1001.bin", regions: ["ntsc-u", "pal"], satisfied: true },
      ],
    };
    const invented: OneOfGroupVerdict = { ...group("met", ["north", "south"], []), regions: ["north", "south"] };

    expect(summary({ required_count: 1, required_downloaded: 1, one_of_groups: [several] })).toEqual({
      status: "3 / 3 regions",
      sentence: "Example Emulator has a BIOS image for every region",
    });
    expect(
      biosSummary(
        { active_core_label: "Arcadia", required_count: 1, required_downloaded: 1, one_of_groups: [invented] },
        [],
        "ok",
      ),
    ).toEqual({ status: "2 / 2 regions", sentence: "Arcadia has a BIOS image for every region" });
  });

  it("counts the plain required files apart from the group's regions", () => {
    const both = summary(
      {
        required_count: 3,
        required_downloaded: 2,
        one_of_groups: [group("partial", ["ntsc-u"], ["ntsc-j", "pal"])],
      } as BiosSummarySource,
      "partial",
    );
    expect(both.status).toBe("2 / 2 required · 1 / 3 regions · USA only");
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
      "Example Emulator starts this game with scph1001.bin — found in the BIOS folder, it serves every region",
    );
  });

  it("does not say the image was found in the folder where nothing says a search found it", () => {
    // Every region's own setting can name one file; then the core opened it by
    // name and no search was made.
    const rows: BiosSummaryRow[] = [{ file_name: "scph1001.bin", caveats: [] }];
    const met = summary({ required_count: 1, required_downloaded: 1, one_of_groups: [swanstation] }, "ok", rows);
    expect(met.sentence).toBe("Example Emulator starts this game with scph1001.bin, which serves every region");
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
      "Example Emulator needs scph5500.bin, scph5501.bin or scph5502.bin to start this game (Japan, USA and Europe) — none of them is in place",
    );
  });

  it("never lets a named file stand for a region that has no image at all", () => {
    // USA has no option; only Europe's file can be named, and only for Europe.
    const mixed: OneOfGroupVerdict = {
      ...group("unmet", [], ["ntsc-u", "pal"], ["ntsc-u", "pal"]),
      options: [{ file_name: "scph5502.bin", regions: ["pal"], satisfied: false }],
    };
    expect(summary({ required_count: 1, required_downloaded: 0, one_of_groups: [mixed] }, "missing").sentence).toBe(
      "Example Emulator needs scph5502.bin to start this game (Europe) — it is missing, and there is no BIOS image for USA",
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
      status: "1 / 3 regions · NORTH only",
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
    // The plain file alone is counted, in the status and in the sentence.
    expect(both).toEqual({
      status: "0 / 1 required · 1 / 1 regions",
      sentence: "The one file Example Emulator requires is not in place",
    });
  });

  it("leaves the headline to a required file nothing could judge beside a partial group", () => {
    const both = summary(
      {
        required_count: 2,
        required_downloaded: 0,
        required_withheld: 1,
        one_of_groups: [group("partial", ["ntsc-u"], ["pal"])],
      } as BiosSummarySource,
      "unknown",
    );
    expect(both.sentence).toBe("One file Example Emulator requires could not be checked");
  });

  it("words a missing plain file beside an unknown group as the missing file it is", () => {
    // The withheld count here is the group alone; the plain file was checked
    // and is absent, so "could not be checked" would be untrue of it.
    const both = summary(
      {
        required_count: 2,
        required_downloaded: 0,
        required_withheld: 1,
        one_of_groups: [{ ...group("unknown", [], []), unchecked: ["ntsc-u"], regions: ["ntsc-u"] }],
      },
      "unknown",
    );
    expect(both).toEqual({
      status: "0 / 1 required · regions not checked",
      sentence: "The one file Example Emulator requires is not in place",
    });
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
    expect(both.sentence).toBe("One file Example Emulator requires could not be checked");
  });

  it("keeps an unmet group's sentence beside a missing plain file", () => {
    const both = summary(
      { required_count: 2, required_downloaded: 0, one_of_groups: [group("unmet", [], ["ntsc-u"])] },
      "missing",
    );
    expect(both.sentence).toBe("Example Emulator cannot start this system without a BIOS image");
  });

  it("speaks for the group where one of its images is in place, whatever the console needs", () => {
    const both = summary(
      {
        console_needs_image: true,
        required_count: 1,
        required_downloaded: 1,
        one_of_groups: [group("met", ["ntsc-u"], [])],
      },
      "ok",
      [{ file_name: "scph5501.bin", used_by_active: true, wanted: "optional", satisfied: true }],
    );
    expect(both.sentence).toBe("Example Emulator has a BIOS image for every region");
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

  it("searches desktop/ and shared/ as well as bigpicture/", () => {
    const paths = componentSources().map((entry) => entry.path);
    expect(paths.filter((path) => path.startsWith("desktop/"))).not.toEqual([]);
    expect(paths.filter((path) => path.startsWith("shared/"))).not.toEqual([]);
  });
});
