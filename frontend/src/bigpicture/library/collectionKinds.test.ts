// The Collections tab's rules, pinned where no component stands around them:
// which collection is the Favorites row, which kind lists what under the owner
// switch, what a kind row counts, and what the pane's sentence promises.

import { describe, it, expect } from "vitest";
import type { CollectionSyncSetting } from "../../types";
import {
  KIND_ORDER,
  freezeOrder,
  hiddenForeignCount,
  inSteamLabel,
  kindMembers,
  kindSentence,
  onCount,
  ownerLabel,
  resolveFavorites,
  romCountLabel,
  searchMembers,
  wireKind,
} from "./collectionKinds";

function coll(overrides: Partial<CollectionSyncSetting> = {}): CollectionSyncSetting {
  return {
    id: "1",
    name: "Couch co-op",
    rom_count: 10,
    sync_enabled: false,
    kind: "standard",
    is_favorite: false,
    is_own: true,
    ...overrides,
  };
}

const ids = (list: CollectionSyncSetting[]) => list.map((c) => `${c.kind}:${c.id}`);

describe("resolveFavorites", () => {
  it("takes the signed-in user's own favorites collection as the row", () => {
    const mine = coll({ id: "f", is_favorite: true });
    expect(resolveFavorites([coll(), mine])).toEqual({ state: "one", collection: mine });
  });

  it("leaves another user's public favorites collection out of the row", () => {
    const theirs = coll({ id: "g", is_favorite: true, is_own: false, owner_username: "mara" });
    expect(resolveFavorites([theirs])).toEqual({ state: "none" });
    const mine = coll({ id: "f", is_favorite: true });
    expect(resolveFavorites([theirs, mine])).toEqual({ state: "one", collection: mine });
  });

  it("names none where more than one counts as yours, including before the user's id is known", () => {
    // Before the id is known the backend marks every collection own, so another
    // user's favorites collection reads as a second one of yours.
    expect(resolveFavorites([coll({ id: "a", is_favorite: true }), coll({ id: "b", is_favorite: true })])).toEqual({
      state: "several",
      count: 2,
    });
    const unmarked = { ...coll({ id: "c", is_favorite: true }) };
    delete unmarked.is_own;
    expect(resolveFavorites([unmarked, coll({ id: "d", is_favorite: true })]).state).toBe("several");
  });

  it("is a smart or virtual collection's never", () => {
    expect(resolveFavorites([coll({ kind: "smart", is_favorite: true })])).toEqual({ state: "none" });
  });
});

describe("kindMembers", () => {
  const favorite = coll({ id: "f", is_favorite: true, name: "Favourites" });
  const theirFavorite = coll({ id: "g", is_favorite: true, is_own: false, owner_username: "mara" });
  const mine = coll({ id: "1" });
  const theirs = coll({ id: "2", is_own: false, owner_username: "jonas" });
  const smart = coll({ id: "1", kind: "smart" });
  const franchise = coll({ id: "fr", kind: "virtual", virtual_type: "franchise" });
  const igdb = coll({ id: "ig", kind: "virtual", virtual_type: "collection" });
  const all = [favorite, theirFavorite, mine, theirs, smart, franchise, igdb];

  it("lists the favorites row's collection under Collections nowhere, and another user's favorites as ordinary", () => {
    const answer = resolveFavorites(all);
    expect(ids(kindMembers(all, "standard", "all", answer))).toEqual(["standard:g", "standard:1", "standard:2"]);
  });

  it("lists unresolved favorites collections under Collections, each with its own switch", () => {
    const second = coll({ id: "h", is_favorite: true });
    const list = [favorite, second, mine];
    expect(ids(kindMembers(list, "standard", "all", resolveFavorites(list)))).toEqual([
      "standard:f",
      "standard:h",
      "standard:1",
    ]);
  });

  it("hides another user's collections with the owner switch off, on the two kinds that have owners", () => {
    const answer = resolveFavorites(all);
    expect(ids(kindMembers(all, "standard", "own", answer))).toEqual(["standard:1"]);
    const theirSmart = coll({ id: "9", kind: "smart", is_own: false });
    expect(ids(kindMembers([smart, theirSmart], "smart", "own", { state: "none" }))).toEqual(["smart:1"]);
  });

  it("splits the virtual kind by type, and the owner switch never touches either half", () => {
    const answer = resolveFavorites(all);
    expect(ids(kindMembers(all, "franchise", "own", answer))).toEqual(["virtual:fr"]);
    expect(ids(kindMembers(all, "igdb", "own", answer))).toEqual(["virtual:ig"]);
  });

  it("gives Favorites no table", () => {
    expect(kindMembers(all, "favorites", "all", resolveFavorites(all))).toEqual([]);
  });

  it("counts what the owner switch is hiding", () => {
    const answer = resolveFavorites(all);
    expect(hiddenForeignCount(all, "standard", "own", answer)).toBe(2);
    expect(hiddenForeignCount(all, "standard", "all", answer)).toBe(0);
    expect(hiddenForeignCount(all, "franchise", "own", answer)).toBe(0);
  });
});

describe("onCount", () => {
  it("counts only what the kind's table lists — a hidden foreign collection stored as on is not one", () => {
    const list = [
      coll({ id: "1", sync_enabled: true }),
      coll({ id: "2", sync_enabled: true, is_own: false }),
      coll({ id: "3", sync_enabled: false }),
    ];
    expect(onCount(kindMembers(list, "standard", "own", { state: "none" }))).toBe("1 of 2 on");
    expect(onCount(kindMembers(list, "standard", "all", { state: "none" }))).toBe("2 of 3 on");
  });
});

describe("freezeOrder", () => {
  it("puts those that are on above those that are off, keeping the listing's order within each", () => {
    const list = [
      coll({ id: "a", sync_enabled: false }),
      coll({ id: "b", sync_enabled: true }),
      coll({ id: "c", sync_enabled: false }),
      coll({ id: "d", sync_enabled: true }),
    ];
    expect(freezeOrder(list).map((c) => c.id)).toEqual(["b", "d", "a", "c"]);
  });
});

describe("searchMembers", () => {
  it("keeps every collection on an empty search and matches loosely otherwise", () => {
    const list = [coll({ id: "1", name: "Final Fantasy" }), coll({ id: "2", name: "Mega Man" })];
    expect(searchMembers(list, "")).toHaveLength(2);
    expect(searchMembers(list, "ffy").map((c) => c.id)).toEqual(["1"]);
  });
});

describe("the columns' words", () => {
  it("names the owner: you, their RomM user name, or a dash where the listing carried none", () => {
    expect(ownerLabel(coll())).toBe("you");
    const unmarked = { ...coll() };
    delete unmarked.is_own;
    expect(ownerLabel(unmarked)).toBe("you");
    expect(ownerLabel(coll({ is_own: false, owner_username: "mara" }))).toBe("mara");
    expect(ownerLabel(coll({ is_own: false, owner_username: null }))).toBe("—");
  });

  it("reads an absent In Steam count as unknown, never as zero", () => {
    expect(inSteamLabel(coll())).toBe("—");
    expect(inSteamLabel(coll({ in_steam_count: 0 }))).toBe("0");
    expect(inSteamLabel(coll({ in_steam_count: 7 }))).toBe("7");
  });

  it("counts ROMs in the singular and the plural", () => {
    expect(romCountLabel(1)).toBe("1 ROM");
    expect(romCountLabel(14)).toBe("14 ROMs");
  });
});

describe("kindSentence", () => {
  it("says on every kind that the games come from platforms that are not synced too", () => {
    for (const kind of KIND_ORDER) {
      expect(kindSentence(kind)).toMatch(/platforms you do not sync|synced or not/);
    }
  });

  it("quotes the suffix the naming setting adds today, per kind", () => {
    // The table the sync writes is `backend/domain/collection_label.py`'s; a
    // favorites collection is a standard one there.
    expect(kindSentence("favorites", "Favourites")).toContain("[Favourites (Standard)]");
    expect(kindSentence("standard")).toContain("[name (Standard)]");
    expect(kindSentence("smart")).toContain("[name (Smart)]");
    expect(kindSentence("franchise")).toContain("[name (Franchise)]");
    expect(kindSentence("igdb")).toContain("[name (IGDB Collection)]");
  });
});

describe("wireKind", () => {
  it("sends both autogenerated kinds to the one virtual bucket", () => {
    expect(wireKind("standard")).toBe("standard");
    expect(wireKind("smart")).toBe("smart");
    expect(wireKind("franchise")).toBe("virtual");
    expect(wireKind("igdb")).toBe("virtual");
  });
});
