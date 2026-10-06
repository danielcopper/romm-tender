import { describe, it, expect } from "vitest";
import {
  DOWNLOAD_AGAIN_LABEL,
  FORGET_DOWNLOAD_LABEL,
  FORGETTING_LABEL,
  FORGET_FAILED_TOAST,
  FILE_MISSING_LABEL,
  FORGET_CONFIRM_BUTTON,
  downloadForgottenToast,
  forgetConfirmDescription,
  forgetRefusedToast,
} from "./missingDownloadWording";

describe("missingDownloadWording", () => {
  it("says the file is missing, without a path, for the note and the menu's title", () => {
    expect(FILE_MISSING_LABEL).toBe("File missing");
  });

  it("names the game and the recorded path in the forget confirmation", () => {
    expect(forgetConfirmDescription("Chrono Trigger", "/run/media/deck/SD/roms/snes/Game.sfc")).toBe(
      "Forget the download of Chrono Trigger? Its file is missing at /run/media/deck/SD/roms/snes/Game.sfc.",
    );
  });

  it("asks about this game when the name is not known yet", () => {
    expect(forgetConfirmDescription("", "/sd/g.z64")).toBe(
      "Forget the download of this game? Its file is missing at /sd/g.z64.",
    );
  });

  it("labels the confirmation's forget button", () => {
    expect(FORGET_CONFIRM_BUTTON).toBe("Forget");
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
    expect(
      forgetRefusedToast({
        reason: "in_progress",
        message: "This ROM is already being uninstalled or forgotten",
        path: "/sd/g.z64",
      }),
    ).toBe("This ROM is already being uninstalled or forgotten");
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
