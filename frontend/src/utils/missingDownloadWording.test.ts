import { describe, it, expect } from "vitest";
import {
  DOWNLOAD_AGAIN_LABEL,
  FORGET_DOWNLOAD_LABEL,
  FORGETTING_LABEL,
  FORGET_FAILED_TOAST,
  downloadForgottenToast,
  fileMissingNote,
  forgetRefusedToast,
} from "./missingDownloadWording";

describe("missingDownloadWording", () => {
  it("names the recorded path in the note", () => {
    expect(fileMissingNote("/run/media/deck/SD/roms/snes/Game.sfc")).toBe(
      "File missing at /run/media/deck/SD/roms/snes/Game.sfc",
    );
  });

  it("labels the two actions", () => {
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

  it("points a refusal over a file that is there at playing it", () => {
    expect(
      forgetRefusedToast({
        reason: "file_present",
        message: "The recorded download exists: /sd/g.z64",
        path: "/sd/g.z64",
      }),
    ).toBe("The file is back at /sd/g.z64. Reopen the game page to play.");
  });

  it("says the file is back only for the file_present refusal", () => {
    expect(forgetRefusedToast({ reason: "unknown", message: "Failed to forget the download", path: "/sd/g.z64" })).toBe(
      "Failed to forget the download",
    );
  });

  it("passes any other refusal's own message through", () => {
    expect(forgetRefusedToast({ reason: "blocked_by_migration", message: "Pending RetroDECK migration." })).toBe(
      "Pending RetroDECK migration.",
    );
  });

  it("falls back to its own words when a refusal carries no message", () => {
    expect(forgetRefusedToast({ reason: "unknown", message: "" })).toBe("Couldn't forget the download");
  });
});
