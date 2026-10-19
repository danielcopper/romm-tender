import { describe, expect, it } from "vitest";
import type { EmulatorTab } from "../types/firmware";
import { isUnclaimedLibraryRow, selectedTab, stepTab, tabDotColor, tabIndex, tabLabel, tabRows } from "./biosTabs";

const launching: EmulatorTab = { emulator: "a.so", label: "A", launching: true, requirements_met: true };
const other: EmulatorTab = {
  emulator: "b.so",
  label: "B",
  launching: false,
  requirements_met: false,
  rows: [
    {
      file_name: "b.bin",
      downloaded: false,
      local_path: "",
      description: "",
      wanted: "needed",
      required_by_active: true,
    },
  ],
};

describe("which emulator's entry a BIOS table shows", () => {
  it("opens on the launching emulator's entry", () => {
    expect(selectedTab([launching, other], null)).toBe(launching);
  });

  it("keeps a chosen emulator while its entry exists, and falls back to the launching one when it is gone", () => {
    expect(selectedTab([launching, other], "b.so")).toBe(other);
    expect(selectedTab([launching], "b.so")).toBe(launching);
  });

  it("shows the launching emulator's own rows, never an unclaimed library file or another emulator's", () => {
    const files = [
      { file_name: "a.bin", declared_by: "a.so", wanted: "needed" as const },
      { file_name: "lib.bin", declared_by: null, wanted: "not_needed" as const },
      { file_name: "b.bin", declared_by: "b.so", wanted: "optional" as const },
    ];
    expect(tabRows(files, [launching, other], launching).map((row) => row.file_name)).toEqual(["a.bin"]);
  });

  it("shows another emulator's own rows when it is chosen", () => {
    expect(tabRows([], [launching, other], other).map((row) => row.file_name)).toEqual(["b.bin"]);
  });

  it("shows every declared row where the payload carries no entries", () => {
    const files = [
      { file_name: "a.bin", declared_by: "b.so", wanted: "needed" as const },
      { file_name: "lib.bin", declared_by: null, wanted: "unknown" as const },
    ];
    expect(tabRows(files, undefined, null).map((row) => row.file_name)).toEqual(["a.bin"]);
  });

  it("names a library file no emulator here declares, by its need being the reading's", () => {
    expect(isUnclaimedLibraryRow({ declared_by: null, wanted: "not_needed" })).toBe(true);
    expect(isUnclaimedLibraryRow({ declared_by: null, wanted: "unknown" })).toBe(true);
    expect(isUnclaimedLibraryRow({ declared_by: "a.so", wanted: "optional" })).toBe(false);
    expect(isUnclaimedLibraryRow({ declared_by: null, wanted: "needed" })).toBe(false);
  });

  it("dots an entry with its emulator's own verdict", () => {
    expect([true, false, null].map(tabDotColor)).toEqual(["#5ba32b", "#d94126", "#8f98a0"]);
  });
});

describe("stepping through the emulators", () => {
  const third: EmulatorTab = { emulator: "c.so", label: "C", launching: false, requirements_met: null };
  const tabs = [launching, other, third];

  it("steps to the next and the previous emulator", () => {
    expect(stepTab(tabs, other, 1)).toBe(third);
    expect(stepTab(tabs, other, -1)).toBe(launching);
  });

  it("wraps around at either end", () => {
    expect(stepTab(tabs, third, 1)).toBe(launching);
    expect(stepTab(tabs, launching, -1)).toBe(third);
  });

  it("finds an entry in a fresh answer by its emulator, not by the object it was", () => {
    const fresh = tabs.map((tab) => ({ ...tab }));
    expect(tabIndex(fresh, other)).toBe(1);
    expect(stepTab(fresh, other, 1)).toBe(fresh[2]);
  });
});

describe("what an entry is called", () => {
  it("is its label, else its identity, and never a made-up name", () => {
    expect(tabLabel(other)).toBe("B");
    expect(tabLabel({ ...other, label: null, emulator: "kronos_libretro.so" })).toBe("kronos");
    expect(tabLabel({ ...other, label: null, emulator: null })).toBeNull();
  });
});
