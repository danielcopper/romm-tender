// The panel's core info is state the BIOS tab is handed, and the tab prints its
// emulator's name only where the BIOS answer names none (the store-only
// sentence) — so where it comes from, and which answer may replace it, is
// pinned here on the state itself.

import { describe, it, expect, vi, beforeEach } from "vitest";
import type { SetStateAction } from "react";
import * as backend from "../api/backend";
import { _resetSharedReadsForTests } from "../api/sharedReads";
import * as cachedStore from "../utils/cachedGameDetailStore";
import type { CoreInfo } from "../types";
import { wirePanelEvents, type PanelEventContext } from "./panelEvents";
import { loadData, type PanelState } from "./panelState";

vi.mock("../utils/cachedGameDetailStore", () => ({
  getCachedGameDetail: vi.fn(),
  invalidateCachedGameDetail: vi.fn(),
}));

const APP_ID = 5000;

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function coreInfoNamed(label: string): CoreInfo {
  return {
    active_core: `${label}.so`,
    active_core_label: label,
    platform_core_label: null,
    has_game_override: false,
    emulator_data_available: true,
    emulator_data_reason: null,
    emulator_source: { kind: "retrodeck", starts_games: true },
    platform_system: null,
    emulators: [],
  };
}

function basePanelState(romId: number): PanelState {
  return {
    loading: false,
    romId,
    romName: "Game",
    platformName: "SNES",
    installed: false,
    installedRom: null,
    metadata: null,
    coverBase64: null,
    biosStatus: null,
    biosLevel: null,
    biosReadFailed: false,
    coreInfo: null,
    saveSyncEnabled: false,
    saveStatus: null,
    conflicts: [],
    error: false,
    activeTab: "bios",
    raId: null,
    slotConfirmed: false,
    activeSlot: "default",
    activeSlotKnown: false,
    availableSlots: [],
    lastKnownSlots: null,
    slotsLoading: false,
    regions: [],
    languages: [],
  };
}

/** A panel's event lane over a plain state value, for the ROM *romId* shows. */
function panelFor(romId: number, coreInfo: CoreInfo | null) {
  let state: PanelState = { ...basePanelState(romId), coreInfo };
  const ctx: PanelEventContext = {
    appId: APP_ID,
    cancelled: () => false,
    romIdRef: { current: romId },
    platformSlugRef: { current: "snes" },
    slotsLoadedRef: { current: false },
    readSeqs: { current: { detail: 0, saveStatus: 0, slots: 0, slotTracking: 0, bios: 0 } },
    setState: (update: SetStateAction<PanelState>) => {
      state = typeof update === "function" ? update(state) : update;
    },
  };
  const unwire = wirePanelEvents(ctx);
  return {
    unwire,
    get coreInfo() {
      return state.coreInfo;
    },
  };
}

const detailFor = (romId: number) =>
  ({ found: true, rom_id: romId, platform_slug: "snes", metadata: null, stale_fields: [] }) as Awaited<
    ReturnType<typeof cachedStore.getCachedGameDetail>
  >;

async function dataChanged(detail: object): Promise<void> {
  globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail }));
  await flush();
  await flush();
}

const switchTo = (romId: number) => {
  vi.mocked(cachedStore.getCachedGameDetail).mockResolvedValue(detailFor(romId));
  return dataChanged({ type: "version_switched", app_id: APP_ID, rom_id: romId });
};

describe("the panel's core info", () => {
  beforeEach(() => {
    // Cleared, not reset: every other endpoint keeps the suite's stub, which answers.
    vi.clearAllMocks();
    vi.mocked(backend.getPlatformCoreInfo).mockReset();
    _resetSharedReadsForTests();
    vi.mocked(backend.getBiosStatus).mockResolvedValue({ bios_status: null, bios_status_unknown: true });
  });

  it("comes from the dedicated core-info path on the first load, never from the cached detail", async () => {
    vi.mocked(cachedStore.getCachedGameDetail).mockResolvedValue(detailFor(1));
    vi.mocked(backend.getPlatformCoreInfo).mockResolvedValue(coreInfoNamed("MyCore"));
    let state = basePanelState(0);

    await loadData(
      APP_ID,
      () => false,
      { current: null },
      { current: "" },
      { current: { detail: 0, saveStatus: 0, slots: 0, slotTracking: 0, bios: 0 } },
      (update) => {
        state = typeof update === "function" ? update(state) : update;
      },
      () => {},
    );
    await flush();

    expect(vi.mocked(backend.getPlatformCoreInfo)).toHaveBeenCalledWith(1);
    expect(state.coreInfo?.active_core_label).toBe("MyCore");
  });

  it("follows a core change", async () => {
    const panel = panelFor(1, coreInfoNamed("Snes9x"));
    vi.mocked(cachedStore.getCachedGameDetail).mockResolvedValue(detailFor(1));
    vi.mocked(backend.getPlatformCoreInfo).mockResolvedValue(coreInfoNamed("bsnes"));

    await dataChanged({ type: "core_changed", platform_slug: "snes" });

    expect(panel.coreInfo?.active_core_label).toBe("bsnes");
    panel.unwire();
  });

  it("follows a version switch to the switched-to version's own", async () => {
    const panel = panelFor(1, coreInfoNamed("Snes9x"));
    vi.mocked(backend.getPlatformCoreInfo).mockResolvedValue(coreInfoNamed("bsnes"));

    await switchTo(2);

    expect(vi.mocked(backend.getPlatformCoreInfo)).toHaveBeenCalledWith(2);
    expect(panel.coreInfo?.active_core_label).toBe("bsnes");
    panel.unwire();
  });

  it("stands where the switched-to version's core read fails", async () => {
    const panel = panelFor(1, coreInfoNamed("Snes9x"));
    vi.mocked(backend.getPlatformCoreInfo).mockRejectedValue(new Error("offline"));

    await switchTo(2);

    expect(panel.coreInfo?.active_core_label).toBe("Snes9x");
    panel.unwire();
  });

  it("takes no core read issued for a version the panel has switched off", async () => {
    const held = deferred<CoreInfo>();
    vi.mocked(backend.getPlatformCoreInfo).mockImplementation((romId: number) =>
      romId === 2 ? held.promise : Promise.resolve(coreInfoNamed("bsnes")),
    );
    const panel = panelFor(1, coreInfoNamed("Snes9x"));
    await switchTo(2);
    await switchTo(3);
    expect(panel.coreInfo?.active_core_label).toBe("bsnes");

    held.resolve(coreInfoNamed("Mesen"));
    await flush();
    await flush();

    expect(panel.coreInfo?.active_core_label).toBe("bsnes");
    panel.unwire();
  });

  it("takes no core-change answer read for the previous version once a switch has landed", async () => {
    const held = deferred<CoreInfo>();
    vi.mocked(backend.getPlatformCoreInfo).mockImplementation((romId: number) =>
      romId === 1 ? held.promise : Promise.resolve(coreInfoNamed("bsnes")),
    );
    const panel = panelFor(1, null);
    // A core change for the first version, its core read held open.
    vi.mocked(cachedStore.getCachedGameDetail).mockResolvedValue(detailFor(1));
    await dataChanged({ type: "core_changed", platform_slug: "snes" });

    await switchTo(2);
    expect(panel.coreInfo?.active_core_label).toBe("bsnes");

    held.resolve(coreInfoNamed("Mesen"));
    await flush();
    await flush();

    expect(panel.coreInfo?.active_core_label).toBe("bsnes");
    panel.unwire();
  });
});
