import { describe, it, expect } from "vitest";
import { GROUP_TONE_COLOR, biosColorForLevel } from "./biosColor";

describe("biosColorForLevel", () => {
  it("maps 'ok' to green", () => {
    expect(biosColorForLevel("ok")).toBe("#5ba32b");
  });

  it("maps 'partial' to amber", () => {
    expect(biosColorForLevel("partial")).toBe("#d4a72c");
  });

  it("maps 'missing' to red", () => {
    expect(biosColorForLevel("missing")).toBe("#d94126");
  });

  it("maps 'unknown' (no emulator's answer established) to neutral grey", () => {
    expect(biosColorForLevel("unknown")).toBe("#8f98a0");
  });

  it("maps null (no level data) to neutral grey", () => {
    expect(biosColorForLevel(null)).toBe("#8f98a0");
  });
});

describe("GROUP_TONE_COLOR", () => {
  it("maps group line tones to green, red, and neutral grey", () => {
    expect(GROUP_TONE_COLOR).toEqual({
      here: "#5ba32b",
      missing: "#d94126",
      other: "#8f98a0",
      unchecked: "#8f98a0",
    });
  });
});
