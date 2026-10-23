import { describe, it, expect } from "vitest";
import type { RomMetadata } from "../types";
import { formatReleaseDate } from "./formatters";
import { metadataRows } from "./metadataRows";

const FULL: RomMetadata = {
  summary: "A golf game.",
  genres: ["Sport", "Arcade"],
  companies: ["Camelot", "Nintendo"],
  first_release_date: 932_515_200,
  average_rating: 84.6,
  game_modes: ["Single player", "Multiplayer"],
  player_count: "1-4",
  cached_at: 0,
};

const EMPTY: RomMetadata = {
  summary: "",
  genres: [],
  companies: [],
  first_release_date: null,
  average_rating: null,
  game_modes: [],
  player_count: "",
  cached_at: 0,
};

describe("metadataRows", () => {
  it("answers every field of a full record in display order, the summary not among them", () => {
    expect(metadataRows(FULL, "N64")).toEqual([
      { key: "platform", label: "Platform", value: "N64" },
      { key: "companies", label: "Companies", value: "Camelot, Nintendo" },
      { key: "genres", label: "Genres", value: ["Sport", "Arcade"] },
      { key: "release-date", label: "Release Date", value: formatReleaseDate(FULL.first_release_date) },
      { key: "game-modes", label: "Game Modes", value: "Single player, Multiplayer" },
      { key: "players", label: "Players", value: "1-4" },
      { key: "rating", label: "Metadata rating", value: "85%" },
    ]);
  });

  it("answers the platform alone where there is no record", () => {
    expect(metadataRows(null, "N64")).toEqual([{ key: "platform", label: "Platform", value: "N64" }]);
  });

  it("answers nothing where there is neither a record nor a platform", () => {
    expect(metadataRows(null, "")).toEqual([]);
  });

  it("leaves out the platform when it has no name", () => {
    expect(metadataRows(FULL, "").map((row) => row.key)).not.toContain("platform");
  });

  it("leaves out every empty field", () => {
    expect(metadataRows(EMPTY, "")).toEqual([]);
  });

  it.each([0, -86_400])("leaves out a release date of %d", (first_release_date) => {
    expect(metadataRows({ ...FULL, first_release_date }, "").map((row) => row.key)).not.toContain("release-date");
  });

  it("leaves out a rating of 0", () => {
    expect(metadataRows({ ...FULL, average_rating: 0 }, "").map((row) => row.key)).not.toContain("rating");
  });

  it.each([
    [72.4, "72%"],
    [72.6, "73%"],
  ])("rounds a rating of %s to the nearest whole percent", (average_rating, value) => {
    expect(metadataRows({ ...EMPTY, average_rating }, "")).toEqual([
      { key: "rating", label: "Metadata rating", value },
    ]);
  });
});
