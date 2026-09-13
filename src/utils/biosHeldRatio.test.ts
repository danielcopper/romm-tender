import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { biosHeldRatio, HELD_RATIO_PHRASE } from "./biosHeldRatio";
import type { BiosStatus, FirmwarePlatformExt } from "../types";

describe("biosHeldRatio", () => {
  it("states how much of what the library holds is on disk", () => {
    expect(biosHeldRatio({ server_count: 3, local_count: 1 })).toBe(" (1/3 files held)");
  });

  it("gives the game page's payload and the platform pane's the same answer", () => {
    // The two surfaces read two different payload types, and each type spells
    // the pair optionally. One function over the shape they share is what keeps
    // one platform from being described in two amounts; the types below are the
    // real ones, so a field renamed on either side fails here.
    const gamePage: BiosStatus = { needs_bios: true, server_count: 20, local_count: 1 };
    const platformPane: FirmwarePlatformExt = {
      platform_slug: "psx",
      files: [],
      server_count: 20,
      local_count: 1,
    };

    expect(biosHeldRatio(gamePage)).toBe(biosHeldRatio(platformPane));
    expect(biosHeldRatio(gamePage)).toBe(" (1/20 files held)");
  });

  it("says nothing where the payload carries neither count", () => {
    // One fallback for both surfaces, and it is the one that states nothing it
    // was not told: the pair is the only thing on the wire that says what the
    // library holds. Counting the rows instead would answer from a second copy
    // of the backend's rule — see the module header.
    const gamePage: BiosStatus = { needs_bios: true };
    const platformPane: FirmwarePlatformExt = { platform_slug: "psx", files: [] };

    expect(biosHeldRatio(gamePage)).toBe("");
    expect(biosHeldRatio(platformPane)).toBe("");
  });

  it("gives a library holding nothing for the platform no ratio at all", () => {
    // `(0/0 files held)` is a ratio over a set that does not exist.
    expect(biosHeldRatio({ server_count: 0, local_count: 0 })).toBe("");
  });
});

/**
 * The same drift lock `biosSummary.test.ts` puts on the seven sentences, over
 * the one tail both of those surfaces append to them.
 *
 * The template stood in both components at once, which is the state this module
 * removed; nothing but a search of the sources can say it has not come back. The
 * phrase comes from the module the answer is built from, so a wording change
 * moves the lock with it rather than leaving a second copy of it here.
 *
 * It reads the components as TEXT, so a comment quoting the ratio fails it too —
 * deliberately: the words belong to the module, and so does the reasoning about
 * them.
 */
describe("no surface writes the ratio itself", () => {
  const surfaces = ["../components/BiosTab.tsx", "../components/library/PlatformDetail.tsx"];

  it.each(surfaces)("%s carries no spelling of its own", (relative) => {
    const source = readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");
    expect(source).not.toContain(HELD_RATIO_PHRASE);
  });

  it("searches for something — an empty phrase would pass over anything", () => {
    expect(HELD_RATIO_PHRASE.length).toBeGreaterThan(8);
  });
});
