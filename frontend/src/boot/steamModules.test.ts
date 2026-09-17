/**
 * The start-up check's own drift lock, plus what it reports.
 *
 * The lock is the first test: every value the panel imports from `@decky/ui`
 * must be classified — as a search this check asks, as a name it cannot answer
 * for, or as the package's own code. A name in none of the three is the one
 * shape that fails, and it is the shape that matters: an import added tomorrow
 * is a dependency nobody decided about, and the check would go on reporting
 * "everything resolved" while the panel rendered a hole.
 *
 * It sweeps the source rather than naming the files, for the reason
 * `test-utils/componentSources.ts` gives at length: a file missing from a
 * hand-kept list carries no lock at all and cannot be told from one that never
 * drifted.
 */

import { globSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { type SearchingCopy, readSearchingCopy } from "./searchingCopy";
import { PACKAGE_OWN, STEAM_LOOKUPS, UNVERIFIABLE, checkSteamModules, describeFailure } from "./steamModules";

const SRC_DIR = `${process.cwd()}/src/`;

/**
 * Every VALUE name imported from `@decky/ui` by code that ships.
 *
 * Tests, test utilities and the global test setup are excluded because they
 * import names to STUB them: a stub is a statement about the harness, not a
 * dependency the panel carries onto a device.
 *
 * Type-only members are excluded in both spellings — a whole `import type`, and
 * an inline `type X` inside a value import. They are erased before the bundle
 * exists, so no search runs for them and there is nothing for this check to ask.
 */
function importedValueNames(): string[] {
  const names = new Set<string>();
  const files = globSync("**/*.{ts,tsx}", { cwd: SRC_DIR })
    .map((relative) => relative.split(/[\\/]/).join("/"))
    .filter((relative) => !/\.(test|spec)\.tsx?$/.test(relative))
    .filter((relative) => !relative.startsWith("test-utils/") && relative !== "test-setup.ts");
  if (files.length === 0) throw new Error(`No sources found under ${SRC_DIR} — a sweep over nothing passes always.`);

  for (const relative of files) {
    const source = readFileSync(`${SRC_DIR}${relative}`, "utf8");
    for (const match of source.matchAll(/import\s+(type\s+)?\{([^}]*)\}\s+from\s+"@decky\/ui"/g)) {
      if (match[1]) continue;
      for (const member of (match[2] ?? "").split(",")) {
        const trimmed = member.trim();
        if (!trimmed || trimmed.startsWith("type ")) continue;
        names.add(trimmed.split(" as ")[0]!.trim());
      }
    }
  }
  return [...names].sort();
}

const classifiedNames = () => [
  ...STEAM_LOOKUPS.map((lookup) => lookup.name),
  ...Object.keys(UNVERIFIABLE),
  ...Object.keys(PACKAGE_OWN),
];

describe("the start-up check's coverage of what the panel imports", () => {
  it("classifies every value the panel imports from @decky/ui", () => {
    const classified = new Set(classifiedNames());
    const unclassified = importedValueNames().filter((name) => !classified.has(name));
    expect(unclassified).toEqual([]);
  });

  it("finds imports to classify, so a sweep that stopped matching cannot pass", () => {
    // Without this the regex above could stop matching — a changed quote style,
    // a moved package name — and the test would compare an empty list against a
    // full one and pass, retiring itself in silence.
    expect(importedValueNames().length).toBeGreaterThan(20);
  });

  it("classifies each name once, so a list cannot quietly absorb another's entries", () => {
    const names = classifiedNames();
    expect(names.length).toBe(new Set(names).size);
  });

  it("marks as a @decky/ui export exactly the names the panel imports from it", () => {
    // What decides whether Decky's own copy can be asked about a name — and a
    // wrong flag is a sentence about the wrong program: the three globals come
    // from the React bootstrap and `ControllerGlyph` from a predicate of ours,
    // so a Decky in perfect step with us exports none of them. The flag is
    // written out because deriving it needs this sweep, which is a filesystem
    // read no device can do.
    const imported = new Set(importedValueNames());
    const wrong = STEAM_LOOKUPS.filter((lookup) => lookup.deckyUiExport !== imported.has(lookup.name));
    expect(wrong.map((lookup) => lookup.name)).toEqual([]);
  });

  it("asks a real question of every search it lists", () => {
    // A `found` that never reads anything would answer `true` forever. Each one
    // is called here, which is also what catches an entry whose thunk throws.
    for (const lookup of STEAM_LOOKUPS) expect(typeof lookup.found()).toBe("boolean");
  });
});

describe("what the start-up check reports", () => {
  const lookup = (name: string, present: boolean, deckyUiExport = true) => ({
    name,
    found: () => present,
    deckyUiExport,
  });
  const ours: SearchingCopy = { owner: "tender" };

  it("is satisfied when every search answered", () => {
    const report = checkSteamModules([lookup("Focusable", true), lookup("Tabs", true)]);
    expect(report).toEqual({ ok: true, missing: [], missingPackageNames: [], checked: 2 });
    expect(describeFailure(report, ours)).toBe("");
  });

  it("names the searches that found nothing, and only those", () => {
    const report = checkSteamModules([lookup("Focusable", true), lookup("Tabs", false), lookup("Spinner", false)]);
    expect(report.ok).toBe(false);
    expect(report.missing).toEqual(["Tabs", "Spinner"]);
    expect(describeFailure(report, ours)).toContain("2 of 3");
    expect(describeFailure(report, ours)).toContain("Steam client update");
  });

  it("separates the names a copy of @decky/ui can be asked about from the rest", () => {
    const report = checkSteamModules([lookup("SP_REACT", false, false), lookup("Tabs", false)]);
    expect(report.missing).toEqual(["SP_REACT", "Tabs"]);
    expect(report.missingPackageNames).toEqual(["Tabs"]);
  });

  it("says something more basic happened when nothing at all resolved", () => {
    // The one fact that leads to a repair: a run of broken predicates and a
    // registry that was never readable are different faults, and only the
    // second is fixed by looking at the bootstrap rather than at Steam.
    const report = checkSteamModules([lookup("Focusable", false), lookup("Tabs", false)]);
    expect(describeFailure(report, ours)).toContain("not a run of");
    expect(describeFailure(report, ours)).not.toContain("Steam client update");
  });

  it("blames neither copy when nothing at all resolved, in either bundle", () => {
    // The bootstrap is not `@decky/ui`'s doing in either bundle, so this one
    // answer must not move with the copy that ran the searches.
    const report = checkSteamModules([lookup("Focusable", false), lookup("Tabs", false)]);
    const theirs: SearchingCopy = { owner: "decky", carriesEveryName: true, version: "v3.2.8" };
    expect(describeFailure(report, theirs)).toBe(describeFailure(report, ours));
  });
});

describe("whose copy the page blames for a stale search", () => {
  // Both bundle answers are exercised for every message: the build constant is
  // a build constant, and a suite that only ever saw one of the two would leave
  // the other's sentence unread until a user read it.
  const stale = checkSteamModules([
    { name: "Focusable", found: () => true, deckyUiExport: true },
    { name: "Tabs", found: () => false, deckyUiExport: true },
  ]);

  it("sends the user after Tender when Tender's own copy searched", () => {
    const sentence = describeFailure(stale, readSearchingCopy(stale, "standalone"));
    expect(sentence).toContain("Tender's own copy of @decky/ui ran them");
    expect(sentence).toContain("A newer Tender is the repair.");
  });

  it("sends the user after Decky when Decky's copy searched, and says who else it breaks", () => {
    const copy = readSearchingCopy(stale, "coexistence", () => ({ carries: () => true, version: "v3.2.8" }));
    const sentence = describeFailure(stale, copy);
    expect(sentence).toContain("Decky Loader v3.2.8's copy of @decky/ui ran them, not Tender's own");
    expect(sentence).toContain("its other plugins are affected");
    expect(sentence).toContain("A newer Decky Loader is the repair.");
  });

  it("names Decky without a version when the version could not be read", () => {
    const copy = readSearchingCopy(stale, "coexistence", () => ({ carries: () => true, version: null }));
    const sentence = describeFailure(stale, copy);
    expect(sentence).toContain("Decky Loader's copy of @decky/ui ran them");
    expect(sentence).not.toContain("v3.2.8");
    expect(sentence).toContain("A newer Decky Loader is the repair.");
  });

  // `ControllerGlyph` is a predicate `utils/deckyUiInternals.ts` runs itself
  // because the package does not export it, and `SP_REACTDOM` is a global a
  // bootstrap installs — so neither is a lookup either copy of the package ran,
  // in either bundle, and the sentence names none. In the coexistence bundle
  // that is what keeps it from sending the user after Decky Loader, whose copy
  // ran neither.
  //
  // The two are NOT alike in what the silence costs. `SP_REACTDOM` has no repair
  // to offer in either bundle — #1900 owns who installed the globals, and in the
  // standalone bundle the miss is `globals.js` not having run or our own
  // predicate in `steamGlobals.ts` having gone stale. `ControllerGlyph`'s
  // predicate is ours in BOTH bundles, so "update Tender" would be correct and
  // is given up here; these cases pin the branch, not the loss.
  //
  // Those two are also the only ones that can reach this: `SP_REACT` and
  // `SP_JSX` are read while the bundle is evaluated, so with either unset it
  // throws at import and this check never runs.
  it.each([
    ["ControllerGlyph", "standalone"],
    ["ControllerGlyph", "coexistence"],
    ["SP_REACTDOM", "standalone"],
    ["SP_REACTDOM", "coexistence"],
  ] as const)("blames neither copy when only %s missed, in the %s bundle", (name, bundle) => {
    const missed = checkSteamModules([
      { name: "Focusable", found: () => true, deckyUiExport: true },
      { name, found: () => false, deckyUiExport: false },
    ]);
    const sentence = describeFailure(
      missed,
      readSearchingCopy(missed, bundle, () => ({ carries: () => true, version: "v3.2.8" })),
    );
    expect(sentence).toContain("1 of 2");
    expect(sentence).toContain("None of them is a name @decky/ui exports");
    expect(sentence).not.toContain("ran them, not Tender's own");
    expect(sentence).not.toContain("Tender's own copy of @decky/ui ran them");
    expect(sentence).not.toContain("is the repair");
  });

  it("calls it a disagreement about the package when Decky's copy lacks the name", () => {
    const copy = readSearchingCopy(stale, "coexistence", () => ({ carries: () => false, version: "v3.2.8" }));
    const sentence = describeFailure(stale, copy);
    expect(sentence).toContain("does not carry some of the names Tender asks it for");
    expect(sentence).toContain("Bringing both Tender and Decky Loader to their current versions is the repair.");
    expect(sentence).not.toContain("A Steam client update");
  });
});
