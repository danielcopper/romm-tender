import { execFileSync } from "node:child_process";

import { describe, expect, it } from "vitest";

import { STEAM_FEATURES, STEAM_INTERNALS } from "../src/boot/steamInternals.ts";
import { panelHalf } from "./steam-internals.mjs";

describe("the panel's half as the watch over Steam's client reads it", () => {
  it("carries every entry, once, by its path", () => {
    expect(panelHalf().map((entry) => entry.path)).toEqual(STEAM_INTERNALS.map((entry) => entry.path));
  });

  it("carries an entry's group, exemption and build, and its losses in the notice's words", () => {
    const byPath = new Map(panelHalf().map((entry) => [entry.path, entry]));
    expect(byPath.get("/library/app/")).toEqual({
      path: "/library/app/",
      oneOf: null,
      notSpelledBySteam: "Steam's router builds it in a template from `/library` and `/app/` and never spells it whole",
      devBuildOnly: true,
      losses: [STEAM_FEATURES.desktop],
    });
    expect(byPath.get("SteamUIStore.NavigateToRunningApp")).toEqual({
      path: "SteamUIStore.NavigateToRunningApp",
      oneOf: "resume navigation",
      notSpelledBySteam: null,
      devBuildOnly: false,
      losses: ["Resume for a running game"],
    });
    expect(byPath.get("SteamClient.Apps.RegisterForAppDetails")?.losses).toEqual([
      "starting a game",
      "changes to Steam shortcuts",
    ]);
  });

  it("prints it as JSON when Node runs the file", () => {
    // The Vitest root, this package's directory: in this suite `import.meta.url` is no file URL.
    const script = `${process.cwd()}/scripts/steam-internals.mjs`;
    const printed = execFileSync(process.execPath, [script], { encoding: "utf8" });
    expect(JSON.parse(printed)).toEqual(panelHalf());
  });
});
