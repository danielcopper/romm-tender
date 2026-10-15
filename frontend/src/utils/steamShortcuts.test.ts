import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import * as backend from "../api/backend";
import {
  addShortcut,
  getExistingRomMShortcuts,
  getLiveRomMShortcutAppIds,
  scanShortcutOwnership,
  removeShortcutConfirmedOutcome,
  setLaunchOptionsConfirmed,
} from "./steamShortcuts";
import type { ShortcutFileReading } from "./shortcutFile";
import type { SyncAddItem } from "../types";

const ROM_LAUNCHER = "/home/deck/.local/bin/tender-rom-launcher";

/**
 * Builds a RegisterForAppDetails mock that, on registration, schedules a single
 * callback fire (via queueMicrotask) carrying the details produced by
 * ``detailsFor(appId)``. Returning ``undefined`` simulates a runtime that never
 * delivers usable details (early/no-data fire), driving the timeout branch.
 */
function makeRegisterForAppDetails(detailsFor: (appId: number) => SteamAppDetails | undefined) {
  const unregister = vi.fn();
  const fn = vi.fn((appId: number, callback: (d: SteamAppDetails | undefined) => void) => {
    queueMicrotask(() => callback(detailsFor(appId)));
    return { unregister };
  });
  return { fn, unregister };
}

describe("setLaunchOptionsConfirmed", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it("fires SetAppLaunchOptions and resolves true when the read-back matches", async () => {
    const value = 'flatpak run net.retrodeck.retrodeck "/games/x.bin"';
    const setLaunchOptions = vi.fn();
    const { fn } = makeRegisterForAppDetails(() => ({ strLaunchOptions: value }));
    vi.stubGlobal("SteamClient", {
      Apps: { SetAppLaunchOptions: setLaunchOptions, RegisterForAppDetails: fn },
    });

    await expect(setLaunchOptionsConfirmed(123, value)).resolves.toBe(true);
    expect(setLaunchOptions).toHaveBeenCalledWith(123, value);
  });

  it("confirms an empty-string value against an empty read-back", async () => {
    const setLaunchOptions = vi.fn();
    const { fn } = makeRegisterForAppDetails(() => ({ strLaunchOptions: "" }));
    vi.stubGlobal("SteamClient", {
      Apps: { SetAppLaunchOptions: setLaunchOptions, RegisterForAppDetails: fn },
    });

    await expect(setLaunchOptionsConfirmed(7, "")).resolves.toBe(true);
    expect(setLaunchOptions).toHaveBeenCalledWith(7, "");
  });

  it("resolves false and unregisters on timeout when the read-back never matches", async () => {
    vi.useFakeTimers();
    const setLaunchOptions = vi.fn();
    // Read-back always reports a stale value, so the match never happens.
    const { fn, unregister } = makeRegisterForAppDetails(() => ({ strLaunchOptions: "stale" }));
    vi.stubGlobal("SteamClient", {
      Apps: { SetAppLaunchOptions: setLaunchOptions, RegisterForAppDetails: fn },
    });

    const promise = setLaunchOptionsConfirmed(99, "new-value", 2000);
    // Flush the queued microtask callback (reports "stale", no match) then the timeout.
    await vi.advanceTimersByTimeAsync(2000);

    await expect(promise).resolves.toBe(false);
    expect(setLaunchOptions).toHaveBeenCalledWith(99, "new-value");
    expect(unregister).toHaveBeenCalled();
  });

  it("confirms a write whose report misses the wait when the re-read shows the value", async () => {
    vi.useFakeTimers();
    // The first registration only ever reports the old value; a fresh read
    // after the wait sees the value Steam took.
    let registrations = 0;
    const { fn } = makeRegisterForAppDetails(() => ({
      strLaunchOptions: ++registrations === 1 ? "old-value" : "new-value",
    }));
    vi.stubGlobal("SteamClient", {
      Apps: { SetAppLaunchOptions: vi.fn(), RegisterForAppDetails: fn },
    });

    const promise = setLaunchOptionsConfirmed(5, "new-value", 2000);
    await vi.advanceTimersByTimeAsync(2000);

    await expect(promise).resolves.toBe(true);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("still resolves false when the re-read after the wait shows a different value", async () => {
    vi.useFakeTimers();
    const { fn } = makeRegisterForAppDetails(() => ({ strLaunchOptions: "old-value" }));
    vi.stubGlobal("SteamClient", {
      Apps: { SetAppLaunchOptions: vi.fn(), RegisterForAppDetails: fn },
    });

    const promise = setLaunchOptionsConfirmed(5, "new-value", 2000);
    await vi.advanceTimersByTimeAsync(2000);

    await expect(promise).resolves.toBe(false);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("resolves false once the re-read is out of time too, when Steam never answers it", async () => {
    vi.useFakeTimers();
    const { fn, unregister } = makeRegisterForAppDetails(() => undefined);
    vi.stubGlobal("SteamClient", {
      Apps: { SetAppLaunchOptions: vi.fn(), RegisterForAppDetails: fn },
    });

    let outcome: boolean | undefined;
    void setLaunchOptionsConfirmed(5, "new-value", 2000).then((confirmed) => {
      outcome = confirmed;
    });
    await vi.advanceTimersByTimeAsync(2000);
    expect(fn).toHaveBeenCalledTimes(2);
    expect(outcome).toBeUndefined();

    await vi.advanceTimersByTimeAsync(2000);
    expect(outcome).toBe(false);
    expect(unregister).toHaveBeenCalledTimes(2);
  });

  describe("the line logged when the re-read confirms", () => {
    const RESCUE_LINE = "setLaunchOptionsConfirmed: appId 5 confirmed by the re-read after the report missed the wait";

    afterEach(() => {
      vi.restoreAllMocks();
    });

    function stubReports(reportFor: (registration: number) => SteamAppDetails | undefined) {
      let registrations = 0;
      const { fn } = makeRegisterForAppDetails(() => reportFor(++registrations));
      vi.stubGlobal("SteamClient", {
        Apps: { SetAppLaunchOptions: vi.fn(), RegisterForAppDetails: fn },
      });
    }

    it("is logged once, at info, when the report missed the wait and the re-read shows the value", async () => {
      vi.useFakeTimers();
      const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const logInfoSpy = vi.spyOn(backend, "logInfo").mockImplementation(() => {});
      stubReports((registration) => ({ strLaunchOptions: registration === 1 ? "old-value" : "new-value" }));

      const promise = setLaunchOptionsConfirmed(5, "new-value", 2000);
      await vi.advanceTimersByTimeAsync(2000);

      await expect(promise).resolves.toBe(true);
      expect(logInfoSpy).toHaveBeenCalledTimes(1);
      expect(logInfoSpy).toHaveBeenCalledWith(RESCUE_LINE);
      expect(logWarnSpy).not.toHaveBeenCalled();
    });

    it("is not logged when the first report confirms", async () => {
      const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const logInfoSpy = vi.spyOn(backend, "logInfo").mockImplementation(() => {});
      stubReports(() => ({ strLaunchOptions: "new-value" }));

      await expect(setLaunchOptionsConfirmed(5, "new-value", 2000)).resolves.toBe(true);
      expect(logInfoSpy).not.toHaveBeenCalled();
      expect(logWarnSpy).not.toHaveBeenCalled();
    });

    it("is not logged when the re-read shows a different value too", async () => {
      vi.useFakeTimers();
      const logInfoSpy = vi.spyOn(backend, "logInfo").mockImplementation(() => {});
      stubReports(() => ({ strLaunchOptions: "old-value" }));

      const promise = setLaunchOptionsConfirmed(5, "new-value", 2000);
      await vi.advanceTimersByTimeAsync(2000);

      await expect(promise).resolves.toBe(false);
      expect(logInfoSpy).not.toHaveBeenCalled();
    });
  });

  describe("the line logged when the confirm fails", () => {
    const WRITTEN = "flatpak run net.retrodeck.retrodeck '/games/psx/Some Game (USA).chd'";
    const HELD_BEFORE = 'flatpak run net.retrodeck.retrodeck "/games/psx/Some Game (USA).chd"';

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("names the appId and that the re-read delivered no details", async () => {
      vi.useFakeTimers();
      const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const { fn } = makeRegisterForAppDetails(() => undefined);
      vi.stubGlobal("SteamClient", {
        Apps: { SetAppLaunchOptions: vi.fn(), RegisterForAppDetails: fn },
      });

      const promise = setLaunchOptionsConfirmed(5, WRITTEN, 2000);
      await vi.advanceTimersByTimeAsync(4000);

      await expect(promise).resolves.toBe(false);
      expect(logWarnSpy).toHaveBeenCalledTimes(1);
      expect(logWarnSpy).toHaveBeenCalledWith(
        "setLaunchOptionsConfirmed: appId 5 not confirmed: the re-read delivered no details within 2000ms; " +
          "the file could not be read (no answer)",
      );
    });

    it("carries the value the re-read showed and the value written, both in full", async () => {
      vi.useFakeTimers();
      const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const { fn } = makeRegisterForAppDetails(() => ({ strLaunchOptions: HELD_BEFORE }));
      vi.stubGlobal("SteamClient", {
        Apps: { SetAppLaunchOptions: vi.fn(), RegisterForAppDetails: fn },
      });

      const promise = setLaunchOptionsConfirmed(5, WRITTEN, 2000);
      await vi.advanceTimersByTimeAsync(2000);

      await expect(promise).resolves.toBe(false);
      expect(logWarnSpy).toHaveBeenCalledTimes(1);
      expect(logWarnSpy).toHaveBeenCalledWith(
        `setLaunchOptionsConfirmed: appId 5 not confirmed: the re-read shows ${JSON.stringify(HELD_BEFORE)}, ` +
          `the write was ${JSON.stringify(WRITTEN)}; the file could not be read (no answer)`,
      );
    });

    it("shows an empty value the re-read gave as an empty string", async () => {
      vi.useFakeTimers();
      const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const { fn } = makeRegisterForAppDetails(() => ({ strLaunchOptions: "" }));
      vi.stubGlobal("SteamClient", {
        Apps: { SetAppLaunchOptions: vi.fn(), RegisterForAppDetails: fn },
      });

      const promise = setLaunchOptionsConfirmed(5, WRITTEN, 2000);
      await vi.advanceTimersByTimeAsync(2000);

      await expect(promise).resolves.toBe(false);
      expect(logWarnSpy).toHaveBeenCalledTimes(1);
      expect(logWarnSpy).toHaveBeenCalledWith(
        `setLaunchOptionsConfirmed: appId 5 not confirmed: the re-read shows "", the write was ${JSON.stringify(WRITTEN)}; ` +
          "the file could not be read (no answer)",
      );
    });
  });
});

/** The endpoint is one `vi.fn` for the whole file: start each test from its suite default. */
function resetFileEndpoint() {
  vi.mocked(backend.getShortcutLaunchOptions)
    .mockReset()
    .mockResolvedValue(undefined as unknown as backend.ShortcutLaunchOptions);
}

describe("setLaunchOptionsConfirmed's reading of Steam's shortcut file", () => {
  const WRITTEN = "flatpak run net.retrodeck.retrodeck '/games/psx/Some Game (USA).chd'";
  const HELD_BEFORE = 'flatpak run net.retrodeck.retrodeck "/games/psx/Some Game (USA).chd"';

  beforeEach(resetFileEndpoint);

  afterEach(() => {
    vi.restoreAllMocks();
    resetFileEndpoint();
  });

  /** Steam's report and re-read both show `reread`, or deliver nothing when it is `undefined`. */
  function stubSteamShowing(reread: string | undefined) {
    const { fn } = makeRegisterForAppDetails(() => (reread === undefined ? undefined : { strLaunchOptions: reread }));
    vi.stubGlobal("SteamClient", { Apps: { SetAppLaunchOptions: vi.fn(), RegisterForAppDetails: fn } });
  }

  function fileHolding(entries: [number, string][]): ShortcutFileReading {
    return { status: "read", launchOptions: new Map(entries) };
  }

  /** A reader that answers `reading` after `ms` of (fake) time. */
  function answeringAfter(ms: number, reading: ShortcutFileReading) {
    return () => new Promise<ShortcutFileReading>((resolve) => setTimeout(() => resolve(reading), ms));
  }

  async function confirmWith(readFile: () => Promise<ShortcutFileReading>): Promise<boolean> {
    vi.useFakeTimers();
    const promise = setLaunchOptionsConfirmed(5, WRITTEN, 2000, readFile);
    await vi.advanceTimersByTimeAsync(4000);
    return promise;
  }

  it("confirms a write the re-read shows another value for when the file holds the value written", async () => {
    const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
    const logInfoSpy = vi.spyOn(backend, "logInfo").mockImplementation(() => {});
    stubSteamShowing(HELD_BEFORE);

    await expect(confirmWith(answeringAfter(87, fileHolding([[5, WRITTEN]])))).resolves.toBe(true);
    expect(logInfoSpy).toHaveBeenCalledTimes(1);
    expect(logInfoSpy).toHaveBeenCalledWith(
      `setLaunchOptionsConfirmed: appId 5 confirmed by Steam's shortcut file after the re-read shows ${JSON.stringify(HELD_BEFORE)}; ` +
        "waited 87ms for the file",
    );
    expect(logWarnSpy).not.toHaveBeenCalled();
  });

  it("confirms a write whose re-read delivered no details when the file holds the value written", async () => {
    const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
    const logInfoSpy = vi.spyOn(backend, "logInfo").mockImplementation(() => {});
    stubSteamShowing(undefined);

    await expect(confirmWith(() => Promise.resolve(fileHolding([[5, WRITTEN]])))).resolves.toBe(true);
    expect(logInfoSpy).toHaveBeenCalledWith(
      "setLaunchOptionsConfirmed: appId 5 confirmed by Steam's shortcut file after the re-read delivered no details " +
        "within 2000ms; waited 0ms for the file",
    );
    expect(logWarnSpy).not.toHaveBeenCalled();
  });

  it("stays a failure when the file shows another value too, and the warning names it", async () => {
    const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
    const logInfoSpy = vi.spyOn(backend, "logInfo").mockImplementation(() => {});
    stubSteamShowing(HELD_BEFORE);

    await expect(confirmWith(() => Promise.resolve(fileHolding([[5, "a third value"]])))).resolves.toBe(false);
    expect(logWarnSpy).toHaveBeenCalledTimes(1);
    expect(logWarnSpy).toHaveBeenCalledWith(
      `setLaunchOptionsConfirmed: appId 5 not confirmed: the re-read shows ${JSON.stringify(HELD_BEFORE)}, ` +
        `the write was ${JSON.stringify(WRITTEN)}; the file shows "a third value"`,
    );
    expect(logInfoSpy).not.toHaveBeenCalled();
  });

  it("names the value written beside the file's value when the re-read delivered no details", async () => {
    const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
    stubSteamShowing(undefined);

    await expect(confirmWith(() => Promise.resolve(fileHolding([[5, HELD_BEFORE]])))).resolves.toBe(false);
    expect(logWarnSpy).toHaveBeenCalledTimes(1);
    expect(logWarnSpy).toHaveBeenCalledWith(
      "setLaunchOptionsConfirmed: appId 5 not confirmed: the re-read delivered no details within 2000ms, " +
        `the write was ${JSON.stringify(WRITTEN)}; the file shows ${JSON.stringify(HELD_BEFORE)}`,
    );
  });

  it("stays a failure when the file holds the value for another shortcut only", async () => {
    const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
    stubSteamShowing(HELD_BEFORE);

    await expect(confirmWith(() => Promise.resolve(fileHolding([[6, WRITTEN]])))).resolves.toBe(false);
    expect(logWarnSpy.mock.calls[0]![0]).toMatch(/; the file holds no such shortcut$/);
  });

  it("stays a failure when the file could not be read", async () => {
    const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
    stubSteamShowing(HELD_BEFORE);

    await expect(
      confirmWith(() => Promise.resolve({ status: "unreadable", why: "Steam's shortcut file could not be read." })),
    ).resolves.toBe(false);
    expect(logWarnSpy.mock.calls[0]![0]).toMatch(
      /; the file could not be read \(Steam's shortcut file could not be read\.\)$/,
    );
  });

  it("stays a failure when the file does not answer within its bound, and the warning names the bound", async () => {
    const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
    stubSteamShowing(HELD_BEFORE);
    vi.useFakeTimers();
    let outcome: boolean | undefined;
    void setLaunchOptionsConfirmed(5, WRITTEN, 2000, () => new Promise<ShortcutFileReading>(() => {})).then(
      (confirmed) => {
        outcome = confirmed;
      },
    );

    // The report wait runs its 2000ms out; the re-read answers at once.
    await vi.advanceTimersByTimeAsync(2000 + 4999);
    expect(outcome).toBeUndefined();

    await vi.advanceTimersByTimeAsync(1);
    expect(outcome).toBe(false);
    expect(logWarnSpy).toHaveBeenCalledTimes(1);
    expect(logWarnSpy.mock.calls[0]![0]).toMatch(/; the file could not be read \(no answer within 5000ms\)$/);
  });

  it("stays a failure when the reader rejects", async () => {
    const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
    stubSteamShowing(HELD_BEFORE);

    await expect(confirmWith(() => Promise.reject(new Error("boom")))).resolves.toBe(false);
    expect(logWarnSpy.mock.calls[0]![0]).toMatch(/; the file could not be read \(Error: boom\)$/);
  });

  it("asks the backend for this shortcut alone when no reader is passed", async () => {
    vi.spyOn(backend, "logInfo").mockImplementation(() => {});
    const ask = vi
      .spyOn(backend, "getShortcutLaunchOptions")
      .mockResolvedValue({ success: true, launch_options: [{ app_id: 5, launch_options: WRITTEN }] });
    stubSteamShowing(HELD_BEFORE);
    vi.useFakeTimers();

    const promise = setLaunchOptionsConfirmed(5, WRITTEN, 2000);
    await vi.advanceTimersByTimeAsync(4000);

    await expect(promise).resolves.toBe(true);
    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask).toHaveBeenCalledWith([5]);
  });

  it.each([
    {
      case: "the backend refuses the reading",
      answer: () => Promise.resolve({ success: false, reason: "shortcut_file_unreadable", message: "no" }),
    },
    { case: "the call rejects", answer: () => Promise.reject(new Error("socket closed")) },
    { case: "the call answers nothing", answer: () => Promise.resolve(undefined) },
  ])("stays a failure when $case", async ({ answer }) => {
    const logWarnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
    vi.mocked(backend.getShortcutLaunchOptions).mockImplementation(
      answer as unknown as typeof backend.getShortcutLaunchOptions,
    );
    stubSteamShowing(HELD_BEFORE);
    vi.useFakeTimers();

    const promise = setLaunchOptionsConfirmed(5, WRITTEN, 2000);
    await vi.advanceTimersByTimeAsync(4000);

    await expect(promise).resolves.toBe(false);
    expect(logWarnSpy).toHaveBeenCalledTimes(1);
    expect(logWarnSpy.mock.calls[0]![0]).toContain("; the file could not be read (");
  });

  it.each([
    { case: "the first report shows the value", reports: ["new-value"] },
    { case: "the re-read shows the value", reports: ["old-value", "new-value"] },
  ])("never reads the file when $case", async ({ reports }) => {
    vi.spyOn(backend, "logInfo").mockImplementation(() => {});
    let registrations = 0;
    const { fn } = makeRegisterForAppDetails(() => ({
      strLaunchOptions: reports[Math.min(registrations++, reports.length - 1)]!,
    }));
    vi.stubGlobal("SteamClient", { Apps: { SetAppLaunchOptions: vi.fn(), RegisterForAppDetails: fn } });
    const readFile = vi.fn(() => Promise.resolve(fileHolding([])));
    vi.useFakeTimers();

    const promise = setLaunchOptionsConfirmed(5, "new-value", 2000, readFile);
    await vi.advanceTimersByTimeAsync(4000);

    await expect(promise).resolves.toBe(true);
    expect(readFile).not.toHaveBeenCalled();
  });
});

describe("removeShortcutConfirmedOutcome", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it("removes one shortcut and confirms absence from the live store", async () => {
    const apps = new Map<number, object>([[77, {}]]);
    const remove = vi.fn((appId: number) => apps.delete(appId));
    vi.stubGlobal("collectionStore", { deckDesktopApps: { apps } });
    vi.stubGlobal("SteamClient", {
      Apps: {
        RemoveShortcut: remove,
        RegisterForAppDetails: (_appId: number, callback: (details: SteamAppDetails) => void) => {
          queueMicrotask(() => callback({ strShortcutExe: "/tender/bin/tender-rom-launcher" }));
          return { unregister: vi.fn() };
        },
      },
    });

    await expect(removeShortcutConfirmedOutcome(77)).resolves.toEqual({ status: "confirmed" });
    expect(remove).toHaveBeenCalledWith(77);
  });

  it("refuses without mutating when the live shortcut store is unreadable", async () => {
    const remove = vi.fn();
    vi.stubGlobal("collectionStore", { deckDesktopApps: undefined });
    vi.stubGlobal("SteamClient", { Apps: { RemoveShortcut: remove } });

    await expect(removeShortcutConfirmedOutcome(77)).resolves.toEqual({ status: "not_attempted" });
    expect(remove).not.toHaveBeenCalled();
  });

  it("refuses a live appId whose executable is not RomM-owned", async () => {
    const apps = new Map<number, object>([[77, {}]]);
    const remove = vi.fn();
    vi.stubGlobal("collectionStore", { deckDesktopApps: { apps } });
    vi.stubGlobal("SteamClient", {
      Apps: {
        RemoveShortcut: remove,
        RegisterForAppDetails: (_appId: number, callback: (details: SteamAppDetails) => void) => {
          queueMicrotask(() => callback({ strShortcutExe: "/usr/bin/foreign-game" }));
          return { unregister: vi.fn() };
        },
      },
    });

    await expect(removeShortcutConfirmedOutcome(77)).resolves.toEqual({ status: "not_attempted" });
    expect(remove).not.toHaveBeenCalled();
  });

  it("times out when Steam never removes the shortcut from its live store", async () => {
    vi.useFakeTimers();
    const apps = new Map<number, object>([[77, {}]]);
    const remove = vi.fn();
    vi.stubGlobal("collectionStore", { deckDesktopApps: { apps } });
    vi.stubGlobal("SteamClient", {
      Apps: {
        RemoveShortcut: remove,
        RegisterForAppDetails: (_appId: number, callback: (details: SteamAppDetails) => void) => {
          queueMicrotask(() => callback({ strShortcutExe: "/tender/bin/tender-rom-launcher" }));
          return { unregister: vi.fn() };
        },
      },
    });

    const result = removeShortcutConfirmedOutcome(77, 200);
    await vi.advanceTimersByTimeAsync(200);

    await expect(result).resolves.toEqual({ status: "attempted_unconfirmed" });
    expect(apps.has(77)).toBe(true);
  });

  it("reports an attempted-but-unconfirmed outcome when the store becomes unreadable", async () => {
    const apps = new Map<number, object>([[77, {}]]);
    const store: { deckDesktopApps?: { apps: Map<number, object> } } = { deckDesktopApps: { apps } };
    const remove = vi.fn(() => {
      delete store.deckDesktopApps;
    });
    vi.stubGlobal("collectionStore", store);
    vi.stubGlobal("SteamClient", { Apps: { RemoveShortcut: remove } });

    await expect(removeShortcutConfirmedOutcome(77, 200, true)).resolves.toEqual({
      status: "attempted_unconfirmed",
    });
    expect(remove).toHaveBeenCalledWith(77);
  });
});

describe("scanShortcutOwnership", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  /** Drive the 2 s `getAppDetails` timeout without waiting it out. */
  async function scanWithTimeouts() {
    vi.useFakeTimers();
    const scan = scanShortcutOwnership();
    // Let the microtask callbacks land, then expire the readings that got none.
    await vi.advanceTimersByTimeAsync(2500);
    return scan;
  }

  it("keeps an entry Steam never answered for apart from one proved not ours", async () => {
    // Reading a silent entry as an exe of "" classifies a shortcut of OURS as
    // foreign, which is what a caller deciding what to remove must not be told.
    const detailsByAppId: Record<number, { strShortcutExe: string } | undefined> = {
      10: { strShortcutExe: ROM_LAUNCHER },
      20: undefined,
      30: { strShortcutExe: "/usr/bin/some-other-game" },
    };
    const { fn } = makeRegisterForAppDetails((appId) => detailsByAppId[appId]);
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: fn } });
    vi.stubGlobal("collectionStore", {
      deckDesktopApps: {
        apps: new Map([
          [10, {}],
          [20, {}],
          [30, {}],
        ]),
      },
    });

    await expect(scanWithTimeouts()).resolves.toEqual({ owned: [10], unresolved: [20] });
  });

  it("reports no unresolved entries when every reading answered", async () => {
    const { fn } = makeRegisterForAppDetails((appId) => ({
      strShortcutExe: appId === 10 ? ROM_LAUNCHER : "/usr/bin/other",
    }));
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: fn } });
    vi.stubGlobal("collectionStore", {
      deckDesktopApps: {
        apps: new Map([
          [10, {}],
          [20, {}],
        ]),
      },
    });

    await expect(scanShortcutOwnership()).resolves.toEqual({ owned: [10], unresolved: [] });
  });

  it("returns null when the store could not be read at all", async () => {
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: vi.fn() } });
    vi.stubGlobal("collectionStore", undefined);

    await expect(scanShortcutOwnership()).resolves.toBeNull();
  });
});

describe("getLiveRomMShortcutAppIds", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the raw appIds of our-exe shortcuts with no backend-map intersection", async () => {
    const exeByAppId: Record<number, string> = {
      10: ROM_LAUNCHER,
      20: ROM_LAUNCHER,
      30: "/usr/bin/some-other-game",
    };
    const { fn } = makeRegisterForAppDetails((appId) => ({ strShortcutExe: exeByAppId[appId] ?? "" }));
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: fn } });
    vi.stubGlobal("collectionStore", {
      deckDesktopApps: {
        apps: new Map([
          [10, {}],
          [20, {}],
          [30, {}],
        ]),
      },
    });
    // Reconcile must NOT touch the backend map — the raw live set is exe-only.
    const mapSpy = vi.mocked(backend.getAppIdRomIdMap);

    const result = await getLiveRomMShortcutAppIds();
    expect(result).toEqual([10, 20]);
    expect(mapSpy).not.toHaveBeenCalled();
  });

  it("returns null when collectionStore is undefined (scan could not run)", async () => {
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: vi.fn() } });
    vi.stubGlobal("collectionStore", undefined);
    const result = await getLiveRomMShortcutAppIds();
    expect(result).toBeNull();
  });

  it("returns null when deckDesktopApps.apps is absent (store unreadable)", async () => {
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: vi.fn() } });
    vi.stubGlobal("collectionStore", { deckDesktopApps: undefined });
    const result = await getLiveRomMShortcutAppIds();
    expect(result).toBeNull();
  });

  it("returns [] when the scan ran but found no RomM shortcuts", async () => {
    const { fn } = makeRegisterForAppDetails(() => ({ strShortcutExe: "/usr/bin/other" }));
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: fn } });
    vi.stubGlobal("collectionStore", { deckDesktopApps: { apps: new Map([[10, {}]]) } });
    const result = await getLiveRomMShortcutAppIds();
    expect(result).toEqual([]);
  });
});

describe("getExistingRomMShortcuts", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("maps romId→appId for shortcuts with our exe AND a backend binding", async () => {
    const exeByAppId: Record<number, string> = {
      10: "/home/deck/.local/bin/tender-rom-launcher",
      20: "/home/deck/.local/bin/tender-rom-launcher",
    };
    const { fn } = makeRegisterForAppDetails((appId) => ({ strShortcutExe: exeByAppId[appId] ?? "" }));
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: fn } });
    vi.stubGlobal("collectionStore", {
      deckDesktopApps: {
        apps: new Map([
          [10, {}],
          [20, {}],
        ]),
      },
    });
    vi.mocked(backend.getAppIdRomIdMap).mockResolvedValue({ "10": 101, "20": 202 });

    const result = await getExistingRomMShortcuts();
    expect(result.get(101)).toBe(10);
    expect(result.get(202)).toBe(20);
    expect(result.size).toBe(2);
  });

  it("excludes shortcuts whose exe is not our launcher", async () => {
    const exeByAppId: Record<number, string> = {
      10: "/home/deck/.local/bin/tender-rom-launcher",
      30: "/usr/bin/some-other-game",
    };
    const { fn } = makeRegisterForAppDetails((appId) => ({ strShortcutExe: exeByAppId[appId] ?? "" }));
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: fn } });
    vi.stubGlobal("collectionStore", {
      deckDesktopApps: {
        apps: new Map([
          [10, {}],
          [30, {}],
        ]),
      },
    });
    vi.mocked(backend.getAppIdRomIdMap).mockResolvedValue({ "10": 101, "30": 303 });

    const result = await getExistingRomMShortcuts();
    expect(result.get(101)).toBe(10);
    expect(result.has(303)).toBe(false);
    expect(result.size).toBe(1);
  });

  it("excludes our-exe appIds absent from the backend map (orphans after DB reset)", async () => {
    const exe = "/home/deck/.local/bin/tender-rom-launcher";
    const { fn } = makeRegisterForAppDetails(() => ({ strShortcutExe: exe }));
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: fn } });
    vi.stubGlobal("collectionStore", {
      deckDesktopApps: {
        apps: new Map([
          [10, {}],
          [20, {}],
        ]),
      },
    });
    // Backend map empty (DB reset) — our shortcuts are detected by exe but unmapped.
    vi.mocked(backend.getAppIdRomIdMap).mockResolvedValue({});

    const result = await getExistingRomMShortcuts();
    expect(result.size).toBe(0);
  });

  it("returns empty and logs when the backend map fetch rejects", async () => {
    const exe = "/home/deck/.local/bin/tender-rom-launcher";
    const { fn } = makeRegisterForAppDetails(() => ({ strShortcutExe: exe }));
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: fn } });
    vi.stubGlobal("collectionStore", { deckDesktopApps: { apps: new Map([[10, {}]]) } });
    vi.mocked(backend.getAppIdRomIdMap).mockRejectedValue(new Error("network down"));
    const logErrorSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});

    const result = await getExistingRomMShortcuts();
    expect(result.size).toBe(0);
    // Non-vacuous: the catch path produces the empty map AND surfaces the failure.
    expect(logErrorSpy).toHaveBeenCalledWith(expect.stringContaining("network down"));
  });

  it("returns empty when there are no desktop apps", async () => {
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: vi.fn() } });
    vi.stubGlobal("collectionStore", { deckDesktopApps: undefined });
    const result = await getExistingRomMShortcuts();
    expect(result.size).toBe(0);
  });

  it("emits a heartbeat when the scan crosses the 10s window across batches", async () => {
    // Two full batches (CONCURRENCY=10 → 20 appIds across two iterations).
    const apps = new Map<number, object>();
    for (let appId = 1; appId <= 20; appId++) apps.set(appId, {});
    const exe = "/home/deck/.local/bin/tender-rom-launcher";
    const { fn } = makeRegisterForAppDetails(() => ({ strShortcutExe: exe }));
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: fn } });
    vi.stubGlobal("collectionStore", { deckDesktopApps: { apps } });
    vi.mocked(backend.getAppIdRomIdMap).mockResolvedValue({});

    // Drive Date.now() so the elapsed-since-last-heartbeat check trips once the
    // first batch completes. The loop seeds lastHeartbeat at the first call;
    // every later call returns a value > 10s past it.
    const base = 1_000_000;
    let calls = 0;
    const nowSpy = vi.spyOn(Date, "now").mockImplementation(() => {
      calls += 1;
      // First read seeds lastHeartbeat; subsequent reads are 11s later.
      return calls <= 1 ? base : base + 11_000;
    });

    await getExistingRomMShortcuts();

    // Non-vacuous: crossing the window fires the fire-and-forget heartbeat.
    expect(vi.mocked(backend.syncHeartbeat)).toHaveBeenCalled();
    nowSpy.mockRestore();
  });

  it("maps a caller-supplied pre-scanned list without re-running the live scan (#1366)", async () => {
    // The once-per-run scan is threaded in via preScanned, so the bound map is
    // built from that list and the expensive RegisterForAppDetails sweep is NOT
    // invoked a second time. This is the dedup linchpin.
    const registerSpy = vi.fn();
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: registerSpy } });
    // collectionStore is present but must never be read when preScanned is given.
    vi.stubGlobal("collectionStore", { deckDesktopApps: { apps: new Map([[123, {}]]) } });
    vi.mocked(backend.getAppIdRomIdMap).mockResolvedValue({ "5000": 501, "9000": 901 });

    const result = await getExistingRomMShortcuts([5000, 9000]);

    // Built from the passed list, intersected with the backend map.
    expect(result.get(501)).toBe(5000);
    expect(result.get(901)).toBe(9000);
    expect(result.size).toBe(2);
    // Non-vacuous: the scan machinery (per-appId RegisterForAppDetails) never ran.
    expect(registerSpy).not.toHaveBeenCalled();
  });

  it("returns an empty map for a null pre-scanned list (store was unreadable)", async () => {
    const registerSpy = vi.fn();
    vi.stubGlobal("SteamClient", { Apps: { RegisterForAppDetails: registerSpy } });
    // Clear accumulated history from earlier tests so the not-called assert is real.
    vi.mocked(backend.getAppIdRomIdMap).mockClear();
    vi.mocked(backend.getAppIdRomIdMap).mockResolvedValue({ "5000": 501 });

    const result = await getExistingRomMShortcuts(null);

    expect(result.size).toBe(0);
    // A null list short-circuits before the backend map too — nothing scanned.
    expect(registerSpy).not.toHaveBeenCalled();
    expect(vi.mocked(backend.getAppIdRomIdMap)).not.toHaveBeenCalled();
  });
});

describe("addShortcut — overview-readiness poll + empty-launch-options skip", () => {
  const EXE = "/home/deck/.local/bin/tender-rom-launcher";

  function item(launchOptions: string): SyncAddItem {
    return {
      rom_id: 1,
      name: "Test ROM",
      exe: EXE,
      start_dir: "/home/deck",
      launch_options: launchOptions,
      platform_name: "PSX",
      platform_collection: null,
      romm_collections: [],
    };
  }

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("sets shortcut properties as soon as the overview appears (no fixed 500ms wait)", async () => {
    vi.useFakeTimers();
    const setName = vi.fn();
    // Overview is absent on the first poll, present on the second — the poll,
    // not a fixed delay, gates the Set* calls.
    let overviewCalls = 0;
    const getOverview = vi.fn(() => (++overviewCalls >= 2 ? ({ appid: 4242 } as SteamAppOverview) : null));
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: getOverview, allApps: [] });
    vi.stubGlobal("SteamClient", {
      Apps: {
        AddShortcut: vi.fn().mockResolvedValue(4242),
        SetShortcutName: setName,
        SetShortcutExe: vi.fn(),
        SetShortcutStartDir: vi.fn(),
        SetAppLaunchOptions: vi.fn(),
        RegisterForAppDetails: vi.fn(() => ({ unregister: vi.fn() })),
      },
    });

    const promise = addShortcut(item(""));
    // Advance one poll interval so the second (truthy) overview check runs.
    await vi.advanceTimersByTimeAsync(100);
    const appId = await promise;

    expect(appId).toBe(4242);
    // The overview was polled exactly twice; Set* fired after the second check.
    expect(getOverview).toHaveBeenCalledTimes(2);
    expect(setName).toHaveBeenCalledWith(4242, "Test ROM");
  });

  it("proceeds with Set* (and logs) after the readiness timeout when the overview never appears", async () => {
    vi.useFakeTimers();
    const setName = vi.fn();
    const getOverview = vi.fn(() => null); // never ready
    const logInfoSpy = vi.spyOn(backend, "logInfo").mockImplementation(() => {});
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: getOverview, allApps: [] });
    vi.stubGlobal("SteamClient", {
      Apps: {
        AddShortcut: vi.fn().mockResolvedValue(7),
        SetShortcutName: setName,
        SetShortcutExe: vi.fn(),
        SetShortcutStartDir: vi.fn(),
        SetAppLaunchOptions: vi.fn(),
        RegisterForAppDetails: vi.fn(() => ({ unregister: vi.fn() })),
      },
    });

    const promise = addShortcut(item(""));
    // Exhaust the 1000ms readiness budget.
    await vi.advanceTimersByTimeAsync(1000);
    const appId = await promise;

    expect(appId).toBe(7);
    // Non-vacuous: the timeout path both proceeds (Set* fired) and logs.
    expect(setName).toHaveBeenCalledWith(7, "Test ROM");
    expect(logInfoSpy).toHaveBeenCalledWith(expect.stringContaining("not ready"));
  });

  it("skips SetAppLaunchOptions and the confirm poll for an empty launch_options (uninstalled ROM)", async () => {
    const setLaunchOptions = vi.fn();
    const registerForAppDetails = vi.fn(() => ({ unregister: vi.fn() }));
    // Overview ready immediately, so no timers are involved.
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(() => ({ appid: 9 }) as SteamAppOverview), allApps: [] });
    vi.stubGlobal("SteamClient", {
      Apps: {
        AddShortcut: vi.fn().mockResolvedValue(9),
        SetShortcutName: vi.fn(),
        SetShortcutExe: vi.fn(),
        SetShortcutStartDir: vi.fn(),
        SetAppLaunchOptions: setLaunchOptions,
        RegisterForAppDetails: registerForAppDetails,
      },
    });

    const appId = await addShortcut(item(""));

    expect(appId).toBe(9);
    // Nothing to write or confirm for an empty command — both are skipped, so
    // the fat-AppDetails-cache hit of the confirm poll is avoided.
    expect(setLaunchOptions).not.toHaveBeenCalled();
    expect(registerForAppDetails).not.toHaveBeenCalled();
  });

  it("takes the confirmed-write path for a non-empty launch_options (installed ROM)", async () => {
    const cmd = 'flatpak run net.retrodeck.retrodeck "/games/x.bin"';
    const setLaunchOptions = vi.fn();
    // RegisterForAppDetails reports the written value back → the confirm matches.
    const registerForAppDetails = vi.fn((_appId: number, cb: (d: SteamAppDetails | undefined) => void) => {
      queueMicrotask(() => cb({ strLaunchOptions: cmd }));
      return { unregister: vi.fn() };
    });
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(() => ({ appid: 55 }) as SteamAppOverview), allApps: [] });
    vi.stubGlobal("SteamClient", {
      Apps: {
        AddShortcut: vi.fn().mockResolvedValue(55),
        SetShortcutName: vi.fn(),
        SetShortcutExe: vi.fn(),
        SetShortcutStartDir: vi.fn(),
        SetAppLaunchOptions: setLaunchOptions,
        RegisterForAppDetails: registerForAppDetails,
      },
    });

    const appId = await addShortcut(item(cmd));

    expect(appId).toBe(55);
    // Confirmed-write path unchanged: SetAppLaunchOptions fired AND the read-back
    // was polled via RegisterForAppDetails.
    expect(setLaunchOptions).toHaveBeenCalledWith(55, cmd);
    expect(registerForAppDetails).toHaveBeenCalledWith(55, expect.any(Function));
  });
});
