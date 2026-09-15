import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  registerMetadataPatches,
  unregisterMetadataPatches,
  applyAllMetadata,
  applyAllPlaytime,
  updatePlaytimeDisplay,
} from "./metadataPatches";
import type { RomMetadata } from "../types";

// RomMetadata has several required fields; build a full object and override the
// few that applyDirectMutations actually reads (average_rating, steam_categories).
function makeMeta(overrides: Partial<RomMetadata> = {}): RomMetadata {
  return {
    summary: "",
    genres: [],
    companies: [],
    first_release_date: null,
    average_rating: null,
    game_modes: [],
    player_count: "",
    cached_at: 0,
    ...overrides,
  };
}

interface FakeOverview {
  appid: number;
  controller_support: number;
  metacritic_score: number;
  m_setStoreCategories: Set<number>;
}

function makeOverview(appid: number): FakeOverview {
  return { appid, controller_support: 0, metacritic_score: 0, m_setStoreCategories: new Set<number>() };
}

// appId 100 → rom_id 10
const APP_ID_MAP = { "100": 10 };

describe("applyAllMetadata (#1203 readiness retry)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // test-setup's afterEach calls unstubAllGlobals, so re-stub the Steam globals
    // each test. __mobxGlobals is undefined here → stateTransaction applies the
    // mutation block directly (its `if (!globals) return block()` path).
    vi.stubGlobal("__mobxGlobals", undefined);
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(), allApps: [] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("applies controller support, metacritic and categories when the overview is present", async () => {
    const ov = makeOverview(100);
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(ov as unknown as SteamAppOverview);
    registerMetadataPatches({ "10": makeMeta({ average_rating: 88, steam_categories: [1, 2] }) }, APP_ID_MAP);

    await applyAllMetadata();

    expect(ov.controller_support).toBe(2);
    expect(ov.metacritic_score).toBe(88);
    expect([...ov.m_setStoreCategories]).toEqual([1, 2]);
  });

  it("retries an app whose overview isn't loaded yet, then applies once it appears", async () => {
    const ov = makeOverview(100);
    vi.mocked(appStore.GetAppOverviewByAppID)
      .mockReturnValueOnce(null) // first pass: appStore not populated yet → silent skip today
      .mockReturnValue(ov as unknown as SteamAppOverview); // retry: overview now present
    registerMetadataPatches({ "10": makeMeta({ average_rating: 70 }) }, APP_ID_MAP);

    const done = applyAllMetadata();
    // The first synchronous pass (0ms) ran against a null overview → nothing applied yet.
    expect(ov.controller_support).toBe(0);

    await vi.advanceTimersByTimeAsync(1000); // second attempt fires at +1s
    await done;

    expect(ov.controller_support).toBe(2);
    expect(ov.metacritic_score).toBe(70);
  });

  it("is idempotent — repeated applies don't duplicate categories or corrupt state", async () => {
    const ov = makeOverview(100);
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(ov as unknown as SteamAppOverview);
    registerMetadataPatches({ "10": makeMeta({ steam_categories: [5, 5, 7] }) }, APP_ID_MAP);

    await applyAllMetadata();
    await applyAllMetadata(); // a second pass must be safe (retries re-apply)

    expect([...ov.m_setStoreCategories].sort((a, b) => a - b)).toEqual([5, 7]);
    expect(ov.controller_support).toBe(2);
  });

  it("does not retry metadata mutations after cancellation", async () => {
    const ov = makeOverview(100);
    vi.mocked(appStore.GetAppOverviewByAppID)
      .mockReturnValueOnce(null)
      .mockReturnValue(ov as unknown as SteamAppOverview);
    registerMetadataPatches({ "10": makeMeta({ average_rating: 70 }) }, APP_ID_MAP);
    const controller = new AbortController();

    const done = applyAllMetadata(controller.signal);
    controller.abort();
    await vi.advanceTimersByTimeAsync(1000);
    await done;

    expect(appStore.GetAppOverviewByAppID).toHaveBeenCalledTimes(1);
    expect(ov.controller_support).toBe(0);
  });

  it("does not retry playtime mutations after cancellation", async () => {
    const overview = { minutes_playtime_forever: 0, rt_last_time_played: 0 };
    vi.mocked(appStore.GetAppOverviewByAppID)
      .mockReturnValueOnce(null)
      .mockReturnValue(overview as unknown as SteamAppOverview);
    const controller = new AbortController();

    const done = applyAllPlaytime({ "10": { total_seconds: 600 } }, APP_ID_MAP, controller.signal);
    controller.abort();
    await vi.advanceTimersByTimeAsync(1000);
    await done;

    expect(appStore.GetAppOverviewByAppID).toHaveBeenCalledTimes(1);
    expect(overview.minutes_playtime_forever).toBe(0);
  });
});

describe("unregisterMetadataPatches", () => {
  beforeEach(() => {
    vi.stubGlobal("__mobxGlobals", undefined);
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(), allApps: [] });
  });

  it("leaves nothing for a later apply to write", async () => {
    const registered = makeOverview(100);
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(registered as unknown as SteamAppOverview);
    registerMetadataPatches({ "10": makeMeta({ average_rating: 88, steam_categories: [1] }) }, APP_ID_MAP);
    await applyAllMetadata();
    // Control: the registration being cleared is one that does reach the overview.
    expect(registered.controller_support).toBe(2);

    unregisterMetadataPatches();

    const later = makeOverview(100);
    vi.mocked(appStore.GetAppOverviewByAppID)
      .mockClear()
      .mockReturnValue(later as unknown as SteamAppOverview);
    await applyAllMetadata();

    // No app is pending any more, so the appStore is never even asked.
    expect(appStore.GetAppOverviewByAppID).not.toHaveBeenCalled();
    expect(later.controller_support).toBe(0);
    expect(later.metacritic_score).toBe(0);
    expect([...later.m_setStoreCategories]).toEqual([]);
  });
});

describe("applyAllPlaytime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("__mobxGlobals", undefined);
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(), allApps: [] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  // The DOM signal every playtime write emits, so a mounted view can re-read the
  // overview on the same mount. Collected per test and torn down with it.
  function recordPlaytimeSignals(): { appIds: number[]; stop: () => void } {
    const appIds: number[] = [];
    const listener = (e: Event) => appIds.push((e as CustomEvent<{ appId: number }>).detail.appId);
    globalThis.addEventListener("romm_playtime_changed", listener);
    return { appIds, stop: () => globalThis.removeEventListener("romm_playtime_changed", listener) };
  }

  it("writes the recorded total as whole minutes and announces the app it wrote", async () => {
    const overview = { minutes_playtime_forever: 0, rt_last_time_played: 0 };
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(overview as unknown as SteamAppOverview);
    const signals = recordPlaytimeSignals();

    await applyAllPlaytime({ "10": { total_seconds: 610 } }, APP_ID_MAP);
    signals.stop();

    expect(overview.minutes_playtime_forever).toBe(10);
    // A bulk pass writes minutes only: it is not evidence the game was just played.
    expect(overview.rt_last_time_played).toBe(0);
    expect(signals.appIds).toEqual([100]);
  });

  it("writes only for a rom that has a shortcut and a recorded total above zero", async () => {
    const overview = { minutes_playtime_forever: 0, rt_last_time_played: 0 };
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(overview as unknown as SteamAppOverview);

    await applyAllPlaytime(
      {
        "10": { total_seconds: 0 }, // has a shortcut, nothing recorded
        "20": { total_seconds: 600 }, // the one write
        "99": { total_seconds: 600 }, // recorded, but no shortcut shows it
      },
      { "100": 10, "200": 20 },
    );

    expect(appStore.GetAppOverviewByAppID).toHaveBeenCalledTimes(1);
    expect(appStore.GetAppOverviewByAppID).toHaveBeenCalledWith(200);
    expect(overview.minutes_playtime_forever).toBe(10);
  });

  it("keeps the playtime Steam already shows when the recorded total is under a minute", async () => {
    const overview = { minutes_playtime_forever: 7, rt_last_time_played: 0 };
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(overview as unknown as SteamAppOverview);
    const signals = recordPlaytimeSignals();

    const done = applyAllPlaytime({ "10": { total_seconds: 30 } }, APP_ID_MAP);
    await vi.advanceTimersByTimeAsync(9000); // the whole retry ladder, had this counted as a failure
    await done;
    signals.stop();

    expect(overview.minutes_playtime_forever).toBe(7);
    expect(signals.appIds).toEqual([]);
    // Rounding down to zero minutes is not a failure, so the app is not retried.
    expect(appStore.GetAppOverviewByAppID).toHaveBeenCalledTimes(1);
  });

  it("gives up after the whole retry ladder when the overview never appears", async () => {
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(null);
    const signals = recordPlaytimeSignals();

    const done = applyAllPlaytime({ "10": { total_seconds: 600 } }, APP_ID_MAP);
    await vi.advanceTimersByTimeAsync(9000); // 0ms + 1s + 3s + 5s
    await done;
    signals.stop();

    expect(appStore.GetAppOverviewByAppID).toHaveBeenCalledTimes(4);
    expect(signals.appIds).toEqual([]);
  });
});

describe("updatePlaytimeDisplay", () => {
  // The other side of the `updateLastPlayed` contract. A bulk pass must NOT
  // stamp (pinned in applyAllPlaytime above, which passes false); a single-app
  // write must, because that one runs off a session that just ended. The value
  // is what makes this assertable rather than "a field changed": Steam reads
  // rt_last_time_played as Unix SECONDS, so dropping the `/ 1000` — or flipping
  // the parameter's `= true` default, which turns Last Played off for every
  // single-app write — makes this red.
  it("stamps the second the session ended when the caller asks for it", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-04T05:06:07.890Z"));
    const overview = { minutes_playtime_forever: 0, rt_last_time_played: 1 };
    vi.stubGlobal("__mobxGlobals", undefined);
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn().mockReturnValue(overview),
      allApps: [],
    });

    // Two arguments: the default is the stamping one.
    expect(updatePlaytimeDisplay(100, 600)).toBe(true);

    expect(overview.minutes_playtime_forever).toBe(10);
    expect(overview.rt_last_time_played).toBe(Math.floor(Date.parse("2026-03-04T05:06:07.890Z") / 1000));
    vi.useRealTimers();
  });

  it("rejects non-finite totals without mutating the Steam overview", () => {
    const overview = { minutes_playtime_forever: 12, rt_last_time_played: 34 };
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn().mockReturnValue(overview),
      allApps: [],
    });

    expect(updatePlaytimeDisplay(100, Number.NaN, false)).toBe(false);
    expect(overview).toEqual({ minutes_playtime_forever: 12, rt_last_time_played: 34 });
  });
});
