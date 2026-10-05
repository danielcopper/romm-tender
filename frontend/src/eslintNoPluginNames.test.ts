/**
 * Proves the rule in `eslint.config.js` that no name calls Tender a plugin
 * actually reports, and reports nothing it should not.
 *
 * No source twin on purpose, for the reason `eslintBoundaries.test.ts` gives:
 * a green lint run cannot tell a working rule from an inert one. The fixtures
 * are linted as text at paths in this package, since the rule is meant to hold
 * for every file ESLint lints, tests and scripts included. Type-aware parsing is
 * off because the project service refuses a file that is not on disk, and the
 * rule reads no type.
 */

import { ESLint } from "eslint";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import tseslint from "typescript-eslint";

import { NAMES_NOT_ABOUT_TENDER } from "../eslint.config.js";

const eslint = new ESLint({ cwd: process.cwd(), overrideConfig: tseslint.configs.disableTypeChecked });

/** The lines `no-restricted-syntax` reports for `source` linted as the file at `relative` in this package. */
async function reportedLines(relative: string, source: string): Promise<number[]> {
  const results = await eslint.lintText(source, { filePath: path.join(process.cwd(), relative) });
  return results.flatMap((r) => r.messages.filter((m) => m.ruleId === "no-restricted-syntax").map((m) => m.line));
}

describe("no name calls Tender a plugin", () => {
  it.each([
    ["a constant", "export const PLUGIN_NAME = 'Tender';\n"],
    ["a function", "export function definePlugin(): void {}\n"],
    ["a parameter", "export const f = (plugin: string): string => plugin;\n"],
    ["a type", "export type PluginSettings = { a: number };\n"],
    ["an interface", "export interface Plugin {\n  a: number;\n}\n"],
    ["an object key", "export const settings = { pluginVersion: 1 };\n"],
    ["a private field", "export class A {\n  #plugin = 1;\n  get b(): number {\n    return this.#plugin;\n  }\n}\n"],
  ])("reports %s", async (_kind, source) => {
    expect(await reportedLines("src/utils/__eslint_fixtures__/names.ts", source)).not.toEqual([]);
  });

  it("reports a JSX attribute name", async () => {
    const source = "export const A = (): JSX.Element => <div data-plugin-name='x' />;\n";
    expect(await reportedLines("src/utils/__eslint_fixtures__/names.tsx", source)).toEqual([1]);
  });

  it("reports a name in a test file", async () => {
    expect(
      await reportedLines("src/utils/__eslint_fixtures__/names.test.ts", "export const PLUGIN_NAME = 'x';\n"),
    ).toEqual([1]);
  });

  it("reports a name in a build script", async () => {
    expect(await reportedLines("scripts/__eslint_fixtures__/names.mjs", "export const PLUGIN_NAME = 'x';\n")).toEqual([
      1,
    ]);
  });

  it("reports a name that only contains an excepted one", async () => {
    expect(
      await reportedLines("src/utils/__eslint_fixtures__/names.ts", "export const extraPluginsToo = [];\n"),
    ).toEqual([1]);
  });

  it("leaves the names of someone else's plugins alone", async () => {
    const source = [
      "const w = window as unknown as { DeckyPluginLoader?: unknown };",
      "export const loader = w.DeckyPluginLoader;",
      "export const config = { plugins: [], extraPlugins: [] };",
      "",
    ].join("\n");
    expect(await reportedLines("src/utils/__eslint_fixtures__/names.ts", source)).toEqual([]);
  });

  it("leaves string literals alone", async () => {
    const source = "export const manifest = { 'plugin_version': 1, title: 'a plugin made up' };\n";
    expect(await reportedLines("src/utils/__eslint_fixtures__/names.ts", source)).toEqual([]);
  });

  // An exception nothing carries any more would let the next name of that
  // spelling through unread. Read as text, so a comment that still carries the
  // word keeps an exception alive; the list itself is left out of the reading.
  it("lets through only names this package still carries", () => {
    const roots = ["src", "scripts", "eslint-rules"];
    const files = [
      ...fs.readdirSync(process.cwd()),
      ...roots.flatMap((root) =>
        fs.readdirSync(root, { recursive: true, encoding: "utf8" }).map((file) => path.join(root, file)),
      ),
    ].filter((file) => /\.(?:ts|tsx|js|mjs|cjs)$/.test(file));
    const text = files
      .map((file) => fs.readFileSync(path.join(process.cwd(), file), "utf8"))
      .join("\n")
      .replace(/export const NAMES_NOT_ABOUT_TENDER = \[[\s\S]*?\];/, "");

    const unused = NAMES_NOT_ABOUT_TENDER.filter((name) => !new RegExp(`(?<![\\w$])${name}(?![\\w$])`).test(text));

    expect(unused).toEqual([]);
  });
}, 60_000);
