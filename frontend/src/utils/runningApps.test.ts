import { describe, it, expect, vi, beforeEach } from "vitest";
import { readRunningApps, isAnyAppHolding } from "./runningApps";

/** A listed entry whose overview reports `status` as its display status. */
const listed = (appid: number, status: unknown) => ({
  appid,
  display_name: `App ${appid}`,
  local_per_client_data: { display_status: status },
});

// The util reads the bare Steam SP global `SteamUIStore`. Each test stubs only
// what it exercises; the global afterEach in test-setup.ts runs
// vi.unstubAllGlobals(), so an unstubbed global reads as truly absent
// (`typeof X === "undefined"`) rather than leaking across tests.

describe("runningApps — guarded SteamUIStore reader", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("readRunningApps", () => {
    it("reads the running apps the store reports", () => {
      vi.stubGlobal("SteamUIStore", {
        RunningApps: [{ appid: 42, display_name: "Zelda", local_per_client_data: { display_status: 4 } }],
      });

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([{ appid: 42, display_name: "Zelda" }]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=[42:4]");
    });

    it("reports every running app, in store order, without reordering", () => {
      // The reader passes the store's order through untouched. That order is
      // not a launch order — nothing reads the head as "the app that just
      // started" — but the reader must not invent an order of its own either.
      vi.stubGlobal("SteamUIStore", {
        RunningApps: [
          { appid: 100, display_name: "Foreground", local_per_client_data: { display_status: 4 } },
          { appid: 200, display_name: "Background", local_per_client_data: { display_status: 4 } },
        ],
      });

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([
        { appid: 100, display_name: "Foreground" },
        { appid: 200, display_name: "Background" },
      ]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=[100:4,200:4]");
    });

    it("falls back to strDisplayName when display_name is absent", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: [{ appid: 6, strDisplayName: "B" }] });

      expect(readRunningApps().apps).toEqual([{ appid: 6, display_name: "B" }]);
    });

    it("keeps a nameless entry with an empty display_name rather than dropping it", () => {
      // The appid is what every consumer matches on; a missing name must not
      // make a genuinely running app invisible.
      vi.stubGlobal("SteamUIStore", { RunningApps: [{ appid: 8 }] });

      expect(readRunningApps().apps).toEqual([{ appid: 8, display_name: "" }]);
    });

    it("reports an empty list as empty — a running game may still be up (post-restart window)", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: [] });

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=empty");
    });

    it("reports absent when the store exposes no RunningApps property", () => {
      vi.stubGlobal("SteamUIStore", { SetRunningApp: vi.fn() });

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=absent");
    });

    it("reports no-store when the global is absent, without throwing", () => {
      // SteamUIStore intentionally not stubbed — a bare `=== undefined` would
      // throw ReferenceError; the util's typeof guard degrades to a note instead.
      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=no-store");
    });

    it("reports no-store when the global is null", () => {
      vi.stubGlobal("SteamUIStore", null);

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=no-store");
    });

    it("tolerates a throwing RunningApps getter and names it in the diagnostics", () => {
      vi.stubGlobal("SteamUIStore", {
        get RunningApps(): unknown {
          throw new Error("bridge fault");
        },
      });

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([]);
      expect(diagnostics).toContain("SteamUIStore.RunningApps=threw:");
      expect(diagnostics).toContain("bridge fault");
    });

    it("drops entries without a numeric appid and reports an empty list", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: [{ display_name: "no id" }, 42, null] });

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=empty");
    });

    it("coerces a non-array iterable (MobX-style observable)", () => {
      const observable = {
        *[Symbol.iterator]() {
          yield { appid: 11, display_name: "Obs", local_per_client_data: { display_status: 4 } };
        },
      };
      vi.stubGlobal("SteamUIStore", { RunningApps: observable });

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([{ appid: 11, display_name: "Obs" }]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=[11:4]");
    });

    it("counts an entry only while its display status reads Running, and names every entry's status", () => {
      // 11 ReadyToLaunch: listed after its exit; 1 Launching: listed by Steam's
      // own Play before the start is reported; 4 Running.
      vi.stubGlobal("SteamUIStore", { RunningApps: [listed(1, 11), listed(2, 1), listed(3, 4)] });

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([{ appid: 3, display_name: "App 3" }]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=[1:11,2:1,3:4]");
    });

    it("names an entry whose display status cannot be read with a question mark", () => {
      vi.stubGlobal("SteamUIStore", {
        RunningApps: [
          listed(1, "4"),
          { appid: 2, display_name: "No client data" },
          {
            appid: 3,
            display_name: "Throws",
            get local_per_client_data(): unknown {
              throw new Error("gone");
            },
          },
        ],
      });

      expect(readRunningApps().diagnostics).toBe("SteamUIStore.RunningApps=[1:?,2:?,3:?]");
    });

    it("counts an entry whose display status cannot be read as running", () => {
      // A Steam build that moved the field falls back to the list as listed,
      // never to "nothing runs".
      vi.stubGlobal("SteamUIStore", { RunningApps: [listed(1, undefined), { appid: 2, display_name: "No data" }] });

      expect(readRunningApps().apps.map((app) => app.appid)).toEqual([1, 2]);
    });

    it("names the apps that count only because their status could not be read", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: [listed(1, 4), listed(2, undefined), listed(3, 11)] });

      expect([...readRunningApps().statusUnread]).toEqual([2]);
    });

    it("reports empty for a present but non-list value", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: 7 as unknown as SteamAppOverview[] });

      const { apps, diagnostics } = readRunningApps();
      expect(apps).toEqual([]);
      expect(diagnostics).toBe("SteamUIStore.RunningApps=empty");
    });
  });

  describe("isAnyAppHolding", () => {
    it("is true while a listed app reads Running", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: [listed(100, 4)] });

      expect(isAnyAppHolding()).toBe(true);
    });

    it("is true while a listed app reads Launching or Terminating", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: [listed(100, 1)] });
      expect(isAnyAppHolding()).toBe(true);

      vi.stubGlobal("SteamUIStore", { RunningApps: [listed(100, 36)] });
      expect(isAnyAppHolding()).toBe(true);
    });

    it("is true while a listed app's display status cannot be read", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: [{ appid: 100, display_name: "Game" }] });

      expect(isAnyAppHolding()).toBe(true);
    });

    it("is false when the store reports an empty list", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: [] });

      expect(isAnyAppHolding()).toBe(false);
    });

    it("is false when the only entry is one Steam kept listed after its exit", () => {
      vi.stubGlobal("SteamUIStore", { RunningApps: [listed(100, 11)] });

      expect(isAnyAppHolding()).toBe(false);
    });

    it("is false and does not throw when the store is absent", () => {
      expect(isAnyAppHolding()).toBe(false);
    });
  });
});
