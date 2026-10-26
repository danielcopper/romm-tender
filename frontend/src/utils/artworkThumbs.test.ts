import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import type { ArtworkSlotRow } from "../api/backend";
import { coverUrls, slotThumbnailUrls, steamsCover } from "./artworkThumbs";
import {
  _resetArtworkSlotsForTests,
  artworkThumbToken,
  loadArtworkSlots,
  noteArtworkWritten,
  writeSlotArtwork,
} from "./artworkSlotsStore";

const row = (overrides: Partial<ArtworkSlotRow>): ArtworkSlotRow => ({
  slot: "hero",
  state: "automatic",
  source: "tender",
  image_id: null,
  has_choice: false,
  signature: "sig",
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

  it("answers Steam's own list for the slot, on steamloopback.host, in Steam's order, signed in place of Steam's v=", () => {
    expect(slotThumbnailUrls(5000, row({}), "7")).toEqual([
      "https://steamloopback.host/customimages/5000_hero.jpg?s=sig&t=7",
      "https://steamloopback.host/customimages/5000_hero.png?s=sig&t=7",
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
      "https://steamloopback.host/customimages/5000_icon.png?s=sig&t=7",
    ]);
  });

  it("names no file for an icon set in Steam, which may lie anywhere", () => {
    expect(slotThumbnailUrls(5000, row({ slot: "icon", state: "set_in_steam", source: "steam" }))).toEqual([]);
  });

  it("names no file for an icon Tender has not set", () => {
    expect(slotThumbnailUrls(5000, row({ slot: "icon", source: "empty" }))).toEqual([]);
  });

  it("loads the icon anew once its token moved, though Steam's own value has not", () => {
    const before = slotThumbnailUrls(5000, row({ slot: "icon", image_id: 31 }), "7");
    const after = slotThumbnailUrls(5000, row({ slot: "icon", image_id: 31 }), "8");
    expect(after).not.toEqual(before);
  });

  it("loads a slot anew after a write, also where it names the same image and Steam's value has not moved", () => {
    const before = slotThumbnailUrls(5000, row({ slot: "hero", source: "romm" }), "7");
    const after = slotThumbnailUrls(5000, row({ slot: "hero", source: "romm" }), "8");
    expect(after).not.toEqual(before);
  });

  it("loads a slot anew once its file changed, though no write of Tender's moved its token", () => {
    const before = slotThumbnailUrls(5000, row({ signature: "a" }), "7");
    const after = slotThumbnailUrls(5000, row({ signature: "b" }), "7");
    expect(after).not.toEqual(before);
  });
});

describe("a write to one slot", () => {
  const SLOTS = ["cover", "grid", "hero", "logo"] as const;
  let steamsValue = 9;

  beforeEach(() => {
    _resetArtworkSlotsForTests();
    steamsValue = 9;
    // Steam's v= is one value per shortcut, moved for all four getters by every image set.
    const urls = (name: string) => () => [`/customimages/${name}.png?v=${steamsValue}`];
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({ appid: 5000 })),
      GetCustomVerticalCapsuleURLs: vi.fn(urls("5000p")),
      GetCustomLandcapeImageURLs: vi.fn(urls("5000")),
      GetCustomHeroImageURLs: vi.fn(urls("5000_hero")),
      GetCustomLogoImageURLs: vi.fn(urls("5000_logo")),
      allApps: [],
    });
  });

  const urlsOf = (signatures: Partial<Record<(typeof SLOTS)[number], string>> = {}) =>
    Object.fromEntries(
      SLOTS.map((slot) => [
        slot,
        slotThumbnailUrls(
          5000,
          row({ slot, signature: signatures[slot] ?? `${slot}-1` }),
          artworkThumbToken(5000, slot),
        ),
      ]),
    );

  it.each(["cover", "hero"] as const)(
    "of the %s changes only that row's URLs, though Steam moved its value for all",
    async (written) => {
      const before = urlsOf();

      await writeSlotArtwork(5000, written, () => {
        steamsValue = 10;
      });
      const after = urlsOf();

      for (const slot of SLOTS) {
        if (slot === written) expect(after[slot]).not.toEqual(before[slot]);
        else expect(after[slot]).toEqual(before[slot]);
      }
    },
  );

  it("set through Steam itself changes only that row's URLs once the read signs it anew", () => {
    const before = urlsOf();

    steamsValue = 10;
    const after = urlsOf({ logo: "logo-2" });

    for (const slot of SLOTS) {
      if (slot === "logo") expect(after[slot]).not.toEqual(before[slot]);
      else expect(after[slot]).toEqual(before[slot]);
    }
  });
});

describe("coverUrls", () => {
  beforeEach(() => {
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({ appid: 5000 })),
      GetCustomVerticalCapsuleURLs: vi.fn(() => ["/customimages/5000p.png?v=9"]),
      allApps: [],
    });
  });

  it("carries Steam's v= beside the cover row's signature and the token", () => {
    expect(coverUrls(5000, "7", "c1")).toEqual(["https://steamloopback.host/customimages/5000p.png?v=9&s=c1&t=7"]);
  });

  it("while the ARTWORK tab's cover row, from the same getter, carries no v=", () => {
    const row = {
      slot: "cover",
      state: "automatic",
      source: "romm",
      image_id: null,
      has_choice: false,
      signature: "c1",
    };
    expect(slotThumbnailUrls(5000, row as ArtworkSlotRow, "7")).toEqual([
      "https://steamloopback.host/customimages/5000p.png?s=c1&t=7",
    ]);
  });

  it("keeps Steam's v= where no cover row is held to sign it", () => {
    expect(coverUrls(5000, "7")).toEqual(["https://steamloopback.host/customimages/5000p.png?v=9&t=7"]);
  });
});

describe("steamsCover", () => {
  beforeEach(() => {
    _resetArtworkSlotsForTests();
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({ appid: 5000 })),
      GetCustomVerticalCapsuleURLs: vi.fn(() => ["/customimages/5000p.png?v=9"]),
      allApps: [],
    });
    vi.mocked(backend.getArtworkSlots).mockResolvedValue({
      success: true,
      has_api_key: true,
      game: { sgdb_id: 7, source: "romm", name: "Mario Golf" },
      slots: [row({ slot: "cover", source: "romm", signature: "c1" })],
    });
  });

  it("signs the cover by the held read of the ROM asked about, with that slot's token", async () => {
    await loadArtworkSlots(5000, 42);
    noteArtworkWritten(5000, ["cover"]);

    expect(steamsCover(5000, 42)).toEqual([
      `https://steamloopback.host/customimages/5000p.png?v=9&s=c1&t=${artworkThumbToken(5000, "cover")}`,
    ]);
  });

  it("is not signed by a read held for another ROM", async () => {
    await loadArtworkSlots(5000, 42);

    expect(steamsCover(5000, 43)).toEqual([
      `https://steamloopback.host/customimages/5000p.png?v=9&t=${artworkThumbToken(5000, "cover")}`,
    ]);
  });
});
