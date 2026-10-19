/**
 * Proves the rule in `eslint.config.js` that no code under `src/desktop/` names
 * a minified class hash actually reports, and reports nothing it should not.
 *
 * No source twin on purpose, for the reason `eslintBoundaries.test.ts` gives:
 * a green lint run cannot tell a working rule from an inert one. The fixtures
 * are linted as text at paths in this package; type-aware parsing is off
 * because the project service refuses a file that is not on disk, and the rule
 * reads no type.
 */

import { ESLint } from "eslint";
import path from "node:path";
import process from "node:process";
import tseslint from "typescript-eslint";

const eslint = new ESLint({ cwd: process.cwd(), overrideConfig: tseslint.configs.disableTypeChecked });

/** The messages `no-restricted-syntax` reports for `source` linted as the file at `relative` in this package. */
async function reported(relative: string, source: string): Promise<string[]> {
  const results = await eslint.lintText(source, { filePath: path.join(process.cwd(), relative) });
  return results.flatMap((r) => r.messages.filter((m) => m.ruleId === "no-restricted-syntax").map((m) => m.message));
}

const DESKTOP = "src/desktop/__eslint_fixtures__/selectors.ts";
const HASH = "_3Yf8b2v5oOD8Wqsxu04ar";

describe("no desktop code names a minified class hash", () => {
  it.each([
    ["a string", `export const s = "${HASH}";\n`],
    ["a selector in a string", `export const s = ".${HASH} > div";\n`],
    ["a class list in a string", `export const s = "Panel ${HASH} Focusable";\n`],
    ["a template part", "export const s = (c: string): string => `.${c} ." + HASH + "`;\n"],
    ["a hash with a dash in it", 'export const s = "_1U7LKpx70kEsz3jJwAFOi-";\n'],
    ["a regex literal", `export const r = /${HASH}/i;\n`],
    ["a selector in a regex literal", `export const r = /\\.${HASH}\\b/;\n`],
  ])("reports %s", async (_kind, source) => {
    expect(await reported(DESKTOP, source)).toEqual([expect.stringContaining("minified class hash")]);
  });

  it.each([
    ["a readable class name", 'export const s = "[class*=\\"AppDetailsOverviewPanel\\"]";\n'],
    ["one of Tender's own ids", 'export const s = "tender-desktop-play-button";\n'],
    ["a CSS transform", 'export const s = "matrix3d(1, 0, 0, 0)";\n'],
    ["a Fiber key", 'export const s = "__reactInternalInstance$";\n'],
    ["a readable regex literal", "export const r = /\\/library\\/app\\/(\\d+)/;\n"],
  ])("leaves %s alone", async (_kind, source) => {
    expect(await reported(DESKTOP, source)).toEqual([]);
  });

  it("leaves a test file alone, which builds Steam's page out of such classes", async () => {
    expect(
      await reported("src/desktop/__eslint_fixtures__/selectors.test.ts", `export const s = "${HASH}";\n`),
    ).toEqual([]);
  });

  it("leaves code outside src/desktop/ alone", async () => {
    expect(await reported("src/utils/__eslint_fixtures__/selectors.ts", `export const s = "${HASH}";\n`)).toEqual([]);
  });

  it("still reports a name that calls Tender a plugin under src/desktop/", async () => {
    expect(await reported(DESKTOP, "export const PLUGIN_NAME = 'x';\n")).toEqual([
      expect.stringContaining("Tender is not a plugin"),
    ]);
  });
}, 60_000);
