/**
 * Proves the rule in `eslint.config.js` that every `flow-children` in the panel
 * is a value Steam's parser knows actually reports, and reports nothing it
 * should not.
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

const RULE_ID = "tender/flow-children-value";

/** The messages the rule reports for `source` linted as the file at `relative` in this package. */
async function reported(relative: string, source: string): Promise<string[]> {
  const results = await eslint.lintText(source, { filePath: path.join(process.cwd(), relative) });
  return results.flatMap((r) => r.messages.filter((m) => m.ruleId === RULE_ID).map((m) => m.message));
}

const FIXTURE = "src/bigpicture/__eslint_fixtures__/groups.tsx";

const group = (attribute: string): string =>
  `import { Focusable } from "@decky/ui";\nexport const G = () => <Focusable ${attribute}><div /></Focusable>;\n`;

describe("every flow-children is a value Steam's parser knows", () => {
  it.each(["column", "column-reverse", "row", "row-reverse", "grid", "geometric"])("leaves %s alone", async (value) => {
    expect(await reported(FIXTURE, group(`flow-children="${value}"`))).toEqual([]);
  });

  it.each(["horizontal", "vertical", "right", "Row", ""])("reports %j", async (value) => {
    expect(await reported(FIXTURE, group(`flow-children="${value}"`))).toEqual([
      expect.stringContaining(`flow-children="${value}" is not a value Steam's parser knows`),
    ]);
  });

  it("names the page that states the six values", async () => {
    expect(await reported(FIXTURE, group('flow-children="horizontal"'))).toEqual([
      expect.stringContaining('docs/architecture/qam-panel.md, "Focus groups"'),
    ]);
  });

  it.each([
    ["a string in braces", 'flow-children={"right"}'],
    ["a template literal", "flow-children={`right`}"],
  ])("reads the value of %s", async (_kind, attribute) => {
    expect(await reported(FIXTURE, group(attribute))).toEqual([
      expect.stringContaining(`flow-children="right" is not a value Steam's parser knows`),
    ]);
  });

  it.each([
    ["a known value in braces", 'flow-children={"row"}'],
    ["a known value as a template literal", "flow-children={`row`}"],
  ])("leaves %s alone", async (_kind, attribute) => {
    expect(await reported(FIXTURE, group(attribute))).toEqual([]);
  });

  it.each([
    ["a variable", "flow-children={direction}"],
    ["a conditional", 'flow-children={wide ? "row" : "column"}'],
    ["a template with a hole", "flow-children={`${direction}`}"],
    ["no value at all", "flow-children"],
  ])("reports %s, which it cannot check", async (_kind, attribute) => {
    const source = `declare const direction: string;\ndeclare const wide: boolean;\n${group(attribute)}`;
    expect(await reported(FIXTURE, source)).toEqual([expect.stringContaining("flow-children takes a string literal")]);
  });

  it("reports in a test file too", async () => {
    expect(
      await reported("src/bigpicture/__eslint_fixtures__/groups.test.tsx", group('flow-children="horizontal"')),
    ).toEqual([expect.stringContaining("is not a value Steam's parser knows")]);
  });

  it("reports under src/desktop/ too", async () => {
    expect(await reported("src/desktop/__eslint_fixtures__/groups.tsx", group('flow-children="right"'))).toEqual([
      expect.stringContaining("is not a value Steam's parser knows"),
    ]);
  });

  it("leaves another attribute alone", async () => {
    expect(await reported(FIXTURE, group('data-flow-children="horizontal"'))).toEqual([]);
  });
}, 60_000);
