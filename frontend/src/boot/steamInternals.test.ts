/**
 * The Steam-internals list's drift lock: what `steamInternals.ts` names is what
 * the shipped code reaches, in both directions, and `types/steam.d.ts` declares
 * nothing the code does not reach.
 *
 * What the scan reads is `test-utils/steamReach.ts`'s; what it cannot see is
 * the invariant register's entry in `docs/architecture/invariants/startup.md`.
 */

import { globSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { type SteamReach, codeWords, scanSteamReach } from "../test-utils/steamReach";
import { NOT_STEAM_INTERNALS, STEAM_FEATURES, STEAM_INTERNALS } from "./steamInternals";
import { staticPart } from "./steamInternalsCheck";
import { STEAM_LOOKUPS } from "./steamModules";

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

describe("what the start-up check asks of each Steam internal", () => {
  /** What follows an entry's static part: a call, an element, or nothing. */
  const after = (path: string): string =>
    path.slice(staticPart(path).join(".").length, staticPart(path).join(".").length + 1);

  it("costs the panel for none of them", () => {
    // The type forbids it already; this says so for a list read without the
    // compiler, as cut 3's script reads it (#2509 D3).
    expect(STEAM_INTERNALS.filter((entry) => (entry.absenceCost as string) === "panel")).toEqual([]);
  });

  it("asks a member of what a call answers as a call, and a member of an element as a value", () => {
    const wrong = STEAM_INTERNALS.filter(
      (entry) =>
        (after(entry.path) === "(" && entry.asked !== "call") || (after(entry.path) === "[" && entry.asked !== "read"),
    );
    expect(wrong.map((entry) => entry.path)).toEqual([]);
  });

  it("asks as a call exactly the typed internals declared as something to call", () => {
    // Where the path ends at its static part, the declaration says whether the
    // code calls it; a path naming what a call answers is held above.
    const wrong = typed
      .filter((entry) => after(entry.path) === "" && typeof entry.asked === "string")
      .filter((entry) => (entry.asked === "call") !== reach.callable.has(entry.path));
    expect(wrong.map((entry) => `${entry.path}: ${String(entry.asked)}`)).toEqual([]);
  });

  it("says why, for every internal it does not ask at start-up", () => {
    const unexplained = STEAM_INTERNALS.filter(
      (entry) => typeof entry.asked !== "string" && entry.asked.notAtStartUp.trim() === "",
    );
    expect(unexplained).toEqual([]);
  });

  it("marks as the dev build's exactly the internals only the desktop surface reaches", () => {
    const filesOf = (entry: (typeof STEAM_INTERNALS)[number]): string[] => [
      ...(entry.untypedIn ?? []),
      ...(reach.reached.get(entry.path) ?? []),
    ];
    const wrong = STEAM_INTERNALS.filter((entry) => {
      const files = filesOf(entry);
      const onlyDesktop = files.length > 0 && files.every((file) => file.startsWith("desktop/"));
      return onlyDesktop !== (entry.devBuildOnly === true);
    });
    expect(wrong.map((entry) => entry.path)).toEqual([]);
  });

  it("names a loss for every internal whose absence the reader sees, and none for a diagnostic one", () => {
    const wrong = STEAM_INTERNALS.filter(
      (entry) => (entry.absenceCost === "diagnostic") !== (entry.features.length === 0),
    );
    expect(wrong.map((entry) => entry.path)).toEqual([]);
  });

  it("groups alternatives only in twos or more, so no group stands for one internal", () => {
    const sizes = new Map<string, number>();
    for (const entry of STEAM_INTERNALS) {
      if (entry.oneOf !== undefined) sizes.set(entry.oneOf, (sizes.get(entry.oneOf) ?? 0) + 1);
    }
    expect(sizes.size).toBeGreaterThan(0);
    expect([...sizes].filter(([, size]) => size < 2)).toEqual([]);
  });

  it("uses every loss it can name, in one list or the other", () => {
    const used = new Set([
      ...STEAM_INTERNALS.flatMap((entry) => entry.features),
      ...STEAM_LOOKUPS.flatMap((lookup) => lookup.features ?? []),
    ]);
    expect(Object.keys(STEAM_FEATURES).filter((feature) => !used.has(feature as keyof typeof STEAM_FEATURES))).toEqual(
      [],
    );
  });
});
