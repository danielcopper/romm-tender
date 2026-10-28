import { describe, expect, it } from "vitest";
import { libretroEmu } from "../test-utils/coreFixtures";
import { entryForLabel } from "./emulatorEntries";

describe("emulatorEntries", () => {
  const lrps2 = libretroEmu("pcsx2_libretro", "LRPS2");
  const pcsx2 = libretroEmu("pcsx2_libretro", "PCSX2");
  const emulators = [libretroEmu("pcsx2_hw_libretro", "PCSX2 HW"), lrps2, pcsx2];

  it("finds the one entry a label names, two labels that run one command being two entries", () => {
    expect(entryForLabel(emulators, "LRPS2")).toBe(lrps2);
    expect(entryForLabel(emulators, "PCSX2")).toBe(pcsx2);
  });

  it("finds no entry for a null label or one no entry lists", () => {
    expect(entryForLabel(emulators, null)).toBeUndefined();
    expect(entryForLabel(emulators, "Play!")).toBeUndefined();
  });
});
