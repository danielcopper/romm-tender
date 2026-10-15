import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  addAppToCollections,
  addToCollections,
  startCollectionsRun,
  createOrUpdateRomMCollections,
  createOrUpdateCollections,
  clearPlatformCollection,
  clearAllRomMCollections,
} from "./collections";
import { logError } from "../api/backend";
import { stubLiveCollectionStore } from "../test-utils/steamStubs";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  logError: vi.fn(),
  logInfo: vi.fn(),
  logWarn: vi.fn(),
}));

// Steam's collection identity is case-insensitive, so a collection whose
// displayName differs only by case from the one we're syncing is the SAME Steam
// collection. These tests pin that our create/find + cleanup match it
// case-insensitively — otherwise a colliding new create overwrites it and its
// games are lost (#1569). The getHostname mock (test-setup) resolves "test".

interface FakeDnd {
  RemoveApps: ReturnType<typeof vi.fn>;
  AddApps: ReturnType<typeof vi.fn>;
}
interface FakeCollection {
  id: string;
  displayName: string;
  allApps: unknown[];
  AsDragDropCollection: () => FakeDnd;
  Save: ReturnType<typeof vi.fn>;
  Delete: ReturnType<typeof vi.fn>;
  dnd: FakeDnd;
}

function fakeCollection(displayName: string): FakeCollection {
  const dnd: FakeDnd = { RemoveApps: vi.fn(), AddApps: vi.fn() };
  return {
    id: `id-${displayName}`,
    displayName,
    allApps: [],
    AsDragDropCollection: () => dnd,
    Save: vi.fn().mockResolvedValue(undefined),
    Delete: vi.fn().mockResolvedValue(undefined),
    dnd,
  };
}

describe("createOrUpdateRomMCollections — case-insensitive identity (#1569)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("updates an existing case-variant collection instead of creating a duplicate", async () => {
    // Steam already holds "RomM: [7 Up] (test)"; the reporter now emits the
    // folded-first-seen casing "7 up". Must UPDATE the existing one, not create a
    // colliding "7 up" that Steam merges over the top of.
    const existing = fakeCollection("RomM: [7 Up] (test)");
    const NewUnsavedCollection = vi.fn();
    vi.stubGlobal("collectionStore", { userCollections: [existing], NewUnsavedCollection });

    await createOrUpdateRomMCollections({ "7 up": [1001, 1002] });

    expect(NewUnsavedCollection).not.toHaveBeenCalled();
    expect(existing.Save).toHaveBeenCalledTimes(1);
    expect(existing.dnd.AddApps).toHaveBeenCalledTimes(1);
  });

  it("updates an existing collection whose name differs only by case folding (#2131)", async () => {
    const existing = fakeCollection("RomM: [STRASSE] (test)");
    const NewUnsavedCollection = vi.fn(() => fakeCollection("RomM: [Straße] (test)"));
    vi.stubGlobal("collectionStore", { userCollections: [existing], NewUnsavedCollection });

    await createOrUpdateRomMCollections({ Straße: [1001] });

    expect(NewUnsavedCollection).not.toHaveBeenCalled();
    expect(existing.Save).toHaveBeenCalledTimes(1);
  });

  it("creates a new collection when nothing matches even case-insensitively", async () => {
    // Non-vacuous complement: the find isn't matching everything — a genuinely
    // new name still creates.
    const created = fakeCollection("RomM: [brand new] (test)");
    const NewUnsavedCollection = vi.fn(() => created);
    vi.stubGlobal("collectionStore", { userCollections: [], NewUnsavedCollection });

    await createOrUpdateRomMCollections({ "brand new": [1] });

    expect(NewUnsavedCollection).toHaveBeenCalledTimes(1);
    expect(created.Save).toHaveBeenCalledTimes(1);
  });
});

describe("createOrUpdateCollections (platform) — case-insensitive identity (#1569)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("updates an existing case-variant platform collection instead of creating a duplicate", async () => {
    const existing = fakeCollection("RomM: Game Boy (test)");
    const NewUnsavedCollection = vi.fn();
    vi.stubGlobal("collectionStore", { userCollections: [existing], NewUnsavedCollection });

    await createOrUpdateCollections({ "game boy": [1001] });

    expect(NewUnsavedCollection).not.toHaveBeenCalled();
    expect(existing.Save).toHaveBeenCalledTimes(1);
  });

  it("does not begin another collection mutation after cancellation", async () => {
    let settle!: () => void;
    const first = fakeCollection("RomM: First (test)");
    const pendingSave = new Promise<void>((resolve) => {
      settle = resolve;
    });
    first.Save.mockReturnValueOnce(pendingSave);
    const second = fakeCollection("RomM: Second (test)");
    vi.stubGlobal("collectionStore", { userCollections: [first, second], NewUnsavedCollection: vi.fn() });
    const controller = new AbortController();
    const applying = createOrUpdateCollections({ First: [1], Second: [2] }, undefined, controller.signal);
    await Promise.resolve();

    controller.abort();
    settle();
    await applying;

    expect(first.Save).toHaveBeenCalledTimes(1);
    expect(second.dnd.AddApps).not.toHaveBeenCalled();
    expect(second.Save).not.toHaveBeenCalled();
  });
});

describe("clearPlatformCollection — case-insensitive identity (#1569)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("deletes a case-variant scoped collection", async () => {
    // We ask to clear "game boy"; the live collection is "RomM: Game Boy (test)".
    const scoped = fakeCollection("RomM: Game Boy (test)");
    vi.stubGlobal("collectionStore", { userCollections: [scoped] });

    await clearPlatformCollection("game boy");

    expect(scoped.Delete).toHaveBeenCalledTimes(1);
  });

  it("deletes a scoped and a legacy collection whose names differ only by case folding (#2131)", async () => {
    const scoped = fakeCollection("RomM: STRASSE (test)");
    const legacy = fakeCollection("RomM: STRASSE");
    const other = fakeCollection("RomM: Strasse Racer (test)");
    vi.stubGlobal("collectionStore", { userCollections: [scoped, legacy, other] });

    await clearPlatformCollection("Straße");

    expect(scoped.Delete).toHaveBeenCalledTimes(1);
    expect(legacy.Delete).toHaveBeenCalledTimes(1);
    expect(other.Delete).not.toHaveBeenCalled();
  });
});

describe("clearAllRomMCollections — case-insensitive identity (#1569)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("sweeps this host's case variants and spares another host's", async () => {
    const shouted = fakeCollection("ROMM: [Faves] (TEST)");
    const theirs = fakeCollection("ROMM: [Faves] (othermachine)");
    const mine = fakeCollection("My Games (test)");
    vi.stubGlobal("collectionStore", { userCollections: [shouted, theirs, mine] });

    await clearAllRomMCollections();

    expect(shouted.Delete).toHaveBeenCalledTimes(1);
    expect(theirs.Delete).not.toHaveBeenCalled();
    expect(mine.Delete).not.toHaveBeenCalled();
  });
});

describe("addAppToCollections — the add-only path a sync files each game through", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    startCollectionsRun("run-isolated");
  });

  it("creates a missing platform collection holding its first game", async () => {
    const store = stubLiveCollectionStore();

    await addAppToCollections(101, "PlayStation", [], "run-create");

    expect(store.NewUnsavedCollection).toHaveBeenCalledTimes(1);
    expect(store.NewUnsavedCollection).toHaveBeenCalledWith("RomM: PlayStation (test)", undefined, []);
    expect([...(store.named("RomM: PlayStation (test)")?.apps ?? [])]).toEqual([101]);
  });

  it("adds a second game to the collection the first one created, even before the store lists it", async () => {
    const store = stubLiveCollectionStore({ listsOnSave: false });

    await addAppToCollections(101, "PlayStation", [], "run-unlisted");
    await addAppToCollections(102, "PlayStation", [], "run-unlisted");

    expect(store.created.map((c) => [...c.apps])).toEqual([[101, 102]]);
  });

  it("forgets the collections it created when a new run starts", async () => {
    const store = stubLiveCollectionStore({ listsOnSave: false });

    await addAppToCollections(101, "PlayStation", [], "run-a");
    await addAppToCollections(102, "PlayStation", [], "run-b");

    expect(store.NewUnsavedCollection).toHaveBeenCalledTimes(2);
  });

  it("adds to an existing collection without removing what it holds", async () => {
    const store = stubLiveCollectionStore();
    const existing = store.make("RomM: PlayStation (test)", [1, 2]);
    store.userCollections.push(existing);

    await addAppToCollections(3, "PlayStation", [], "run-add");

    expect(existing.dnd.RemoveApps).not.toHaveBeenCalled();
    expect([...existing.apps]).toEqual([1, 2, 3]);
    expect(existing.Save).toHaveBeenCalledTimes(1);
    expect(store.NewUnsavedCollection).not.toHaveBeenCalled();
  });

  it("finds an existing collection whose name differs only by case", async () => {
    const store = stubLiveCollectionStore();
    const existing = store.make("RomM: [Favs] (TEST)", [1]);
    store.userCollections.push(existing);

    await addAppToCollections(2, null, ["favs"], "run-fold");

    expect(store.NewUnsavedCollection).not.toHaveBeenCalled();
    expect([...existing.apps]).toEqual([1, 2]);
  });

  it("writes nothing for a game the collection already holds", async () => {
    const store = stubLiveCollectionStore();
    const existing = store.make("RomM: PlayStation (test)", [7]);
    store.userCollections.push(existing);

    await addAppToCollections(7, "PlayStation", [], "run-member");

    expect(existing.dnd.AddApps).not.toHaveBeenCalled();
    expect(existing.Save).not.toHaveBeenCalled();
  });

  it("files a game into exactly the RomM collections named and no platform collection when none is named", async () => {
    const store = stubLiveCollectionStore();

    await addAppToCollections(55, null, ["Favs", "RPGs (Smart)"], "run-romm");

    expect(store.userCollections.map((c) => c.displayName)).toEqual([
      "RomM: [Favs] (test)",
      "RomM: [RPGs (Smart)] (test)",
    ]);
    expect(store.userCollections.every((c) => c.apps.has(55))).toBe(true);
  });

  it("logs a collection that fails and still files the game into the next one", async () => {
    const store = stubLiveCollectionStore();
    const broken = store.make("RomM: PlayStation (test)");
    broken.Save.mockRejectedValueOnce(new Error("cloud write failed"));
    store.userCollections.push(broken);

    await addAppToCollections(9, "PlayStation", ["Favs"], "run-fail");

    expect(vi.mocked(logError)).toHaveBeenCalledWith(
      'Failed to add appId 9 to collection "RomM: PlayStation (test)": Error: cloud write failed',
    );
    expect(store.named("RomM: [Favs] (test)")?.apps.has(9)).toBe(true);
  });

  it("does nothing when Steam's collection store is not there", async () => {
    vi.stubGlobal("collectionStore", undefined);

    await addAppToCollections(1, "PlayStation", ["Favs"], "run-none");

    expect(vi.mocked(logError)).not.toHaveBeenCalled();
  });
});

describe("addToCollections — a cancelled run's maps, added without removing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    startCollectionsRun("run-isolated");
  });

  it("a new run does not use the previous run's created collection", async () => {
    const store = stubLiveCollectionStore({ listsOnSave: false });
    await addAppToCollections(6000, "Nintendo 64", [], "run-before");

    startCollectionsRun("run-after");
    await addToCollections({ "Nintendo 64": [5000] }, {});

    expect(store.created.map((c) => [...c.apps])).toEqual([[6000], [5000]]);
  });

  it("adds each map's games to its collection, creating a missing one, and removes none", async () => {
    const store = stubLiveCollectionStore();
    const platform = store.make("RomM: Nintendo 64 (test)", [6000]);
    store.userCollections.push(platform);

    await addToCollections({ "Nintendo 64": [5000, 6000] }, { Favs: [5000] });

    expect([...platform.apps]).toEqual([6000, 5000]);
    expect(platform.dnd.RemoveApps).not.toHaveBeenCalled();
    expect([...(store.named("RomM: [Favs] (test)")?.apps ?? [])]).toEqual([5000]);
  });

  it("adds to a collection the run created that the store does not list yet, creating it no second time", async () => {
    const store = stubLiveCollectionStore({ listsOnSave: false });
    await addAppToCollections(6000, "Nintendo 64", [], "run-unlisted-end");

    await addToCollections({ "Nintendo 64": [5000] }, {});

    expect(store.created.map((c) => [...c.apps])).toEqual([[6000, 5000]]);
  });

  it("writes no further collection once the signal aborts", async () => {
    const store = stubLiveCollectionStore();
    const controller = new AbortController();
    controller.abort();

    await addToCollections({ "Nintendo 64": [5000] }, { Favs: [5000] }, controller.signal);

    expect(store.NewUnsavedCollection).not.toHaveBeenCalled();
  });
});
