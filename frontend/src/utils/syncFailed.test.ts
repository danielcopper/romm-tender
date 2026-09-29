import { describe, it, expect } from "vitest";
import { syncFailedMessage } from "./syncFailed";

describe("syncFailedMessage", () => {
  it("puts a bare message behind the prefix", () => {
    expect(syncFailedMessage("Server unreachable — check your URL")).toBe(
      "Sync failed — Server unreachable — check your URL",
    );
  });

  it("leaves a message that already carries the prefix as it is", () => {
    expect(syncFailedMessage("Sync failed — Authentication failed")).toBe("Sync failed — Authentication failed");
  });

  it("says the sync failed where the frame gave no words", () => {
    expect(syncFailedMessage("")).toBe("Sync failed.");
    expect(syncFailedMessage(undefined)).toBe("Sync failed.");
  });
});
