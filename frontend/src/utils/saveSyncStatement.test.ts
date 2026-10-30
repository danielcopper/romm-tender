import { describe, it, expect } from "vitest";
import { NO_SAVE_YET, noSaveLine, notDownloadedStatement, refusesDownloadedGame } from "./saveSyncStatement";
import type { SaveSyncAvailability } from "../types";

const REFUSAL = "a refusal's sentence, as the backend words it";

const answer = (overrides: Partial<SaveSyncAvailability> = {}): SaveSyncAvailability => ({
  available: true,
  installed: true,
  emulator_label: "mGBA",
  ...overrides,
});

const refused = (overrides: Partial<SaveSyncAvailability> = {}): SaveSyncAvailability =>
  answer({ available: false, reason: "save_shape_unsupported", message: REFUSAL, ...overrides });

describe("noSaveLine (#2149 D3)", () => {
  it("names the entry the game starts with where its saves sync", () => {
    expect(noSaveLine(answer())).toBe("No save yet — mGBA's saves for this game sync with RomM once it writes one.");
  });

  it("names no emulator where the game starts with no entry", () => {
    expect(noSaveLine(answer({ emulator_label: null }))).toBe(
      "No save yet — this game's saves sync with RomM once its emulator writes one.",
    );
  });

  it("claims nothing about syncing while no answer is held, or for a game that is not downloaded", () => {
    expect(noSaveLine(null)).toBe(NO_SAVE_YET);
    expect(noSaveLine(answer({ installed: false }))).toBe(NO_SAVE_YET);
    expect(noSaveLine(refused())).toBe(NO_SAVE_YET);
  });
});

describe("notDownloadedStatement (#2149 D4)", () => {
  it("names no emulator where the game starts with no entry", () => {
    expect(notDownloadedStatement(answer({ installed: false, emulator_label: null }))).toEqual({
      line: "Not downloaded. Once it is, its saves sync with RomM.",
      refusal: null,
    });
  });

  it("carries the refusal's sentence as the backend sent it", () => {
    expect(notDownloadedStatement(refused({ installed: false }))).toEqual({
      line: "Not downloaded. Once it is, its saves will not sync:",
      refusal: REFUSAL,
    });
  });

  it("says nothing for a downloaded game or while no answer is held", () => {
    expect(notDownloadedStatement(answer())).toBeNull();
    expect(notDownloadedStatement(null)).toBeNull();
  });
});

describe("refusesDownloadedGame (#2149 D1, D5)", () => {
  it("holds only for a refusal of a game that is downloaded", () => {
    expect(refusesDownloadedGame(refused())).toBe(true);
    expect(refusesDownloadedGame(refused({ installed: false }))).toBe(false);
    expect(refusesDownloadedGame(answer())).toBe(false);
    expect(refusesDownloadedGame(null)).toBe(false);
  });
});
