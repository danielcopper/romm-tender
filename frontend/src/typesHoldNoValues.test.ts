/**
 * Proves no module in `types/`, at any depth, exports a value at run time.
 *
 * No source twin on purpose: what this guards is the folder, which the coverage
 * setup excludes by its place (`vitest.config.ts`, `sonar-project.properties`)
 * on the strength of exactly this property, and `scripts/check_coverage_exclusions.py`
 * does not look inside it. Code filed there would run without counting towards
 * coverage. Every module is imported as it is on disk, so one the barrel
 * (`types/index.ts`) does not re-export is read too; a `.d.ts` file declares and
 * never runs, so it is left out. A module that runs code at module scope without
 * exporting a value passes.
 */

import { readdirSync, statSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const TYPES = path.join(process.cwd(), "src", "types");

const FILES = readdirSync(TYPES, { recursive: true, encoding: "utf8" }).filter((name) =>
  statSync(path.join(TYPES, name)).isFile(),
);
const isDeclaration = (name: string): boolean => /\.d\.(m|c)?ts$/.test(name);
const MODULES = FILES.filter((name) => /\.(m|c)?[jt]sx?$/.test(name) && !isDeclaration(name));

describe("types/", () => {
  it("reads every file the folder holds but its declaration files", () => {
    expect(MODULES).toEqual(expect.arrayContaining(["index.ts", "navigation.ts", "saves.ts"]));
    expect(FILES.filter((name) => !MODULES.includes(name) && !isDeclaration(name))).toEqual([]);
  });

  it("exports no value from any module", async () => {
    const withValues: string[] = [];
    for (const name of MODULES) {
      const module = (await import(path.join(TYPES, name))) as Record<string, unknown>;
      withValues.push(...Object.keys(module).map((exported) => `${name}: ${exported}`));
    }
    expect(withValues).toEqual([]);
  });
});
