/**
 * Proves `types/` holds type declarations only: no module there exports a value
 * at run time.
 *
 * No source twin on purpose: what this guards is the folder, which the coverage
 * setup excludes by its place (`vitest.config.ts`, `sonar-project.properties`)
 * on the strength of exactly this property, and `scripts/check_coverage_exclusions.py`
 * does not look inside it. A value filed there would leave the coverage ratio
 * unseen. Every module is imported as it is on disk, so one the barrel
 * (`types/index.ts`) does not re-export is read too; a `.d.ts` file declares and
 * never runs, so it is left out.
 */

import { readdirSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const TYPES = path.join(process.cwd(), "src", "types");

const MODULES = readdirSync(TYPES).filter((name) => name.endsWith(".ts") && !name.endsWith(".d.ts"));

describe("types/", () => {
  it("is read in full", () => {
    expect(MODULES).toEqual(expect.arrayContaining(["index.ts", "navigation.ts", "saves.ts"]));
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
