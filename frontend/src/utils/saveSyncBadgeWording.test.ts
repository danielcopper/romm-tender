import { describe, it, expect } from "vitest";
import { syncedLabel } from "./saveSyncBadgeWording";

describe("syncedLabel", () => {
  it("reads a moment ago in lower case after its verb", () => {
    expect(syncedLabel("Just now")).toBe("Synced just now");
  });

  it("reads a time ago after its verb", () => {
    expect(syncedLabel("3d ago")).toBe("Synced 3d ago");
  });
});
