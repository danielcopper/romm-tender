/**
 * Proves the direction rules in `eslint.config.js` actually report.
 *
 * No source twin on purpose: what this guards is the lint config, and
 * specifically that a boundary rule can be present, correctly configured, and
 * still completely inert. `import-x/extensions` ships as `['.js']` — until it
 * names `.ts`/`.tsx` the plugin resolves an import but never opens the target to
 * read ITS imports, so `no-cycle` walks a graph one edge deep and reports
 * nothing, on any codebase, forever. A green lint run is indistinguishable from
 * a working one; only a known-bad fixture tells the two apart.
 *
 * Fixtures live under the very directories the rules name, because the rules are
 * scoped by path, and are removed again afterwards so `pnpm lint` never sees
 * them. Every fixture that reaches into `desktop/` comes with a module planted
 * there to import — from `bigpicture/`, from `utils/` and from `api/` — because
 * `no-restricted-paths` skips an import it cannot resolve, and that directory
 * holds no module of its own, so a fixture pointing at it would otherwise pass
 * for the wrong reason. The twin planted under `bigpicture/` is not needed for
 * that — that directory is full of real modules — but for symmetry: the two
 * sideways fixtures then import the same shape in both directions, and neither
 * result turns on which real module it happened to name.
 */

import { ESLint } from "eslint";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const SRC = path.join(process.cwd(), "frontend", "src");
const UTILS_DIR = path.join(SRC, "utils", "__eslint_fixtures__");
const API_DIR = path.join(SRC, "api", "__eslint_fixtures__");
// Not `__eslint_fixtures__`: eslintQamFocusable.test.ts owns a directory of that
// name directly under `bigpicture/`, and the two files run in parallel workers.
const BIGPICTURE_DIR = path.join(SRC, "bigpicture", "__eslint_surface_fixtures__");
const DESKTOP_DIR = path.join(SRC, "desktop", "__eslint_surface_fixtures__");

const FIXTURE_DIRS = [UTILS_DIR, API_DIR, BIGPICTURE_DIR, DESKTOP_DIR];

const FIXTURES: [dir: string, name: string, source: string][] = [
  [
    UTILS_DIR,
    "reachesUp.ts",
    'import { showCoreChangeModal } from "../../bigpicture/CoreChangeModal";\nexport const probe = showCoreChangeModal;\n',
  ],
  [
    API_DIR,
    "reachesUp.ts",
    'import { showCoreChangeModal } from "../../bigpicture/CoreChangeModal";\nexport const probe = showCoreChangeModal;\n',
  ],
  [
    UTILS_DIR,
    "reachesDesktop.ts",
    'import { surfaceProbe } from "../../desktop/__eslint_surface_fixtures__/surfaceModule";\nexport const probe = surfaceProbe;\n',
  ],
  [
    API_DIR,
    "reachesDesktop.ts",
    'import { surfaceProbe } from "../../desktop/__eslint_surface_fixtures__/surfaceModule";\nexport const probe = surfaceProbe;\n',
  ],
  [UTILS_DIR, "cycleA.ts", 'import { bee } from "./cycleB";\nexport const ay = (): number => bee() + 1;\n'],
  [
    UTILS_DIR,
    "cycleB.ts",
    'import { ay } from "./cycleA";\nexport const bee = (): number => (Math.random() > 2 ? ay() : 0);\n',
  ],
  [BIGPICTURE_DIR, "surfaceModule.ts", "export const surfaceProbe = 1;\n"],
  [DESKTOP_DIR, "surfaceModule.ts", "export const surfaceProbe = 1;\n"],
  [
    BIGPICTURE_DIR,
    "reachesSideways.ts",
    'import { surfaceProbe } from "../../desktop/__eslint_surface_fixtures__/surfaceModule";\nexport const probe = surfaceProbe;\n',
  ],
  [
    DESKTOP_DIR,
    "reachesSideways.ts",
    'import { surfaceProbe } from "../../bigpicture/__eslint_surface_fixtures__/surfaceModule";\nexport const probe = surfaceProbe;\n',
  ],
];

/** Rule IDs reported for `file`, using the repository's real ESLint config. */
async function rulesReportedFor(file: string): Promise<string[]> {
  const results = await new ESLint({ cwd: process.cwd() }).lintFiles([file]);
  return results.flatMap((r) => r.messages.map((m) => m.ruleId ?? "<fatal>"));
}

describe("frontend direction rules", () => {
  beforeAll(async () => {
    await Promise.all(FIXTURE_DIRS.map((dir) => mkdir(dir, { recursive: true })));
    await Promise.all(FIXTURES.map(([dir, name, source]) => writeFile(path.join(dir, name), source, "utf8")));
  });

  afterAll(async () => {
    await Promise.all(FIXTURE_DIRS.map((dir) => rm(dir, { recursive: true, force: true })));
  });

  it("reports utils/ reaching up into bigpicture/", async () => {
    expect(await rulesReportedFor(path.join(UTILS_DIR, "reachesUp.ts"))).toContain("import-x/no-restricted-paths");
  });

  it("reports utils/ reaching up into desktop/", async () => {
    expect(await rulesReportedFor(path.join(UTILS_DIR, "reachesDesktop.ts"))).toContain("import-x/no-restricted-paths");
  });

  it("reports api/ reaching into bigpicture/", async () => {
    expect(await rulesReportedFor(path.join(API_DIR, "reachesUp.ts"))).toContain("import-x/no-restricted-paths");
  });

  it("reports api/ reaching into desktop/", async () => {
    expect(await rulesReportedFor(path.join(API_DIR, "reachesDesktop.ts"))).toContain("import-x/no-restricted-paths");
  });

  it("reports bigpicture/ reaching sideways into desktop/", async () => {
    expect(await rulesReportedFor(path.join(BIGPICTURE_DIR, "reachesSideways.ts"))).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports desktop/ reaching sideways into bigpicture/", async () => {
    expect(await rulesReportedFor(path.join(DESKTOP_DIR, "reachesSideways.ts"))).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports a dependency cycle between two .ts modules", async () => {
    expect(await rulesReportedFor(path.join(UTILS_DIR, "cycleA.ts"))).toContain("import-x/no-cycle");
  });

  it("leaves a compliant module alone", async () => {
    expect(await rulesReportedFor(path.join(SRC, "utils", "detach.ts"))).toEqual([]);
  });
}, 60_000);
