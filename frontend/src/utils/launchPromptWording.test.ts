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
    expect(unsyncedSavesDescription({ versionName: "USA", stranding: "unsynced", noSync: null })).toBe(
      "\"USA\" has saves that haven't been synced to RomM. They stay on disk, but won't sync until you switch back.",
    );
  });

  it("says why nothing is synced first while RomM is unreachable", () => {
    expect(unsyncedSavesDescription({ versionName: "USA", stranding: "unsynced", noSync: "unreachable" })).toBe(
      "\"USA\" has saves that haven't been synced to RomM. RomM is not reachable right now, so they can't be " +
        "synced first. They stay on disk, but won't sync until you switch back.",
    );
  });

  it("says Tender couldn't check where the check could not read", () => {
    expect(unsyncedSavesDescription({ versionName: "USA", stranding: "unchecked", noSync: null })).toBe(
      "Tender couldn't check whether \"USA\" has saves that haven't been synced to RomM. They stay on disk, but " +
        "won't sync until you switch back.",
    );
  });

  it("says the saves can't be uploaded where the version is gone from RomM", () => {
    expect(unsyncedSavesDescription({ versionName: "USA", stranding: "unsynced", noSync: "version_gone" })).toBe(
      '"USA" has saves that haven\'t been synced to RomM. "USA" is no longer available on RomM, so its saves ' +
        "can't be uploaded. They stay on disk.",
    );
  });
});
