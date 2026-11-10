// Which open game pages a platform's change reaches (#2452 D16): a `bios` event
// reaches every page whose platform has the event's slug, because platforms that
// share a slug share their emulator's places; a `core_changed` event reaches only
// the pages of the platform whose pick changed.

import { describe, it, expect, vi, beforeEach } from "vitest";
import type { SetStateAction } from "react";
import * as backend from "../api/backend";
import { _resetSharedReadsForTests } from "../api/sharedReads";
import * as cachedStore from "../utils/cachedGameDetailStore";
import { wirePanelEvents, type PanelEventContext } from "./panelEvents";
import { loadData, type PanelState } from "./panelState";
import { announceBiosChange } from "./bios/useBiosActions";

vi.mock("../utils/cachedGameDetailStore", () => ({
  getCachedGameDetail: vi.fn(),
  invalidateCachedGameDetail: vi.fn(),
}));

const APP_ID = 5100;
const ROM_ID = 7;

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const seqs = () => ({ detail: 0, saveStatus: 0, slots: 0, slotTracking: 0, saveSync: 0, localSaves: 0, bios: 0 });

/** A game page's event lane for a game of *slug* on the platform RomM keeps in *folder*. */
function pageOn(slug: string, folder: string): { unwire: () => void } {
  let state = { romId: ROM_ID } as PanelState;
  const ctx: PanelEventContext = {
    appId: APP_ID,
    cancelled: () => false,
    romIdRef: { current: ROM_ID },
    platformSlugRef: { current: slug },
    platformFolderRef: { current: folder },
    slotsLoadedRef: { current: false },
    readSeqs: { current: seqs() },
    setState: (update: SetStateAction<PanelState>) => {
      state = typeof update === "function" ? update(state) : update;
    },
    announcingOwnBiosChange: { current: false },
  };
  return { unwire: wirePanelEvents(ctx) };
}

async function dataChanged(detail: object): Promise<void> {
  globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail }));
  await flush();
  await flush();
}

describe("which game pages a platform's change reaches", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _resetSharedReadsForTests();
    vi.mocked(cachedStore.getCachedGameDetail).mockResolvedValue({
      found: true,
      rom_id: ROM_ID,
      platform_slug: "snes",
      platform_fs_slug: "snes",
      metadata: null,
      stale_fields: [],
    } as Awaited<ReturnType<typeof cachedStore.getCachedGameDetail>>);
    vi.mocked(backend.getBiosStatus).mockResolvedValue({ bios_status: null, bios_status_unknown: true });
  });

  it("re-reads the BIOS answer of a page whose platform shares the changed platform's slug", async () => {
    const page = pageOn("snes", "snes");

    await dataChanged({ type: "bios", platform_slug: "snes", platform_fs_slug: "snes-hacks" });

    expect(vi.mocked(backend.getBiosStatus)).toHaveBeenCalledWith(ROM_ID);
    page.unwire();
  });

  it("leaves the BIOS answer of a page on another slug", async () => {
    const page = pageOn("snes", "snes");

    await dataChanged({ type: "bios", platform_slug: "gba", platform_fs_slug: "gba" });

    expect(vi.mocked(backend.getBiosStatus)).not.toHaveBeenCalled();
    page.unwire();
  });

  it("re-reads the core of a page on the platform whose pick changed", async () => {
    const page = pageOn("snes", "snes-hacks");

    await dataChanged({ type: "core_changed", platform_fs_slug: "snes-hacks" });

    expect(vi.mocked(backend.getPlatformCoreInfo)).toHaveBeenCalledWith(ROM_ID);
    page.unwire();
  });

  it("leaves the core of a page on another platform of the same slug", async () => {
    const page = pageOn("snes", "snes");

    await dataChanged({ type: "core_changed", platform_fs_slug: "snes-hacks" });

    expect(vi.mocked(backend.getPlatformCoreInfo)).not.toHaveBeenCalled();
    page.unwire();
  });

  it("re-reads the core of a page of a game RomM gives no folder on a change announced for no folder", async () => {
    const page = pageOn("snes", "");

    await dataChanged({ type: "core_changed", platform_fs_slug: null });

    expect(vi.mocked(backend.getPlatformCoreInfo)).toHaveBeenCalledWith(ROM_ID);
    page.unwire();
  });

  it("leaves the core of a page of a game RomM gives no folder on a change announced for a platform", async () => {
    const page = pageOn("snes", "");

    await dataChanged({ type: "core_changed", platform_fs_slug: "snes" });

    expect(vi.mocked(backend.getPlatformCoreInfo)).not.toHaveBeenCalled();
    page.unwire();
  });

  it("leaves the core of a page on a platform on a change announced for no folder", async () => {
    const page = pageOn("snes", "snes-hacks");

    await dataChanged({ type: "core_changed", platform_fs_slug: null });

    expect(vi.mocked(backend.getPlatformCoreInfo)).not.toHaveBeenCalled();
    page.unwire();
  });

  it("keeps the folder the game's platform is kept in once the page has loaded", async () => {
    const folderRef = { current: "" };
    vi.mocked(cachedStore.getCachedGameDetail).mockResolvedValue({
      found: true,
      rom_id: ROM_ID,
      platform_slug: "snes",
      platform_fs_slug: "snes-hacks",
      metadata: null,
      stale_fields: [],
    } as Awaited<ReturnType<typeof cachedStore.getCachedGameDetail>>);

    await loadData(
      APP_ID,
      () => false,
      { current: null },
      { current: "" },
      folderRef,
      { current: seqs() },
      () => {},
      () => {},
    );

    expect(folderRef.current).toBe("snes-hacks");
  });

  it("names both the slug and the platform's folder when a platform's BIOS files change", () => {
    const seen: unknown[] = [];
    const listener = ((e: Event) => seen.push((e as CustomEvent).detail)) as EventListener;
    globalThis.addEventListener("romm_data_changed", listener);
    try {
      announceBiosChange("snes", "snes-hacks");
    } finally {
      globalThis.removeEventListener("romm_data_changed", listener);
    }

    expect(seen).toEqual([{ type: "bios", platform_slug: "snes", platform_fs_slug: "snes-hacks" }]);
  });
});
