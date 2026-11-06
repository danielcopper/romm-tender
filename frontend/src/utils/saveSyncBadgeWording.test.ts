import { describe, it, expect } from "vitest";
import { NOT_SYNCED_LABEL, syncedLabel } from "./saveSyncBadgeWording";

describe("syncedLabel", () => {
  it("reads a moment ago in lower case after its verb", () => {
    expect(syncedLabel("Just now")).toBe("Synced just now");
  });

  it("reads a time ago after its verb", () => {
    expect(syncedLabel("3d ago")).toBe("Synced 3d ago");
  });

  it("claims no sync where none is recorded", () => {
    expect(syncedLabel(NOT_SYNCED_LABEL)).toBe(NOT_SYNCED_LABEL);
  });
});
