import { describe, expect, it } from "vitest";
import type { TenderPlaceRefusal } from "../types";
import {
  createMissingQuestion,
  driveFreeLine,
  driveRowLabel,
  elsewhereLine,
  folderConditionLine,
  forgetTitle,
  placeRefusalSentence,
  tenderNamedQuestion,
  useParentButton,
} from "./tenderFolderWording";

const DRIVE = { name: "SD", mount_point: "/run/media/deck/SD", folder: "/run/media/deck/SD/Tender", free_bytes: 2048 };

function refused(fields: Partial<TenderPlaceRefusal>): TenderPlaceRefusal {
  return { success: false, reason: "place_in_rom_folder", message: "the backend's sentence", ...fields };
}

describe("Tender folder wording", () => {
  it("names the folder and why it cannot be used", () => {
    expect(folderConditionLine("/sd/Tender", "drive_not_mounted")).toBe("/sd/Tender: the drive is not connected.");
    expect(folderConditionLine("/sd/Tender", "folder_missing")).toBe("/sd/Tender: the folder is missing.");
    expect(folderConditionLine("/sd/Tender", "folder_not_writable")).toBe(
      "/sd/Tender: the folder cannot be written to.",
    );
  });

  it("says where the settings point and where the files are recorded", () => {
    expect(elsewhereLine("/home/deck/Tender", "/sd/Tender")).toBe(
      "The settings name /home/deck/Tender as Tender's folder, but Tender's files are recorded under /sd/Tender.",
    );
  });

  it("asks before forgetting, naming the folder", () => {
    expect(forgetTitle("/sd/Tender")).toBe("Forget everything Tender has recorded under /sd/Tender?");
  });

  it("asks before creating the folders of a typed place that do not exist yet", () => {
    expect(createMissingQuestion("/home/deck/Games/Lib", "/home/deck", "Games/Lib")).toBe(
      "/home/deck/Games/Lib does not exist yet; only /home/deck does. Create Games/Lib/ there?",
    );
  });

  it("asks about a typed place whose last folder is named Tender", () => {
    expect(tenderNamedQuestion("/sd/Tender", "/sd")).toBe(
      "Tender keeps its files in a folder named Tender inside the place you choose, so this would make " +
        "/sd/Tender/Tender. Use /sd instead?",
    );
    expect(useParentButton("/sd")).toBe("Use /sd");
  });

  it("offers a drive by its name and folder, with its free space", () => {
    expect(driveRowLabel(DRIVE)).toBe("SD — /run/media/deck/SD/Tender");
    expect(driveFreeLine(DRIVE)).toBe("2.0 KB free");
  });

  describe("a refused choice", () => {
    const where = { path: "/r/roms/x/Tender", source: "retrodeck", folder: "/r/roms" };

    it("names the source of a ROM folder the place lies inside by Tender's name for it", () => {
      expect(placeRefusalSentence(refused({ ...where, system: null }))).toBe(
        "/r/roms/x/Tender lies inside RetroDECK's ROM folder (/r/roms). Choose a place outside it.",
      );
    });

    it("names the system of a system folder the place lies inside", () => {
      expect(placeRefusalSentence(refused({ ...where, folder: "/big/ps2", system: "ps2" }))).toBe(
        "/r/roms/x/Tender lies inside RetroDECK's folder for ps2 (/big/ps2). Choose a place outside it.",
      );
    });

    it("names the ROM folder a place would hold", () => {
      expect(placeRefusalSentence(refused({ ...where, reason: "place_above_rom_folder" }))).toBe(
        "RetroDECK's ROM folder (/r/roms) lies inside /r/roms/x/Tender. Choose a place that holds no ROM folder.",
      );
    });

    it("shows any other refusal as the backend worded it", () => {
      expect(
        placeRefusalSentence(refused({ reason: "place_drive_not_mounted", message: "The drive SD is not connected." })),
      ).toBe("The drive SD is not connected.");
    });
  });
});
