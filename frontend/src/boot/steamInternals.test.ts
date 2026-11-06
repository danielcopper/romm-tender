/**
 * The Steam-internals list's drift lock: what `steamInternals.ts` names is what
 * the shipped code reaches, in both directions, and `types/steam.d.ts` declares
 * nothing the code does not reach.
 *
 * What the scan reads is `test-utils/steamReach.ts`'s; what it cannot see is
 * the invariant register's entry in CLAUDE.md.
 */

import { globSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { type SteamReach, codeWords, scanSteamReach } from "../test-utils/steamReach";
import { NOT_STEAM_INTERNALS, STEAM_INTERNALS } from "./steamInternals";

const FRONTEND_DIR = process.cwd();
const SRC_DIR = `${FRONTEND_DIR}/src/`;

let reach: SteamReach;

beforeAll(() => {
  reach = scanSteamReach(FRONTEND_DIR);
}, 120_000);

const typed = STEAM_INTERNALS.filter((entry) => entry.untypedIn === undefined);
const untyped = STEAM_INTERNALS.filter((entry) => entry.untypedIn !== undefined);
const untypedFiles = new Set(untyped.flatMap((entry) => entry.untypedIn ?? []));

const isNotSteams = (path: string): boolean =>
  Object.keys(NOT_STEAM_INTERNALS).some((prefix) => path === prefix || path.startsWith(`${prefix}.`));

const where = (path: string): string => `${path} (${[...(reach.reached.get(path) ?? [])].sort().join(", ")})`;

function shippedSources(): string[] {
  const files = globSync("**/*.{ts,tsx}", { cwd: SRC_DIR })
    .map((relative) => relative.split(/[\\/]/).join("/"))
    .filter((relative) => !/\.(test|spec)\.tsx?$/.test(relative))
    .filter((relative) => !relative.startsWith("test-utils/") && relative !== "test-setup.ts");
  if (files.length === 0) throw new Error(`No sources found under ${SRC_DIR} — a sweep over nothing passes always.`);
  return files;
}

describe("the Steam-internals list", () => {
  it("scans the shipped code rather than nothing", () => {
    expect(reach.sourceFiles).toBeGreaterThan(200);
    expect(reach.reached.size).toBeGreaterThan(50);
  });

  it("finds a route from Steam's roots to every type holding a member it counts", () => {
    expect([...reach.unrouted].sort()).toEqual([]);
  });

  it("names each internal once", () => {
    const paths = STEAM_INTERNALS.map((entry) => entry.path);
    expect(paths.filter((path, index) => paths.indexOf(path) !== index)).toEqual([]);
  });

  it("names every Steam internal the shipped code reaches", () => {
    const listed = new Set(typed.map((entry) => entry.path));
    const unnamed = [...reach.reached.keys()].filter((path) => !listed.has(path) && !isNotSteams(path)).map(where);
    expect(unnamed).toEqual([]);
  });

  it("names no typed internal the code no longer reaches", () => {
    expect(typed.filter((entry) => !reach.reached.has(entry.path)).map((entry) => entry.path)).toEqual([]);
  });

  it("names a reach untyped only where the scan cannot see it", () => {
    expect(untyped.filter((entry) => reach.reached.has(entry.path)).map((entry) => where(entry.path))).toEqual([]);
  });

  it("finds every part of an untyped internal's path in each file that reaches it", () => {
    const missing: string[] = [];
    for (const entry of untyped) {
      for (const file of entry.untypedIn ?? []) {
        const { identifiers, strings } = codeWords(`${SRC_DIR}${file}`);
        for (const part of entry.path.split(/[./()[\]]+/).filter(Boolean)) {
          if (!identifiers.has(part) && !strings.some((text) => text.includes(part))) {
            missing.push(`${entry.path}: "${part}" in ${file}`);
          }
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it("carries no exception nothing needs", () => {
    const reached = [...reach.reached.keys()];
    const unused = Object.keys(NOT_STEAM_INTERNALS).filter(
      (prefix) => !reached.some((path) => path === prefix || path.startsWith(`${prefix}.`)),
    );
    expect(unused).toEqual([]);
  });

  it("finds every member types/steam.d.ts declares reached by the code", () => {
    expect(reach.declared.filter((path) => !reach.reached.has(path))).toEqual([]);
  });

  it("names, for every cast of the window whose target it cannot read, the file it is in", () => {
    expect([...reach.unreadableCastFiles].filter((file) => !untypedFiles.has(file))).toEqual([]);
  });

  it("names, for every route literal, the file it is in", () => {
    // A path handed to `Navigate`, or a literal spelling the loopback host, a
    // `steam://` URL or the route of a game's library page; the list spells the
    // routes it names.
    const spellsARoute = (text: string): boolean =>
      /steamloopback\.host|steam:\/\/|\/library\/app\//.test(text.split("\\").join(""));
    const unnamed = shippedSources().filter((file) => {
      if (file === "boot/steamInternals.ts" || untypedFiles.has(file)) return false;
      const { strings, navigated } = codeWords(`${SRC_DIR}${file}`);
      return navigated.length > 0 || strings.some(spellsARoute);
    });
    expect(unnamed).toEqual([]);
  });
});
