import { describe, it, expect } from "vitest";
import { biosHeldRatio, HELD_RATIO_PHRASE } from "./biosHeldRatio";
import { componentSources } from "../test-utils/componentSources";
import type { BiosStatus, FirmwarePlatformExt } from "../types";

describe("biosHeldRatio", () => {
  it("names the set beside the numbers — how much of what the library holds is at its destination", () => {
    expect(biosHeldRatio({ server_count: 3, local_count: 1 })).toBe(" (1/3 RomM library files)");
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
    expect(biosHeldRatio(gamePage)).toBe(" (1/20 RomM library files)");
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
    // `(0/0 RomM library files)` is a ratio over a set that does not exist.
    expect(biosHeldRatio({ server_count: 0, local_count: 0 })).toBe("");
  });
});

/**
 * The same drift lock `biosSummary.test.ts` puts on the seven sentences, over
 * the one tail the surfaces append to them.
 *
 * The template stood in two components at once, which is the state this module
 * removed; nothing but a search of the sources can say it has not come back. The
 * phrase comes from the module the answer is built from, so a wording change
 * moves the lock with it rather than leaving a second copy of it here.
 *
 * **It sweeps the same set its twin does** (`test-utils/componentSources.ts`)
 * rather than naming the components, for the reason written there: both locks
 * once named two while three components rendered a BIOS state. Only the sentence
 * half actually drifted — the Platforms list has never written a ratio — so the
 * sweep holds this half before the fact rather than repairing it after, which is
 * the only thing a lock can do for a surface that has not gone wrong yet.
 *
 * It reads the components as TEXT, so a comment quoting the ratio fails it too —
 * deliberately: the words belong to the module, and so does the reasoning about
 * them. And like its twin it can only see this phrase copied: a component
 * inventing its own way to write the library's ratio is invisible to it, as it
 * would be to any string search.
 */
describe("no surface writes the ratio itself", () => {
  it.each(componentSources())("$path carries no spelling of its own", ({ source }) => {
    expect(source).not.toContain(HELD_RATIO_PHRASE);
  });

  it("searches for a run that names the set and holds no part of the numbers", () => {
    // The two properties the phrase needs: that it names the set, which is what
    // makes the run searchable at all, and that neither the numbers nor the
    // brackets they sit in are part of it. Why each one, at `HELD_RATIO_PHRASE`.
    expect(HELD_RATIO_PHRASE).toContain("RomM library");
    expect(HELD_RATIO_PHRASE).not.toMatch(/[\d()]/);
  });
});
