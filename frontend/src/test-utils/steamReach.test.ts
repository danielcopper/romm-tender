/**
 * The scanner's own lock, for the shapes the shipped code carries no site of.
 *
 * Every other shape the scan reads has a site in the shipped code, and
 * `boot/steamInternals.test.ts` fails when the scan stops seeing it. These have
 * none today, so they are pinned over a program of their own.
 */

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { type SteamReach, codeWords, scanSteamReach } from "./steamReach";

let dir: string;
let reach: SteamReach;

beforeAll(() => {
  dir = mkdtempSync(`${tmpdir()}/steam-reach-`);
  mkdirSync(`${dir}/src/types`, { recursive: true });
  writeFileSync(
    `${dir}/tsconfig.json`,
    JSON.stringify({ compilerOptions: { strict: true, lib: ["es2020", "dom"], types: [] }, include: ["src"] }),
  );
  writeFileSync(
    `${dir}/src/types/steam.d.ts`,
    "declare var SteamUIStore: { RunningApps: number[]; SetRunningApp(id: number): void };\n" +
      "declare var SteamClient: { Inner: SteamShared };\n" +
      "interface SteamShared { x: number }\n" +
      "interface SteamOrphan { Lost: number }\n",
  );
  writeFileSync(
    `${dir}/src/reader.ts`,
    "// SteamUIStore.SetRunningApp is named only in this comment.\nexport const { RunningApps } = SteamUIStore;\n",
  );
  writeFileSync(
    `${dir}/src/cast.ts`,
    "interface Base { BFinished(): boolean }\n" +
      "interface Shape extends Base { Other?: number }\n" +
      "export const ready = (window as unknown as Shape).BFinished();\n",
  );
  writeFileSync(
    `${dir}/src/holder.ts`,
    "interface Holder { Shared: SteamShared }\nexport const x = (window as unknown as Holder).Shared.x;\n",
  );
  writeFileSync(`${dir}/src/orphan.ts`, "export const lost = (null as unknown as SteamOrphan).Lost;\n");
  reach = scanSteamReach(dir);
}, 60_000);

afterAll(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("scanSteamReach", () => {
  it("reaches exactly what the code reads: a destructured member, and no member only a comment names", () => {
    expect([...reach.reached.keys()].sort()).toEqual(["BFinished", "Shared", "Shared.x", "SteamUIStore.RunningApps"]);
  });

  it("names a type a cast's property and a global's member both reach after the shorter route", () => {
    expect(reach.reached.has("Shared.x")).toBe(true);
    expect(reach.reached.has("SteamClient.Inner.x")).toBe(false);
  });

  it("counts a member a window cast's shape inherits, named from the global scope", () => {
    expect(reach.reached.has("BFinished")).toBe(true);
  });

  it("marks as callable exactly the reached members declared as something to call", () => {
    expect([...reach.callable].sort()).toEqual(["BFinished"]);
  });

  it("names a type holding a counted member that no route from Steam's roots reaches", () => {
    const { reached, unrouted } = reach;
    expect([...unrouted]).toEqual(["SteamOrphan"]);
    expect([...reached.keys()].some((path) => path.endsWith("Lost"))).toBe(false);
  });
});

describe("codeWords", () => {
  it("leaves out what only a comment spells", () => {
    const { identifiers, strings } = codeWords(`${dir}/src/reader.ts`);
    expect(identifiers.has("RunningApps")).toBe(true);
    expect(identifiers.has("SetRunningApp")).toBe(false);
    expect(strings.some((text) => text.includes("SetRunningApp"))).toBe(false);
  });
});
