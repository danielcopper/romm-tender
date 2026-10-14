import { describe, it, expect } from "vitest";
import { artworkApplyMessage } from "./artworkApplyMessage";

describe("artworkApplyMessage", () => {
  it.each([
    [4, "Artwork refreshed (4/4 images applied)"],
    [1, "Artwork refreshed (1/4 images applied)"],
    [0, "No artwork available for this game"],
    [-1, "Set a SteamGridDB API key in Settings first"],
  ])("says what %i images applied means", (applied, message) => {
    expect(artworkApplyMessage(applied)).toBe(message);
  });

  it("says a refusal in its own words", () => {
    expect(artworkApplyMessage({ success: false, reason: "auth_failed", message: "Invalid API key" })).toBe(
      "Invalid API key",
    );
  });
});
