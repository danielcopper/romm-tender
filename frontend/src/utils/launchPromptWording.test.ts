import { describe, it, expect } from "vitest";
import { fallbackLaunchDescription, unsyncedSavesDescription } from "./launchPromptWording";

describe("fallbackLaunchDescription", () => {
  it("asks over the failed sync's own message", () => {
    expect(fallbackLaunchDescription("Upload refused")).toBe("Upload refused — launch with local saves?");
  });

  it("asks after a message that already ends its sentence", () => {
    expect(fallbackLaunchDescription("Upload refused.")).toBe("Upload refused. Launch with local saves?");
  });

  it("says it in its own words when the message is missing or blank", () => {
    const own = "Couldn't sync saves with RomM server. Launch with local saves?";
    expect(fallbackLaunchDescription()).toBe(own);
    expect(fallbackLaunchDescription("")).toBe(own);
    expect(fallbackLaunchDescription("   ")).toBe(own);
  });
});

describe("unsyncedSavesDescription", () => {
  it("names the version and that its saves stay on disk", () => {
    expect(unsyncedSavesDescription("USA", true)).toBe(
      '"USA" has save changes that were never uploaded to RomM. They stay on disk, but won\'t sync until you ' +
        "switch back.",
    );
  });

  it("says why nothing is synced first while RomM is unreachable", () => {
    expect(unsyncedSavesDescription("USA", false)).toBe(
      '"USA" has save changes that were never uploaded, and RomM is not reachable right now — so they can\'t be ' +
        "synced first. They stay on disk, but won't sync until you switch back.",
    );
  });
});
