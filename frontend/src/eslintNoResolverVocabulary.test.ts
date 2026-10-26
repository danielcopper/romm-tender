/**
 * Proves the rule in `eslint.config.js` that no panel code reads the
 * resolver's codes or answer field names actually reports, reports nothing it
 * should not, and lists only what the panel still reads.
 *
 * No source twin on purpose, for the reason `eslintBoundaries.test.ts` gives:
 * a green lint run cannot tell a working rule from an inert one. The fixtures
 * are linted as text at paths in this package; type-aware parsing is off
 * because the project service refuses a file that is not on disk, and the rule
 * reads no type.
 */

import { ESLint, type Linter } from "eslint";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import tseslint from "typescript-eslint";

const RULE = "tender/no-resolver-vocabulary";

type ByFile = Record<string, string[]>;
type Options = { codes: string[]; names: string[]; readers: ByFile; notTheResolvers: ByFile };

const eslint = new ESLint({ cwd: process.cwd(), overrideConfig: tseslint.configs.disableTypeChecked });

/** The tokens the rule reports for `source` linted as the file at `relative` in this package. */
async function reported(relative: string, source: string, linter: ESLint = eslint): Promise<string[]> {
  const results = await linter.lintText(source, { filePath: path.join(process.cwd(), relative) });
  return results.flatMap((r) =>
    r.messages.filter((m) => m.ruleId === RULE).map((m) => /^'([^']+)'/.exec(m.message)?.[1] ?? m.message),
  );
}

/** The rule's options as the config hands them to a panel source file. */
async function configured(): Promise<Options> {
  const config = (await eslint.calculateConfigForFile(path.join(process.cwd(), "src/index.tsx"))) as Linter.Config;
  const entry = config.rules?.[RULE];
  if (!Array.isArray(entry)) throw new Error(`${RULE} is not configured for src/index.tsx`);
  return entry[1] as Options;
}

const FIXTURE = "src/utils/__eslint_fixtures__/answer.ts";

// What each file read when the list was written. The configured list may lose
// entries, never gain one: a new reader is built in the backend's reading
// module, not added here.
const READERS_WHEN_WRITTEN: ByFile = {
  "src/bigpicture/EmulatorSelector.tsx": ["requirements_met"],
  "src/bigpicture/bios/BiosFiles.tsx": ["checked", "declared_kind", "satisfied"],
  "src/types/firmware.ts": [
    "caveats",
    "checked",
    "declaration",
    "declared",
    "declared_kind",
    "known_as",
    "locating",
    "refused",
    "requirements_met",
    "satisfied",
    "supplied_by",
  ],
  "src/utils/biosFetchable.ts": ["caveats", "declared_kind", "firmware-path-inaccessible"],
  "src/utils/biosFileNote.ts": [
    "caveats",
    "checked",
    "declaration",
    "declared_kind",
    "firmware-directory-holds-no-candidate",
    "firmware-directory-holds-no-image",
    "firmware-image-contradicted",
    "firmware-image-identified",
    "firmware-image-unlisted",
    "firmware-path-not-a-directory",
    "firmware-path-obstructed",
    "firmware-scan-incomplete",
    "firmware-unreadable",
    "satisfied",
    "supplied_by",
  ],
  "src/utils/biosGroup.ts": ["satisfied"],
  "src/utils/biosPlaceWords.ts": ["declared_kind", "satisfied"],
  "src/utils/biosSummary.ts": [
    "caveats",
    "checked",
    "declared",
    "firmware-path-escapes-root",
    "firmware-path-names-no-file",
    "firmware-path-unresolvable",
    "locating",
    "refused",
    "satisfied",
  ],
  "src/utils/emulatorSourceWording.ts": [
    "bios-root-missing",
    "catalogue-invalid",
    "companion-config-missing",
    "config-unreadable",
    "content-tree-unwired",
    "emulator-config-missing",
    "emulator-config-unreadable",
    "marker-invalid",
    "marker-missing",
    "marker-unreadable",
    "not-set-up",
    "roms-root-missing",
    "roms-root-not-absolute",
    "root-missing",
    "saves-root-missing",
  ],
  "src/utils/firmwareIdentifications.ts": ["known_as"],
};

describe("no panel code reads the resolver's vocabulary", () => {
  it.each([
    ["a code in a string", 'export const c = "firmware-image-identified";\n', ["firmware-image-identified"]],
    ["a code in a template", "export const c = `marker-missing`;\n", ["marker-missing"]],
    ["a code as an object key", 'export const w = { "not-set-up": "Not set up" };\n', ["not-set-up"]],
    ["a field read", "export const f = (a: { x: boolean }) => a.requirements_met;\n", ["requirements_met"]],
    ["a field read by a string", 'export const f = (a: Record<string, boolean>) => a["satisfied"];\n', ["satisfied"]],
    ["a field read by optional chaining", "export const f = (a?: { x: 1 }) => a?.granularity;\n", ["granularity"]],
    ["a field in an object", "export const o = { satisfied: true };\n", ["satisfied"]],
    [
      "a field destructured",
      "export const f = ({ checked }: { checked: boolean }) => !checked;\n",
      ["checked", "checked"],
    ],
    ["a field in a type", "export type T = { requirements_met: boolean | null };\n", ["requirements_met"]],
    ["a field in an interface", "export interface I {\n  caveats: string[];\n}\n", ["caveats"]],
  ])("reports %s", async (_kind, source, tokens) => {
    expect(await reported(FIXTURE, source)).toEqual(tokens);
  });

  it.each([
    ["a generic field name", "export const f = (a: { path: string; label: string }) => a.path + a.label;\n"],
    ["a word that is no code", 'export const c = "marker";\n'],
    ["a field name as a plain string", 'export const c = "satisfied";\n'],
    ["a computed key", "export const f = (a: Record<string, boolean>, k: string) => a[k];\n"],
  ])("leaves %s alone", async (_kind, source) => {
    expect(await reported(FIXTURE, source)).toEqual([]);
  });

  it("leaves a test file alone, which builds the wire payloads it asserts on", async () => {
    const source = 'export const o = { satisfied: true, code: "marker-missing" };\n';
    expect(await reported("src/utils/__eslint_fixtures__/answer.test.ts", source)).toEqual([]);
  });

  it("lets a listed file read only what it is listed for", async () => {
    const source = "export const f = (a: { x: 1 }) => [a.satisfied, a.requirements_met];\n";
    expect(await reported("src/utils/biosGroup.ts", source)).toEqual(["requirements_met"]);
  });

  it("lets through a name that means something else in a file", async () => {
    const source = "export const f = (a: Record<string, number>) => [a.checked, a.satisfied];\n";
    expect(await reported("src/boot/steamModules.ts", source)).toEqual(["satisfied"]);
  });

  it("watches every code and name the backend's check wrote", async () => {
    const written = JSON.parse(fs.readFileSync("resolver-vocabulary.json", "utf8")) as {
      codes: string[];
      names: string[];
    };
    const { codes, names } = await configured();

    expect(codes).toEqual(written.codes);
    expect(names).toEqual(written.names);
    expect(codes).toContain("firmware-image-identified");
    expect(names).toContain("requirements_met");
  });

  it("lists no file for a code or name it does not read any more", async () => {
    const options = await configured();
    const unlisted = new ESLint({
      cwd: process.cwd(),
      overrideConfig: [
        tseslint.configs.disableTypeChecked,
        { rules: { [RULE]: ["error", { ...options, readers: {}, notTheResolvers: {} }] } },
      ],
    });
    const listed = [...Object.entries(options.readers), ...Object.entries(options.notTheResolvers)];

    const unused: string[] = [];
    for (const [file, tokens] of listed) {
      const read = new Set(await reported(file, fs.readFileSync(file, "utf8"), unlisted));
      unused.push(...tokens.filter((token) => !read.has(token)).map((token) => `${file}: ${token}`));
    }

    expect(listed).not.toEqual([]);
    expect(unused).toEqual([]);
  });

  it("lists no reader it did not list when it was written", async () => {
    const { readers } = await configured();
    const added = Object.entries(readers).flatMap(([file, tokens]) =>
      tokens.filter((token) => !(READERS_WHEN_WRITTEN[file] ?? []).includes(token)).map((token) => `${file}: ${token}`),
    );

    expect(added).toEqual([]);
  });
}, 120_000);
