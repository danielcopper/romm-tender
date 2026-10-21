import { describe, it, expect, beforeEach, vi } from "vitest";
import type { ArtworkSlotRow } from "../api/backend";
import { slotThumbnailUrls } from "./artworkThumbs";

const row = (overrides: Partial<ArtworkSlotRow>): ArtworkSlotRow => ({
  slot: "hero",
  state: "automatic",
  source: "tender",
  image_id: null,
  ...overrides,
});

describe("slotThumbnailUrls", () => {
  beforeEach(() => {
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({ appid: 5000, rt_custom_image_mtime: 9 })),
      GetCustomHeroImageURLs: vi.fn(() => ["/customimages/5000_hero.jpg?v=9", "/customimages/5000_hero.png?v=9"]),
      allApps: [],
    });
  });

  it("answers Steam's own list for the slot, on steamloopback.host, in Steam's order", () => {
    expect(slotThumbnailUrls(5000, row({}))).toEqual([
      "https://steamloopback.host/customimages/5000_hero.jpg?v=9",
      "https://steamloopback.host/customimages/5000_hero.png?v=9",
    ]);
  });

  it("answers nothing where Steam has no getter for the slot", () => {
    expect(slotThumbnailUrls(5000, row({ slot: "logo" }))).toEqual([]);
  });

  it("answers nothing for a shortcut Steam does not know", () => {
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(null);
    expect(slotThumbnailUrls(5000, row({}))).toEqual([]);
  });

  it("names Tender's icon file for an icon that is Tender's", () => {
    expect(slotThumbnailUrls(5000, row({ slot: "icon" }))).toEqual([
      "https://steamloopback.host/customimages/5000_icon.png?v=9",
    ]);
  });

  it("names no file for an icon set in Steam, which may lie anywhere", () => {
    expect(slotThumbnailUrls(5000, row({ slot: "icon", state: "set_in_steam", source: "steam" }))).toEqual([]);
  });

  it("names no file for an icon Tender has not set", () => {
    expect(slotThumbnailUrls(5000, row({ slot: "icon", source: "empty" }))).toEqual([]);
  });
});
