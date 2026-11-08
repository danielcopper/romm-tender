import { describe, it, expect } from "vitest";
import { NO_DOWNLOAD_ANSWERS, downloadAnswers, downloadTakesNoPress } from "./downloadPress";

describe("downloadTakesNoPress", () => {
  const free = { offline: false, pending: false, boundVanished: false, noFileOnRomm: false };

  it("takes a press when nothing stands in its way", () => {
    expect(downloadTakesNoPress(free)).toBe(false);
  });

  it.each(["offline", "pending", "boundVanished", "noFileOnRomm"] as const)(
    "takes none where %s alone holds",
    (key) => {
      expect(downloadTakesNoPress({ ...free, [key]: true })).toBe(true);
    },
  );
});

describe("downloadAnswers", () => {
  it("reads an empty detail as nothing on disk and a file on RomM", () => {
    expect(downloadAnswers({})).toEqual(NO_DOWNLOAD_ANSWERS);
    expect(NO_DOWNLOAD_ANSWERS).toEqual({ targetOccupied: false, candidatePresent: false, noFileOnRomm: false });
  });

  it("reads what sits at the game's location", () => {
    expect(downloadAnswers({ target_path_occupied: true })).toEqual({
      ...NO_DOWNLOAD_ANSWERS,
      targetOccupied: true,
    });
  });

  it("reads a candidate under another name", () => {
    expect(downloadAnswers({ adoption_candidate_present: true })).toEqual({
      ...NO_DOWNLOAD_ANSWERS,
      candidatePresent: true,
    });
  });

  it("reads no file on RomM only where the detail says RomM holds none", () => {
    expect(downloadAnswers({ has_file_on_disk: false })).toEqual({
      ...NO_DOWNLOAD_ANSWERS,
      noFileOnRomm: true,
    });
    expect(downloadAnswers({ has_file_on_disk: true }).noFileOnRomm).toBe(false);
  });
});
