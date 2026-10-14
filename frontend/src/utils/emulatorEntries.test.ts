import { describe, expect, it } from "vitest";
import { libretroEmu, sharedBy } from "../test-utils/coreFixtures";
import { emulatorName, entryForLabel } from "./emulatorEntries";

describe("emulatorEntries", () => {
  const shared = sharedBy(libretroEmu("pcsx2_libretro", "LRPS2"), ["LRPS2", "PCSX2"]);
  const emulators = [libretroEmu("pcsx2_hw_libretro", "PCSX2 HW"), shared];

  it("finds the entry a label stored under any of its labels reads as", () => {
    expect(entryForLabel(emulators, "LRPS2")).toBe(shared);
    expect(entryForLabel(emulators, "PCSX2")).toBe(shared);
  });

  it("finds no entry for a null label or one no entry lists", () => {
    expect(entryForLabel(emulators, null)).toBeUndefined();
    expect(entryForLabel(emulators, "Play!")).toBeUndefined();
  });

  it("names a label by its entry, and a label no entry lists by itself", () => {
    expect(emulatorName(emulators, "PCSX2")).toBe("LRPS2 / PCSX2");
    expect(emulatorName(emulators, "Play!")).toBe("Play!");
  });
});
