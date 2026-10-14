import { describe, expect, it } from "vitest";
import type { EmulatorTab } from "../types/firmware";
import { selectedTab, tabDotColor, tabRows } from "./biosTabs";

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

describe("which tab a BIOS table shows (#2190)", () => {
  it("opens on the launching emulator's tab", () => {
    expect(selectedTab([launching, other], null)).toBe(launching);
  });

  it("keeps a chosen tab while it exists, and falls back to the launching one when it is gone", () => {
    expect(selectedTab([launching, other], "b.so")).toBe(other);
    expect(selectedTab([launching], "b.so")).toBe(launching);
  });

  it("shows the launching emulator's own rows and the unclaimed ones on its tab, never another's", () => {
    const files = [
      { file_name: "a.bin", declared_by: "a.so" },
      { file_name: "lib.bin", declared_by: null },
      { file_name: "b.bin", declared_by: "b.so" },
    ];
    expect(tabRows(files, [launching, other], launching).map((row) => row.file_name)).toEqual(["a.bin", "lib.bin"]);
  });

  it("shows another emulator's own rows on its tab", () => {
    expect(tabRows([], [launching, other], other).map((row) => row.file_name)).toEqual(["b.bin"]);
  });

  it("shows every row where the payload carries no tabs", () => {
    const files = [{ file_name: "a.bin", declared_by: "b.so" }];
    expect(tabRows(files, undefined, null)).toBe(files);
  });

  it("dots a tab with its emulator's own verdict", () => {
    expect([true, false, null].map(tabDotColor)).toEqual(["#5ba32b", "#d94126", "#8f98a0"]);
  });
});
