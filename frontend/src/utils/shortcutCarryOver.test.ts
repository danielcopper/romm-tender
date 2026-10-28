import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import * as backend from "../api/backend";
import type { ArtworkSlotRow, ArtworkSlots, ArtworkTabSlot } from "../api/backend";
import { carryOverShortcutState, isConvergedRemoval, type ConvergedRemoval } from "./shortcutCarryOver";

vi.mock("./artworkThumbs", () => ({
  slotThumbnailUrls: (appId: number, row: ArtworkSlotRow) => [`https://steamloopback.host/${appId}/${row.slot}`],
}));

const REMOVAL: ConvergedRemoval = { rom_id: 31, app_id: 6031, kept_rom_id: 30, kept_app_id: 6030 };

function collection(id: string, displayName: string, appIds: number[]) {
  const apps = new Set(appIds);
  const added: number[] = [];
  return {
    id,
    displayName,
    apps,
    allApps: [],
    added,
    AsDragDropCollection: () => ({
      AddApps: (overviews: { appid: number }[]) => added.push(...overviews.map((overview) => overview.appid)),
      RemoveApps: vi.fn(),
    }),
    Save: vi.fn().mockResolvedValue(undefined),
    Delete: vi.fn(),
  };
}

function slots(states: Partial<Record<ArtworkTabSlot, ArtworkSlotRow["state"]>>): ArtworkSlots {
  const all: ArtworkTabSlot[] = ["cover", "grid", "hero", "logo", "icon"];
  return {
    success: true,
    has_api_key: true,
    game: { sgdb_id: null, source: null, name: null },
    slots: all.map((slot) => ({
      slot,
      state: states[slot] ?? "automatic",
      source: states[slot] === "set_in_steam" ? "steam" : "romm",
      image_id: null,
      has_choice: false,
      signature: "",
    })),
  };
}

function answerSlots(removed: ArtworkSlots, kept: ArtworkSlots) {
  vi.mocked(backend.getArtworkSlots).mockImplementation(async (_romId: number, appId: number) =>
    appId === REMOVAL.app_id ? removed : kept,
  );
}

let collections: ReturnType<typeof collection>[];
let hidden: Set<number>;
const setArtwork = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  vi.mocked(backend.getArtworkSlots).mockReset();
  answerSlots(slots({}), slots({}));
  setArtwork.mockClear();
  collections = [];
  hidden = new Set();
  vi.stubGlobal("collectionStore", {
    get userCollections() {
      return collections;
    },
    BIsHidden: (appId: number) => hidden.has(appId),
    SetAppsAsHidden: vi.fn((appIds: number[], value: boolean) => {
      for (const appId of appIds) if (value) hidden.add(appId);
    }),
  });
  vi.stubGlobal("appStore", {
    GetAppOverviewByAppID: (appId: number) => ({ appid: appId, display_name: "", strDisplayName: "" }),
    allApps: [],
  });
  vi.stubGlobal("SteamClient", { Apps: { SetCustomArtworkForApp: setArtwork } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => ({
      ok: true,
      blob: async () => new Blob([url], { type: url.endsWith("/hero") ? "image/jpeg" : "image/png" }),
    })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("isConvergedRemoval", () => {
  it("tells a converged removal from a plain stale one", () => {
    expect(isConvergedRemoval(REMOVAL)).toBe(true);
    expect(isConvergedRemoval({ rom_id: 31, app_id: 6031 })).toBe(false);
  });
});

describe("carryOverShortcutState (#2645)", () => {
  it("puts the kept shortcut into the user's collections the removed one is in, Favorites among them", async () => {
    const favorites = collection("favorite", "Favorites", [6031]);
    const golf = collection("uc-1", "Golf games", [6031]);
    const other = collection("uc-2", "Other", [7000]);
    collections = [favorites, golf, other];

    await carryOverShortcutState(REMOVAL);

    expect([favorites.added, golf.added, other.added]).toEqual([[6030], [6030], []]);
    expect(favorites.Save).toHaveBeenCalledTimes(1);
    expect(other.Save).not.toHaveBeenCalled();
  });

  it("leaves Tender's own collections and one already holding the kept shortcut alone", async () => {
    const platform = collection("uc-3", "RomM: GBA (steamdeck)", [6031]);
    const both = collection("uc-4", "Golf games", [6031, 6030]);
    collections = [platform, both];

    await carryOverShortcutState(REMOVAL);

    expect([platform.added, both.added]).toEqual([[], []]);
  });

  it("hides the kept shortcut when the removed one was hidden", async () => {
    hidden.add(6031);

    await carryOverShortcutState(REMOVAL);

    expect(hidden.has(6030)).toBe(true);
  });

  it("leaves the kept shortcut shown when the removed one was not hidden", async () => {
    await carryOverShortcutState(REMOVAL);

    expect(collectionStore.SetAppsAsHidden).not.toHaveBeenCalled();
  });

  it("sets an image set in Steam on the kept shortcut where it has none set in Steam", async () => {
    answerSlots(
      slots({ grid: "set_in_steam", hero: "set_in_steam", logo: "set_in_steam" }),
      slots({ logo: "set_in_steam" }),
    );

    await carryOverShortcutState(REMOVAL);

    expect(setArtwork.mock.calls.map(([appId, , type, assetType]) => [appId, type, assetType])).toEqual([
      [6030, "png", 3],
      [6030, "jpg", 1],
    ]);
    expect(atob(setArtwork.mock.calls[0]![1] as string)).toBe("https://steamloopback.host/6031/grid");
  });

  it("carries no image Tender set, and no icon", async () => {
    answerSlots(slots({ icon: "set_in_steam", cover: "chosen" }), slots({}));

    await carryOverShortcutState(REMOVAL);

    expect(setArtwork).not.toHaveBeenCalled();
  });

  it("carries the rest when one part fails", async () => {
    collections = [
      {
        ...collection("uc-5", "Broken", [6031]),
        Save: vi.fn().mockRejectedValue(new Error("Steam refused")),
      },
    ];
    hidden.add(6031);
    answerSlots(slots({ grid: "set_in_steam" }), slots({}));

    await carryOverShortcutState(REMOVAL);

    expect(hidden.has(6030)).toBe(true);
    expect(setArtwork).toHaveBeenCalledTimes(1);
  });

  it("writes no image when the slots cannot be read", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue({ success: false, reason: "no_grid_dir", message: "no grid" });

    await carryOverShortcutState(REMOVAL);

    expect(setArtwork).not.toHaveBeenCalled();
  });
});
