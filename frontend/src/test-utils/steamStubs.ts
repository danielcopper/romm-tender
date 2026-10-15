// Per-test stubs for the Steam Deck globals `collectionStore` and `appStore`.
// Use in `beforeEach` to control the non-Steam app enumeration surface for
// components that read these globals (DataManagementPage, RomMPlaySection,
// RomMGameInfoPanel).
//
// The shapes here mirror the relevant slices of the ambient declarations in
// `frontend/src/types/steam.d.ts` — only the fields the consumers actually read
// need to be present, but `SteamAppOverview` requires `appid`, `display_name`,
// and `strDisplayName`, so `stubAppStore` accepts a permissive partial shape
// and fills in defaults at lookup time.

import { vi } from "vitest";

type OverviewLike = Partial<
  Pick<
    SteamAppOverview,
    "appid" | "display_name" | "strDisplayName" | "rt_last_time_played" | "minutes_playtime_forever"
  >
>;

export function stubCollectionStore(appIds: number[]): void {
  vi.stubGlobal("collectionStore", {
    deckDesktopApps: { apps: new Map(appIds.map((id) => [id, {}])) },
    userCollections: [],
  });
}

export function stubAppStore(overviews: Record<number, OverviewLike>): void {
  vi.stubGlobal("appStore", {
    GetAppOverviewByAppID: vi.fn((id: number): SteamAppOverview | null => {
      const o = overviews[id];
      if (!o) return null;
      // Fill in the required SteamAppOverview fields so callers that read
      // `appid` / `display_name` / `strDisplayName` see consistent shapes.
      return {
        appid: o.appid ?? id,
        display_name: o.display_name ?? "",
        strDisplayName: o.strDisplayName ?? "",
        ...(o.rt_last_time_played !== undefined ? { rt_last_time_played: o.rt_last_time_played } : {}),
        ...(o.minutes_playtime_forever !== undefined ? { minutes_playtime_forever: o.minutes_playtime_forever } : {}),
      };
    }),
    allApps: [],
  });
}

interface LiveDragDrop {
  AddApps: ReturnType<typeof vi.fn>;
  RemoveApps: ReturnType<typeof vi.fn>;
}

export interface LiveCollection {
  id: string;
  displayName: string;
  apps: Set<number>;
  allApps: unknown[];
  AsDragDropCollection: () => LiveDragDrop;
  Save: ReturnType<typeof vi.fn>;
  Delete: ReturnType<typeof vi.fn>;
  dnd: LiveDragDrop;
}

/**
 * A `collectionStore` whose collections hold the appIds their `AddApps` /
 * `RemoveApps` leave them with (read through `apps` and `allApps`), and whose `Save` lists a new collection in
 * `userCollections` — unless `listsOnSave` is off, which stands for Steam not
 * listing a just-saved collection yet.
 */
export function stubLiveCollectionStore(options: { listsOnSave?: boolean } = {}) {
  const listsOnSave = options.listsOnSave ?? true;
  const userCollections: LiveCollection[] = [];
  const make = (displayName: string, appIds: number[] = []): LiveCollection => {
    const apps = new Set(appIds);
    const dnd: LiveDragDrop = {
      AddApps: vi.fn((overviews: { appid: number }[]) => {
        for (const o of overviews) apps.add(o.appid);
      }),
      RemoveApps: vi.fn((overviews: { appid: number }[]) => {
        for (const o of overviews) apps.delete(o.appid);
      }),
    };
    const collection: LiveCollection = {
      id: `id-${displayName}`,
      displayName,
      apps,
      get allApps() {
        return [...apps].map((appid) => ({ appid }));
      },
      AsDragDropCollection: () => dnd,
      Save: vi.fn(() => {
        if (listsOnSave && !userCollections.includes(collection)) userCollections.push(collection);
        return Promise.resolve();
      }),
      Delete: vi.fn().mockResolvedValue(undefined),
      dnd,
    };
    return collection;
  };
  const created: LiveCollection[] = [];
  const NewUnsavedCollection = vi.fn((name: string) => {
    const collection = make(name);
    created.push(collection);
    return collection;
  });
  vi.stubGlobal("collectionStore", { userCollections, NewUnsavedCollection });
  const named = (displayName: string): LiveCollection | undefined =>
    userCollections.find((c) => c.displayName === displayName);
  return { userCollections, created, NewUnsavedCollection, make, named };
}
