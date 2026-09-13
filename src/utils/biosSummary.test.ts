import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BIOS_SUMMARY_PHRASES, biosSummary, type BiosSummaryRow, type BiosSummarySource } from "./biosSummary";
import type { BiosLevel } from "../types/firmware";

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
      { wanted: "optional", required_by_active: false, downloaded: false },
      { wanted: "optional", required_by_active: false, downloaded: false },
      { wanted: "optional", required_by_active: false, downloaded: true },
    ];
    expect(summary({ required_count: 2, required_downloaded: 2 }, "ok", rows).sentence).toBe(
      "All 2 files SwanStation requires are in place (2 optional missing)",
    );
  });

  it("says ONE file rather than `All 1 files`, and keeps the optional gap behind it", () => {
    // Reachable on the reference machine rather than a theoretical count:
    // DuckStation requires exactly one image on a stock RetroDECK.
    const rows: BiosSummaryRow[] = [{ wanted: "optional", required_by_active: false, downloaded: false }];
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
 * component "just this once". So the two surfaces are read as SOURCE and searched
 * for the phrases the module builds its answers from — which come from the module
 * itself, so a wording change moves the lock with it rather than leaving a second
 * copy of the list here to drift.
 *
 * What it can see is a summary string in a component. What it cannot see is a
 * component inventing a NEW wording for one of these states, which no string
 * search could catch.
 */
describe("no surface words a summary itself", () => {
  const surfaces = ["../components/BiosTab.tsx", "../components/library/PlatformDetail.tsx"];

  it.each(surfaces)("%s carries no summary phrase of its own", (relative) => {
    const source = readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");
    const found = BIOS_SUMMARY_PHRASES.filter((phrase) => source.includes(phrase));
    expect(found).toEqual([]);
  });

  it("searches for something — an empty phrase list would pass over anything", () => {
    expect(BIOS_SUMMARY_PHRASES.length).toBeGreaterThan(0);
    for (const phrase of BIOS_SUMMARY_PHRASES) expect(phrase.length).toBeGreaterThan(8);
  });
});
