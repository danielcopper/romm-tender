import { describe, it, expect, beforeEach, vi } from "vitest";
import type { ArtworkSlotRow } from "../api/backend";
import { slotThumbnailUrls } from "./artworkThumbs";

const row = (overrides: Partial<ArtworkSlotRow>): ArtworkSlotRow => ({
  slot: "hero",
  state: "automatic",
  source: "tender",
  image_id: null,
  has_choice: false,
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
    expect(slotThumbnailUrls(5000, row({}), "7")).toEqual([
      "https://steamloopback.host/customimages/5000_hero.jpg?v=9&t=7.tender",
      "https://steamloopback.host/customimages/5000_hero.png?v=9&t=7.tender",
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
    expect(slotThumbnailUrls(5000, row({ slot: "icon", image_id: 31 }), "7")).toEqual([
      "https://steamloopback.host/customimages/5000_icon.png?t=7.31",
    ]);
  });

  it("names no file for an icon set in Steam, which may lie anywhere", () => {
    expect(slotThumbnailUrls(5000, row({ slot: "icon", state: "set_in_steam", source: "steam" }))).toEqual([]);
  });

  it("names no file for an icon Tender has not set", () => {
    expect(slotThumbnailUrls(5000, row({ slot: "icon", source: "empty" }))).toEqual([]);
  });

  it("loads the icon anew once another image is in it, though Steam's own value has not moved", () => {
    const before = slotThumbnailUrls(5000, row({ slot: "icon", image_id: 31 }), "7");
    const after = slotThumbnailUrls(5000, row({ slot: "icon", image_id: 32 }), "7");
    expect(after).not.toEqual(before);
  });

  it("loads a slot anew after a write, also where it names the same image and Steam's value has not moved", () => {
    const before = slotThumbnailUrls(5000, row({ slot: "hero", source: "romm" }), "7");
    const after = slotThumbnailUrls(5000, row({ slot: "hero", source: "romm" }), "8");
    expect(after).not.toEqual(before);
  });
});
