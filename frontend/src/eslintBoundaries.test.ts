/**
 * Proves the direction rules in `eslint.config.js` actually report.
 *
 * No source twin on purpose: what this guards is the lint config, and
 * specifically that a boundary rule can be present, correctly configured, and
 * still completely inert. A green lint run is indistinguishable from a working
 * one; only a known-bad fixture tells the two apart. How `no-cycle` goes inert
 * is on the comment at `import-x/extensions` in `eslint.config.js`.
 *
 * No fixture is written into `src/`, where every other sweep of the tree would
 * race it. The restricted-path fixtures are linted as text at a path under the
 * real `src/`, because the rules are scoped by path and `no-restricted-paths`
 * resolves its zones against `process.cwd()`, not ESLint's `cwd` option
 * (`lib/rules/no-restricted-paths.js` in eslint-plugin-import-x). What they
 * import must exist, because `no-restricted-paths` skips an import it cannot
 * resolve: `desktop/` holds no module, so the fixtures reaching into it import
 * its README, the one file it does hold.
 *
 * `no-cycle` opens the imported file from disk to read ITS imports, so the cycle
 * pair cannot be text. It is written into a throwaway project under the OS temp
 * directory and linted there with this package's real config file.
 *
 * Type-aware parsing is off for every fixture, since the project service refuses
 * a file its tsconfig does not hold — a text that is not on disk, or the cycle
 * pair outside this package — and none of the rules under test reads a type.
 */

import { ESLint } from "eslint";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { version as reactVersion } from "react";
import tseslint from "typescript-eslint";

const SRC = path.join(process.cwd(), "src");

const eslint = new ESLint({ cwd: process.cwd(), overrideConfig: tseslint.configs.disableTypeChecked });

const REACHES_BIGPICTURE =
  'import { showStopGameModal } from "../../bigpicture/StopGameModal";\nexport const probe = showStopGameModal;\n';
const REACHES_DESKTOP = 'import readme from "../../desktop/README.md";\nexport const probe = readme;\n';
const REACHES_SHARED =
  'import { showCoreChangeModal } from "../../shared/CoreChangeModal";\nexport const probe = showCoreChangeModal;\n';

function ruleIds(results: ESLint.LintResult[]): string[] {
  return results.flatMap((r) => r.messages.map((m) => m.ruleId ?? "<fatal>"));
}

/** Rule IDs reported for `source` linted as the file at `relative` under `src/`. */
async function rulesReportedFor(relative: string, source: string): Promise<string[]> {
  return ruleIds(await eslint.lintText(source, { filePath: path.join(SRC, relative) }));
}

/** Rule IDs reported for one half of a two-module cycle under a throwaway `src/utils/`. */
async function rulesReportedForCycle(): Promise<string[]> {
  const root = await mkdtemp(path.join(os.tmpdir(), "tender-eslint-cycle-"));
  try {
    const utils = path.join(root, "src", "utils");
    await mkdir(utils, { recursive: true });
    await writeFile(
      path.join(utils, "cycleA.ts"),
      'import { bee } from "./cycleB";\nexport const ay = (): number => bee() + 1;\n',
      "utf8",
    );
    await writeFile(
      path.join(utils, "cycleB.ts"),
      'import { ay } from "./cycleA";\nexport const bee = (): number => (Math.random() > 2 ? ay() : 0);\n',
      "utf8",
    );
    // The config's `react.version: "detect"` resolves `react` from the linted
    // file's directory (eslint-plugin-react `lib/util/version.js`), which this
    // tmp dir cannot, and the plugin's warning would fail the test.
    const cycleLinter = new ESLint({
      cwd: root,
      overrideConfigFile: path.join(process.cwd(), "eslint.config.js"),
      overrideConfig: [tseslint.configs.disableTypeChecked, { settings: { react: { version: reactVersion } } }],
    });
    return ruleIds(await cycleLinter.lintFiles([path.join(utils, "cycleA.ts")]));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

describe("frontend direction rules", () => {
  it("reports utils/ reaching up into bigpicture/", async () => {
    expect(await rulesReportedFor("utils/__eslint_fixtures__/reachesUp.ts", REACHES_BIGPICTURE)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports utils/ reaching up into desktop/", async () => {
    expect(await rulesReportedFor("utils/__eslint_fixtures__/reachesDesktop.ts", REACHES_DESKTOP)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports api/ reaching into bigpicture/", async () => {
    expect(await rulesReportedFor("api/__eslint_fixtures__/reachesUp.ts", REACHES_BIGPICTURE)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports api/ reaching into desktop/", async () => {
    expect(await rulesReportedFor("api/__eslint_fixtures__/reachesDesktop.ts", REACHES_DESKTOP)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports bigpicture/ reaching sideways into desktop/", async () => {
    expect(await rulesReportedFor("bigpicture/__eslint_fixtures__/reachesSideways.ts", REACHES_DESKTOP)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports desktop/ reaching sideways into bigpicture/", async () => {
    expect(await rulesReportedFor("desktop/__eslint_fixtures__/reachesSideways.ts", REACHES_BIGPICTURE)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports shared/ reaching up into bigpicture/", async () => {
    expect(await rulesReportedFor("shared/__eslint_fixtures__/reachesUp.ts", REACHES_BIGPICTURE)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports shared/ reaching up into desktop/", async () => {
    expect(await rulesReportedFor("shared/__eslint_fixtures__/reachesDesktop.ts", REACHES_DESKTOP)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports utils/ reaching up into shared/", async () => {
    expect(await rulesReportedFor("utils/__eslint_fixtures__/reachesShared.ts", REACHES_SHARED)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("reports api/ reaching into shared/", async () => {
    expect(await rulesReportedFor("api/__eslint_fixtures__/reachesShared.ts", REACHES_SHARED)).toContain(
      "import-x/no-restricted-paths",
    );
  });

  it("leaves bigpicture/ reaching down into shared/ alone", async () => {
    expect(await rulesReportedFor("bigpicture/__eslint_fixtures__/reachesDown.ts", REACHES_SHARED)).toEqual([]);
  });

  it("leaves desktop/ reaching down into shared/ alone", async () => {
    expect(await rulesReportedFor("desktop/__eslint_fixtures__/reachesDown.ts", REACHES_SHARED)).toEqual([]);
  });

  it("reports a dependency cycle between two .ts modules", async () => {
    expect(await rulesReportedForCycle()).toContain("import-x/no-cycle");
  });

  it("leaves a compliant module alone", async () => {
    const results = await new ESLint({ cwd: process.cwd() }).lintFiles([path.join(SRC, "utils", "detach.ts")]);
    expect(ruleIds(results)).toEqual([]);
  });
}, 60_000);
