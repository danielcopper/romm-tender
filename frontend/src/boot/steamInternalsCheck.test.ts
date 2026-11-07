/**
 * What the start-up check over the Steam internals asks, and what it reports.
 *
 * Every case hands the check a list and a global scope of its own, so what is
 * asked is exactly what the case names; whether the shipped list is complete is
 * `steamInternals.test.ts`'s question.
 */

import { describe, expect, it, vi } from "vitest";

import type { SteamInternal } from "./steamInternals";
import { type ReadPath, checkSteamInternals, readGlobalPath, staticPart } from "./steamInternalsCheck";

/** A reader over *scope* rather than over the real global scope, the way the check reads it. */
const over =
  (scope: Record<string, unknown>): ReadPath =>
  (segments) => {
    let value: unknown = scope;
    for (const segment of segments) {
      if (typeof value !== "object" || value === null || !(segment in value)) return { there: false, value };
      value = (value as Record<string, unknown>)[segment];
      if (value === undefined) return { there: false, value };
    }
    return { there: true, value };
  };

const called = (path: string, devBuildOnly?: true): SteamInternal => ({
  path,
  asked: "call",
  absenceCost: "feature",
  features: ["shortcuts"],
  ...(devBuildOnly ? { devBuildOnly } : {}),
});

const read = (path: string): SteamInternal => ({
  path,
  asked: "read",
  absenceCost: "feature",
  features: ["collections"],
});

const names = (entries: readonly SteamInternal[], scope: Record<string, unknown>): string[] =>
  checkSteamInternals(entries, { desktopSurface: true, read: over(scope) }).missed.map(
    (missed) => `${missed.name}: ${missed.why ?? ""}${missed.noticed === false ? " (no notice)" : ""}`,
  );

describe("the static part of a path", () => {
  it("is the path itself where nothing follows a member", () => {
    expect(staticPart("SteamClient.Apps.AddShortcut")).toEqual(["SteamClient", "Apps", "AddShortcut"]);
  });

  it("ends at the first call, element or parameter", () => {
    expect(staticPart("appStore.GetAppOverviewByAppID().display_name")).toEqual(["appStore", "GetAppOverviewByAppID"]);
    expect(staticPart("collectionStore.userCollections[].Save")).toEqual(["collectionStore", "userCollections"]);
    expect(staticPart("SteamClient.Apps.RegisterForAppDetails(callback)(details).strShortcutExe")).toEqual([
      "SteamClient",
      "Apps",
      "RegisterForAppDetails",
    ]);
  });
});

describe("what the Steam-internals check reports", () => {
  it("reports an internal the code calls that is absent, and one that is there but no function", () => {
    const entries = [called("SteamClient.Apps.AddShortcut"), called("SteamClient.Apps.RunGame")];
    expect(names(entries, { SteamClient: { Apps: { RunGame: 42 } } })).toEqual([
      "SteamClient.Apps.AddShortcut: absent",
      "SteamClient.Apps.RunGame: not a function",
    ]);
  });

  it("reports nothing for a function where the code calls one, or a value where it reads one", () => {
    const entries = [called("SteamClient.Apps.AddShortcut"), read("collectionStore.userCollections")];
    const scope = { SteamClient: { Apps: { AddShortcut: () => 1 } }, collectionStore: { userCollections: [] } };
    expect(names(entries, scope)).toEqual([]);
  });

  it("asks for no function where the code only reads the value", () => {
    expect(
      names([read("NotificationStore.m_nNextTestNotificationID")], {
        NotificationStore: { m_nNextTestNotificationID: 7 },
      }),
    ).toEqual([]);
  });

  it("asks a member of what a call answers by the call, which must be a function", () => {
    const entries = [called("appStore.GetAppOverviewByAppID().display_name")];
    expect(names(entries, { appStore: { GetAppOverviewByAppID: {} } })).toEqual([
      "appStore.GetAppOverviewByAppID: not a function",
    ]);
  });

  it("asks an element's member by the holder, which must be there", () => {
    const entries = [read("collectionStore.userCollections[].Save")];
    expect(names(entries, { collectionStore: {} })).toEqual(["collectionStore.userCollections: absent"]);
    expect(names(entries, { collectionStore: { userCollections: [] } })).toEqual([]);
  });

  it("reports a static part once, with the highest cost and every loss of the entries resting on it", () => {
    const entries: SteamInternal[] = [
      { path: "appStore.GetCustomHeroImageURLs", asked: "call", absenceCost: "appearance", features: ["thumbnails"] },
      { path: "appStore.GetCustomHeroImageURLs().x", asked: "call", absenceCost: "feature", features: ["artwork"] },
      { path: "appStore.GetCustomHeroImageURLs().y", asked: "call", absenceCost: "diagnostic", features: [] },
    ];
    const report = checkSteamInternals(entries, { desktopSurface: true, read: over({ appStore: {} }) });
    expect(report.asked).toBe(1);
    expect(report.missed).toEqual([
      {
        name: "appStore.GetCustomHeroImageURLs",
        absenceCost: "feature",
        features: ["artwork", "thumbnails"],
        why: "absent",
      },
    ]);
  });

  it("never looks up an entry nothing can be asked about at start-up", () => {
    const lookup = vi.fn(over({}));
    const entries: SteamInternal[] = [
      { path: "/apprunning", asked: { notAtStartUp: "a route" }, absenceCost: "feature", features: ["resume"] },
    ];
    const report = checkSteamInternals(entries, { desktopSurface: true, read: lookup });
    expect(lookup).not.toHaveBeenCalled();
    expect(report).toEqual({ missed: [], asked: 0 });
  });

  it("asks the dev build's entries only where the build carries the desktop surface", () => {
    const entries = [called("g_PopupManager.GetPopups", true)];
    expect(checkSteamInternals(entries, { desktopSurface: false, read: over({}) })).toEqual({ missed: [], asked: 0 });
    expect(checkSteamInternals(entries, { desktopSurface: true, read: over({}) }).missed).toHaveLength(1);
  });
});

describe("alternatives the code falls back between", () => {
  const either = (path: string, oneOf = "services initialised"): SteamInternal => ({ ...called(path), oneOf });

  it("logs a member missing while its alternative is there under its own name, and raises no notice for it", () => {
    const entries = [either("App.WaitForServicesInitialized"), either("App.GetServicesInitialized")];
    expect(names(entries, { App: { GetServicesInitialized: () => true } })).toEqual([
      "App.WaitForServicesInitialized: absent; its alternative App.GetServicesInitialized is there (no notice)",
    ]);
    expect(names(entries, { App: { WaitForServicesInitialized: () => Promise.resolve() } })).toEqual([
      "App.GetServicesInitialized: absent; its alternative App.WaitForServicesInitialized is there (no notice)",
    ]);
  });

  it("reports nothing where every alternative is there", () => {
    const entries = [either("App.WaitForServicesInitialized"), either("App.GetServicesInitialized")];
    const scope = { App: { WaitForServicesInitialized: () => Promise.resolve(), GetServicesInitialized: () => true } };
    expect(names(entries, scope)).toEqual([]);
  });

  it("reports the group once, naming every alternative, where none of them is there", () => {
    const entries = [either("App.WaitForServicesInitialized"), either("App.GetServicesInitialized")];
    expect(names(entries, { App: { GetServicesInitialized: 1 } })).toEqual([
      "App.WaitForServicesInitialized or App.GetServicesInitialized: absent, not a function",
    ]);
  });

  it("logs a member missing beside an alternative that cannot be asked at start-up, and raises no notice for it", () => {
    const entries: SteamInternal[] = [
      { ...called("SteamUIStore.NavigateToRunningApp"), oneOf: "resume navigation" },
      {
        path: "/apprunning",
        asked: { notAtStartUp: "a route" },
        absenceCost: "feature",
        features: ["resume"],
        oneOf: "resume navigation",
      },
    ];
    expect(names(entries, {})).toEqual([
      "SteamUIStore.NavigateToRunningApp: absent; its alternative /apprunning cannot be asked at start-up (no notice)",
    ]);
  });

  it("never pulls an entry outside a group into one on a static part a group's entry shares", () => {
    const entries: SteamInternal[] = [
      either("App.GetServicesInitialized"),
      either("App.WaitForServicesInitialized"),
      { path: "App.GetServicesInitialized().x", asked: "call", absenceCost: "feature", features: ["library"] },
    ];
    expect(names(entries, { App: { WaitForServicesInitialized: () => Promise.resolve() } })).toEqual([
      "App.GetServicesInitialized: absent; its alternative App.WaitForServicesInitialized is there (no notice)",
      "App.GetServicesInitialized: absent",
    ]);
  });

  it("keeps a group's place in the order the list first names it", () => {
    const entries = [
      called("SteamClient.Apps.AddShortcut"),
      either("App.WaitForServicesInitialized"),
      either("App.GetServicesInitialized"),
      called("SteamClient.Apps.RunGame"),
    ];
    expect(names(entries, {}).map((line) => line.split(":")[0])).toEqual([
      "SteamClient.Apps.AddShortcut",
      "App.WaitForServicesInitialized or App.GetServicesInitialized",
      "SteamClient.Apps.RunGame",
    ]);
  });
});

describe("the reader of the global scope", () => {
  it("finds what the global scope holds, and nothing it does not", () => {
    vi.stubGlobal("SteamClient", { Apps: { AddShortcut: () => 1, Gone: undefined } });
    expect(readGlobalPath(["SteamClient", "Apps", "AddShortcut"]).there).toBe(true);
    expect(readGlobalPath(["SteamClient", "Apps", "Gone"]).there).toBe(false);
    expect(readGlobalPath(["SteamClient", "Apps", "Missing"]).there).toBe(false);
    expect(readGlobalPath(["SteamClient", "Apps", "AddShortcut", "deeper"]).there).toBe(false);
    expect(readGlobalPath(["NoSuchGlobalOfSteams"]).there).toBe(false);
  });

  it("counts a member whose holder is null as absent", () => {
    vi.stubGlobal("SteamUIStore", null);
    expect(readGlobalPath(["SteamUIStore", "RunningApps"]).there).toBe(false);
  });

  it("counts a getter that throws as absent rather than ending the check", () => {
    vi.stubGlobal(
      "appStore",
      Object.defineProperty({}, "GetAppOverviewByAppID", {
        get() {
          throw new Error("boom");
        },
      }),
    );
    expect(readGlobalPath(["appStore", "GetAppOverviewByAppID"]).there).toBe(false);
  });

  it("finds a member a prototype holds, as a store's methods are", () => {
    class Store {
      GetAppOverviewByAppID(): null {
        return null;
      }
    }
    vi.stubGlobal("appStore", new Store());
    const found = readGlobalPath(["appStore", "GetAppOverviewByAppID"]);
    expect(found.there).toBe(true);
    expect(typeof found.value).toBe("function");
  });
});
