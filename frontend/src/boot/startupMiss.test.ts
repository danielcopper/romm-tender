/**
 * How a start-up check's miss is worded: the backend log's line, and the
 * losses the notice on Main names.
 */

import { describe, expect, it } from "vitest";

import { type MissedName, noticeFeatures, noticedMisses, startupMissLine } from "./startupMiss";

const glyph: MissedName = { name: "ControllerGlyph", absenceCost: "appearance", features: ["buttonPictures"] };
const shortcut: MissedName = {
  name: "SteamClient.Apps.AddShortcut",
  absenceCost: "feature",
  features: ["shortcuts"],
  why: "absent",
};
const dump: MissedName = { name: "playSectionClasses", absenceCost: "diagnostic", features: [] };
const tabs: MissedName = { name: "Tabs", absenceCost: "panel", features: [] };

describe("the warning line a start-up check writes", () => {
  it("names the check, how many of how many missed, and every missing name with its cost and losses", () => {
    expect(startupMissLine("Steam internals", [shortcut, dump], 46)).toBe(
      'start-up check "Steam internals": 2 of 46 missed — SteamClient.Apps.AddShortcut ' +
        "(feature: changes to Steam shortcuts; absent), playSectionClasses (diagnostic).",
    );
  });

  it("leaves the count to the sentence that follows it where that sentence states one", () => {
    expect(startupMissLine("Steam's module registry", [glyph], null)).toBe(
      'start-up check "Steam\'s module registry": missed — ControllerGlyph (appearance: controller button icons).',
    );
  });

  it("is empty where nothing missed", () => {
    expect(startupMissLine("Steam's module registry", [], 35)).toBe("");
  });
});

describe("the misses that reach the notice", () => {
  it("are every miss but one an alternative stands in for", () => {
    const covered: MissedName = { ...shortcut, name: "App.GetServicesInitialized", noticed: false };
    expect(noticedMisses([glyph, covered, dump]).map((missed) => missed.name)).toEqual([
      "ControllerGlyph",
      "playSectionClasses",
    ]);
  });
});

describe("the losses the notice names", () => {
  it("names each loss once, in the table's order rather than the order the names missed in", () => {
    const twice: MissedName = { ...shortcut, name: "SteamClient.Apps.RemoveShortcut" };
    expect(noticeFeatures([glyph, shortcut, twice, dump, tabs])).toEqual([
      "changes to Steam shortcuts",
      "controller button icons",
    ]);
  });
});
