import { describe, it, expect } from "vitest";
import type { BiosFileStatus } from "../types";
import { fileDotColor, rowBelongsOnThisPage } from "./biosGamePageRows";

const GREEN = "#5ba32b";
const RED = "#d94126";
const GREY = "#8f98a0";

/** A declared file that is not required, not an option, not met and not
 *  fetchable: the baseline the cases below change. `one_of` is `null`, not
 *  absent, because the backend sends the key on every row. */
const row = (over: Partial<BiosFileStatus> = {}): BiosFileStatus => ({
  file_name: "bios.bin",
  downloaded: false,
  local_path: "",
  declared_path: "bios.bin",
  description: "",
  wanted: "optional",
  required_by_active: false,
  on_server: false,
  declared_kind: "file",
  satisfied: false,
  one_of: null,
  ...over,
});

/** A row from a payload that carries no verdict at all. */
const withoutVerdict = (over: Partial<BiosFileStatus> = {}): BiosFileStatus => {
  const file = row(over);
  delete file.satisfied;
  return file;
};

const NO_REGIONS: ReadonlySet<string> = new Set();

describe("fileDotColor", () => {
  it("is green for a met verdict", () => {
    expect(fileDotColor(row({ satisfied: true, required_by_active: true }), NO_REGIONS)).toBe(GREEN);
  });

  it("is grey where nothing established the verdict", () => {
    expect(fileDotColor(row({ satisfied: null, required_by_active: true }), NO_REGIONS)).toBe(GREY);
  });

  it("is red for an unmet file the launch requires", () => {
    expect(fileDotColor(row({ required_by_active: true }), NO_REGIONS)).toBe(RED);
  });

  it("is red for an unmet option serving a region the group's verdict names missing", () => {
    const option = row({ one_of: { regions: ["ntsc-j", "pal"], every_region: false } });
    expect(fileDotColor(option, new Set(["pal"]))).toBe(RED);
  });

  it("is grey for an unmet option whose regions are not missing", () => {
    const option = row({ one_of: { regions: ["ntsc-j"], every_region: false } });
    expect(fileDotColor(option, new Set(["pal"]))).toBe(GREY);
  });

  it("is grey for an unmet file the launch does not require", () => {
    expect(fileDotColor(row(), new Set(["pal"]))).toBe(GREY);
  });

  it("reads `downloaded` where the payload carries no verdict", () => {
    expect(fileDotColor(withoutVerdict({ downloaded: true }), NO_REGIONS)).toBe(GREEN);
    expect(fileDotColor(withoutVerdict({ downloaded: false, required_by_active: true }), NO_REGIONS)).toBe(RED);
  });
});

describe("rowBelongsOnThisPage", () => {
  it("keeps a file the launch requires", () => {
    expect(rowBelongsOnThisPage(row({ required_by_active: true }))).toBe(true);
  });

  it("keeps an option of the launching emulator's one-of group", () => {
    expect(rowBelongsOnThisPage(row({ one_of: { regions: ["ntsc-u"], every_region: false } }))).toBe(true);
  });

  it("keeps a file whose verdict is met", () => {
    expect(rowBelongsOnThisPage(row({ satisfied: true }))).toBe(true);
  });

  it("keeps a file nothing could judge", () => {
    expect(rowBelongsOnThisPage(row({ satisfied: null }))).toBe(true);
  });

  it("keeps a file the platform page can fetch", () => {
    expect(rowBelongsOnThisPage(row({ on_server: true }))).toBe(true);
  });

  it("leaves out an unmet file that is not required, not an option and not fetchable", () => {
    expect(rowBelongsOnThisPage(row())).toBe(false);
  });

  it("leaves out a row the library holds that the platform page offers no button for", () => {
    expect(rowBelongsOnThisPage(row({ on_server: true, downloaded: true }))).toBe(false);
  });

  it("reads `downloaded` where the payload carries no verdict", () => {
    expect(rowBelongsOnThisPage(withoutVerdict({ downloaded: true }))).toBe(true);
    expect(rowBelongsOnThisPage(withoutVerdict({ downloaded: false }))).toBe(false);
  });
});
