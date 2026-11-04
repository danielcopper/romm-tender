/**
 * The scanner's own lock, for the shapes the shipped code carries no site of.
 *
 * Every other shape the scan reads has a site in the shipped code, and
 * `boot/steamInternals.test.ts` fails when the scan stops seeing it. These two
 * have none today, so they are pinned over a program of their own.
 */

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { codeWords, scanSteamReach } from "./steamReach";

let dir: string;

beforeAll(() => {
  dir = mkdtempSync(`${tmpdir()}/steam-reach-`);
  mkdirSync(`${dir}/src/types`, { recursive: true });
  writeFileSync(
    `${dir}/tsconfig.json`,
    JSON.stringify({ compilerOptions: { strict: true, lib: ["es2020"], types: [] }, include: ["src"] }),
  );
  writeFileSync(
    `${dir}/src/types/steam.d.ts`,
    "declare var SteamUIStore: { RunningApps: number[]; SetRunningApp(id: number): void };\n",
  );
  writeFileSync(
    `${dir}/src/reader.ts`,
    "// SteamUIStore.SetRunningApp is named only in this comment.\nexport const { RunningApps } = SteamUIStore;\n",
  );
});

afterAll(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("scanSteamReach", () => {
  it("reads a destructured member as a reach, and a member only a comment names as none", () => {
    const { reached } = scanSteamReach(dir);
    expect([...reached.keys()]).toEqual(["SteamUIStore.RunningApps"]);
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
