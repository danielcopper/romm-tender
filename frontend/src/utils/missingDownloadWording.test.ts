import { describe, it, expect } from "vitest";
import {
  DOWNLOAD_AGAIN_LABEL,
  FORGET_DOWNLOAD_LABEL,
  FORGETTING_LABEL,
  FORGET_FAILED_TOAST,
  downloadForgottenToast,
  fileMissingNote,
} from "./missingDownloadWording";

describe("missingDownloadWording", () => {
  it("names the recorded path in the note", () => {
    expect(fileMissingNote("/run/media/deck/SD/roms/snes/Game.sfc")).toBe(
      "File missing at /run/media/deck/SD/roms/snes/Game.sfc",
    );
  });

  it("labels the two actions the way the decision words them", () => {
    expect(DOWNLOAD_AGAIN_LABEL).toBe("Download again");
    expect(FORGET_DOWNLOAD_LABEL).toBe("Forget this download");
    expect(FORGETTING_LABEL).toBe("Forgetting...");
  });

  it("names the game in the forgotten toast", () => {
    expect(downloadForgottenToast("Chrono Trigger")).toBe("Chrono Trigger: download forgotten");
  });

  it("falls back to ROM when the name is not known yet", () => {
    expect(downloadForgottenToast("")).toBe("ROM: download forgotten");
  });

  it("says a failed forget in its own words", () => {
    expect(FORGET_FAILED_TOAST).toBe("Couldn't forget the download");
  });
});
