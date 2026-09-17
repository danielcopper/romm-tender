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
import {
  PACKAGE_OWN,
  STEAM_LOOKUPS,
  type SteamLookup,
  UNVERIFIABLE,
  checkSteamModules,
  describeCosmeticMiss,
  describeFailure,
} from "./steamModules";

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

  it("counts exactly one name whose absence is cosmetic", () => {
    // Not a claim that the other twenty-eight were each weighed: `panel` is the
    // status quo, and staying there costs no evidence. This list is the set that
    // HAS been weighed — every consumer read, and each one found to cope — so a
    // second name failing here is somebody's decision rather than an entry that
    // came along with a feature.
    //
    // It is also what `describeCosmeticMiss`'s repair clause stands on: the one
    // name here is reached by a `findModule` predicate of ours, so "a newer
    // Tender" is true of it in both bundles. A cosmetic name `@decky/ui`
    // exported would be Decky's search in the coexistence bundle and that
    // sentence would send the user after the wrong program.
    const cosmetic = STEAM_LOOKUPS.filter((lookup) => lookup.absenceCost === "appearance");
    expect(cosmetic.map((lookup) => lookup.name)).toEqual(["ControllerGlyph"]);
    expect(cosmetic.map((lookup) => lookup.deckyUiExport)).toEqual([false]);
  });

  it("asks a real question of every search it lists", () => {
    // A `found` that never reads anything would answer `true` forever. Each one
    // is called here, which is also what catches an entry whose thunk throws.
    for (const lookup of STEAM_LOOKUPS) expect(typeof lookup.found()).toBe("boolean");
  });
});

describe("what the start-up check reports", () => {
  const lookup = (name: string, present: boolean, deckyUiExport = true): SteamLookup => ({
    name,
    found: () => present,
    deckyUiExport,
    absenceCost: "panel",
  });
  const ours: SearchingCopy = { owner: "tender" };

  it("is satisfied when every search answered", () => {
    const report = checkSteamModules([lookup("Focusable", true), lookup("Tabs", true)]);
    expect(report).toEqual({
      everySearchAnswered: true,
      panelMayMount: true,
      missing: [],
      missingPackageNames: [],
      checked: 2,
    });
    expect(describeFailure(report, ours)).toBe("");
    expect(describeCosmeticMiss(report)).toBe("");
  });

  it("names the searches that found nothing, and only those", () => {
    const report = checkSteamModules([lookup("Focusable", true), lookup("Tabs", false), lookup("Spinner", false)]);
    expect(report.everySearchAnswered).toBe(false);
    expect(report.panelMayMount).toBe(false);
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

describe("what a miss costs the panel", () => {
  const blocking = (name: string, present: boolean): SteamLookup => ({
    name,
    found: () => present,
    deckyUiExport: true,
    absenceCost: "panel",
  });
  const cosmetic = (name: string, present: boolean): SteamLookup => ({
    name,
    found: () => present,
    deckyUiExport: false,
    absenceCost: "appearance",
  });
  const ours: SearchingCopy = { owner: "tender" };

  it("lets the panel mount when everything that missed only costs appearance", () => {
    const report = checkSteamModules([blocking("Focusable", true), cosmetic("ControllerGlyph", false)]);
    expect(report.panelMayMount).toBe(true);
    expect(report.everySearchAnswered).toBe(false);
    expect(report.missing).toEqual(["ControllerGlyph"]);
  });

  it("puts that miss in the log, which is the only thing that reports it", () => {
    // The page is what says anything to a user, and the page does not appear:
    // whoever is asked why a button lost its glyph has this line and nothing
    // else. So it carries the repair, which the page cannot for this name.
    const report = checkSteamModules([blocking("Focusable", true), cosmetic("ControllerGlyph", false)]);
    const sentence = describeCosmeticMiss(report);
    expect(sentence).toContain("1 of 2 searches into Steam's interface found nothing");
    expect(sentence).toContain("Nothing that missed is needed to render the panel");
    expect(sentence).toContain("a newer Tender is the repair");
    expect(describeFailure(report, ours)).toBe("");
  });

  it("keeps the panel off when a search the panel renders with missed", () => {
    const report = checkSteamModules([blocking("Focusable", false), cosmetic("ControllerGlyph", true)]);
    expect(report.panelMayMount).toBe(false);
    expect(describeFailure(report, ours)).toContain("Steam client update");
    // The fallback page is doing the reporting, so the log's cosmetic line must
    // not appear beside it saying the panel started.
    expect(describeCosmeticMiss(report)).toBe("");
  });

  it("keeps the panel off when a cosmetic search missed beside a blocking one", () => {
    const report = checkSteamModules([blocking("Focusable", false), cosmetic("ControllerGlyph", false)]);
    expect(report.panelMayMount).toBe(false);
    expect(report.missing).toEqual(["Focusable", "ControllerGlyph"]);
    expect(describeCosmeticMiss(report)).toBe("");
  });

  it("still reaches the bootstrap answer when everything missed, cosmetic names included", () => {
    // Everything missing necessarily includes the names the panel renders with,
    // so this answer cannot be reached by a cosmetic miss and is unchanged.
    const report = checkSteamModules([blocking("Focusable", false), cosmetic("ControllerGlyph", false)]);
    expect(describeFailure(report, ours)).toContain("not a run of");
  });
});

describe("whose copy the page blames for a stale search", () => {
  // Both bundle answers are exercised for every message: the build constant is
  // a build constant, and a suite that only ever saw one of the two would leave
  // the other's sentence unread until a user read it.
  const stale = checkSteamModules([
    { name: "Focusable", found: () => true, deckyUiExport: true, absenceCost: "panel" },
    { name: "Tabs", found: () => false, deckyUiExport: true, absenceCost: "panel" },
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

  // `SP_REACTDOM` is a global a bootstrap installs, so it is a lookup neither
  // copy of the package ran, in either bundle, and the sentence names none. In
  // the coexistence bundle that is what keeps it from sending the user after
  // Decky Loader, whose copy ran nothing here. It is also the only name that can
  // reach this branch alone: `SP_REACT` and `SP_JSX` are read while the bundle
  // is evaluated, so with either unset it throws at import and this check never
  // runs.
  //
  // `ControllerGlyph` reaches it only in company, since on its own it no longer
  // brings this page up at all — and there it is a real loss, because its
  // predicate is ours in BOTH bundles and "update Tender" would be correct for
  // it. The company it keeps here is a global whose own silence is right, so
  // what the branch gives up is bounded; `describeCosmeticMiss` prints the
  // sentence for the case where the glyph is the whole of the miss.
  it.each([
    [["SP_REACTDOM"], "standalone"],
    [["SP_REACTDOM"], "coexistence"],
    [["SP_REACTDOM", "ControllerGlyph"], "standalone"],
    [["SP_REACTDOM", "ControllerGlyph"], "coexistence"],
  ] as const)("blames neither copy when %s missed, in the %s bundle", (names, bundle) => {
    const missed = checkSteamModules([
      { name: "Focusable", found: () => true, deckyUiExport: true, absenceCost: "panel" },
      ...names.map((name): SteamLookup => ({
        name,
        found: () => false,
        deckyUiExport: false,
        absenceCost: name === "ControllerGlyph" ? "appearance" : "panel",
      })),
    ]);
    const sentence = describeFailure(
      missed,
      readSearchingCopy(missed, bundle, () => ({ carries: () => true, version: "v3.2.8" })),
    );
    expect(sentence).toContain(`${names.length} of ${names.length + 1}`);
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
