/**
 * The start-up check's own drift lock, plus what it reports.
 *
 * The lock is the first test: every value the panel imports from `@decky/ui`
 * must be classified — as a search this check asks, as a name it cannot answer
 * for, as a name answered by Steam's runtime state, or as the package's own
 * code. A name in none of the four is the one shape that fails, and it is the
 * shape that matters: an import added tomorrow is a dependency nobody decided
 * about, and the check would go on reporting "everything resolved" while the
 * panel rendered a hole.
 *
 * The third of those four is the one list this file DERIVES rather than trusts,
 * from `@decky/ui`'s own shipped source: which axis a name is answered on is a
 * property of the package's implementation, so reading it there is the only way
 * the classification can be wrong and fail rather than wrong and green.
 *
 * It sweeps the source rather than naming the files, for the reason
 * `test-utils/componentSources.ts` gives at length: a file missing from a
 * hand-kept list carries no lock at all and cannot be told from one that never
 * drifted.
 */

import { globSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { declaredFunctions, divisionSlashes } from "../test-utils/jsFunctionScanner";
import { type SearchingCopy, readSearchingCopy } from "./searchingCopy";
import {
  ASKED_LIVE,
  NOTIFICATION_LOOKUPS,
  PACKAGE_OWN,
  SEARCH_OWNERS,
  STEAM_LOOKUPS,
  type SearchOwner,
  type StartupReport,
  type SteamLookup,
  UNVERIFIABLE,
  checkSteamModules,
  describeFailure,
  describeSurvivedMiss,
  notificationsMissing,
  searchOwner,
  sentenceAsksForAReport,
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
function shippedSources(): string[] {
  const files = globSync("**/*.{ts,tsx}", { cwd: SRC_DIR })
    .map((relative) => relative.split(/[\\/]/).join("/"))
    .filter((relative) => !/\.(test|spec)\.tsx?$/.test(relative))
    .filter((relative) => !relative.startsWith("test-utils/") && relative !== "test-setup.ts");
  if (files.length === 0) throw new Error(`No sources found under ${SRC_DIR} — a sweep over nothing passes always.`);
  return files;
}

function importedValueNames(): string[] {
  const names = new Set<string>();
  for (const relative of shippedSources()) {
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

/**
 * Every name shipped source resolves off Steam FOR ITSELF, in the two shapes
 * one is written in today.
 *
 * The first is `export const X = findModule…(`. `findModule` and its siblings
 * are `@decky/ui`'s readers of Steam's module cache, but the predicate handed
 * to one is ours and runs in both bundles — which is the whole of what "Tender
 * runs this search itself" means, and the only thing that makes a repair
 * Tender's to name.
 *
 * The second is `export const X = (window as …).X`, a global STEAM installs
 * that nothing in `@decky/ui` reaches at all. No copy of the package can be
 * asked about such a name and no copy searched for it, so the repair is
 * Tender's for exactly the same reason: Steam moved it, and this version of
 * Tender is what reads the old place. The exported name must match the
 * property read, or the entry is about a different global than it claims.
 *
 * **What the regexes see is narrower than those sentences**: a probe behind a
 * wrapper, one assigned to a non-exported const, one re-exported from another
 * module, and a global read through anything but a direct cast of `window` are
 * all invisible to them. That costs nothing here, because the only reader below
 * treats an unseen name as NOT Tender's own and fails — so a lookup written in
 * a shape this misses is reported, never waved through.
 *
 * Swept rather than listed, for the reason {@link importedValueNames} gives.
 */
function tenderOwnSearchNames(): string[] {
  const names = new Set<string>();
  for (const relative of shippedSources()) {
    const source = readFileSync(`${SRC_DIR}${relative}`, "utf8");
    for (const match of source.matchAll(
      /export\s+const\s+(\w+)\s*(?::[^=]*?)?=\s*find(?:Module|ClassModule)\w*\s*\(/g,
    )) {
      names.add(match[1]!);
    }
    for (const match of source.matchAll(/export\s+const\s+(\w+)\s*:[^=]*?=\s*\(\s*window\s+as[^)]*\)\s*\.\s*(\w+)/g)) {
      if (match[1] === match[2]) names.add(match[1]!);
    }
  }
  return [...names].sort();
}

const DIST_DIR = `${process.cwd()}/node_modules/@decky/ui/dist/`;

/**
 * The reads of Steam's RUNTIME STATE in `@decky/ui`'s shipped source, spelled
 * as that source spells them.
 *
 * `document.title` is `findSP`'s first question; the other two are the package's
 * own readers of the focus controller, which is where every navigation tree is
 * reached from. None of them asks Steam's module registry — they ask what Steam
 * has mounted, which is what makes a start-up reading of them no verdict.
 */
const RUNTIME_STATE_READS = ["getGamepadNavigationTrees(", "getFocusNavController(", "document.title"];

/** The two readers themselves: they read by definition, whatever their bodies happen to call. */
const RUNTIME_STATE_READERS = ["getFocusNavController", "getGamepadNavigationTrees"];

/**
 * Every name `@decky/ui` exports whose implementation reads Steam's runtime
 * state — derived from the package's own shipped source, never listed here.
 *
 * A function reads if its body performs one of {@link RUNTIME_STATE_READS}, if
 * it IS one of {@link RUNTIME_STATE_READERS}, or if it calls something in the
 * same file that does — which is what carries `useQuickAccessVisible` through
 * its module-private `getQuickAccessWindow`.
 *
 * **What this sees is narrower than the sentence above**: `function`
 * declarations only, not nested in another, and transitivity only WITHIN a
 * file. A name that reaches the trees through an arrow export, through a
 * re-export, or through another module's helper is invisible to it —
 * `showModal` is exactly that, calling `findSP() || window` from
 * `dist/components/Modal.js`, and it is classified on a different axis anyway.
 *
 * What the sweep cannot see it says nothing about: such a name can sit in
 * {@link STEAM_LOOKUPS} unflagged, which is the shape this cut removed by hand.
 * What the narrowness cannot do is put a registry search onto the live list in
 * silence — a name the sweep did not derive fails the equality there.
 */
/** Every shipped `.js` of the installed package — the source both sweeps below read. */
function distFiles(): string[] {
  const files = globSync("**/*.js", { cwd: DIST_DIR });
  if (files.length === 0) throw new Error(`No @decky/ui source found under ${DIST_DIR} — a sweep over nothing passes.`);
  return files;
}

function runtimeStateNames(): string[] {
  const exported = new Set<string>();
  for (const relative of distFiles()) {
    const functions = declaredFunctions(readFileSync(`${DIST_DIR}${relative}`, "utf8"));
    const reads = new Set(
      functions
        .filter(
          (fn) => RUNTIME_STATE_READERS.includes(fn.name) || RUNTIME_STATE_READS.some((read) => fn.body.includes(read)),
        )
        .map((fn) => fn.name),
    );
    for (let grew = true; grew;) {
      grew = false;
      for (const fn of functions) {
        if (reads.has(fn.name)) continue;
        if (![...reads].some((reader) => fn.body.includes(`${reader}(`))) continue;
        reads.add(fn.name);
        grew = true;
      }
    }
    for (const fn of functions) if (fn.exported && reads.has(fn.name)) exported.add(fn.name);
  }
  return [...exported].sort();
}

const classifiedNames = () => [
  ...STEAM_LOOKUPS.map((lookup) => lookup.name),
  ...Object.keys(UNVERIFIABLE),
  ...Object.keys(ASKED_LIVE),
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

  it("finds a name that reads Steam's runtime state, so a sweep that stopped matching cannot pass", () => {
    // The same guard the import sweep carries, for the same reason: a renamed
    // reader or a repackaged `dist/` would leave this deriving nothing. An empty
    // set passes the "asks none of them" test below in silence, and fails the
    // equality only while ASKED_LIVE has entries — this names the cause instead.
    expect(runtimeStateNames()).toContain("findSP");
  });

  it("reads no slash in the installed @decky/ui as division with a second slash after it on the line, which is the one shape the regex heuristic cannot tell apart", () => {
    // The scanner tells a regex literal from division by what precedes the `/`,
    // and the reading it cannot check is a `/` it calls division that opens a
    // literal after all — silent, because the literal's quotes and braces are
    // then counted as code. Every such mistake has its closing `/` later on the
    // same line, so this says the installed package contains no candidate at
    // all. A failure names a line to read: either it is real division and this
    // assertion is too strong for this package, or the heuristic has met the
    // case it cannot see.
    //
    // **Measured today: the package contains no division at all**, so this
    // ranges over an empty set and constrains the CURRENT install only in that
    // it would speak up when one appears. That `divisionSlashes` reports
    // division when there is division is held separately, by the scanner's own
    // test, so an empty answer here cannot be the function having stopped
    // working.
    const candidates: string[] = [];
    for (const relative of distFiles()) {
      const source = readFileSync(`${DIST_DIR}${relative}`, "utf8");
      for (const offset of divisionSlashes(source)) {
        const lineEnd = source.indexOf("\n", offset);
        const rest = source.slice(offset + 1, lineEnd === -1 ? source.length : lineEnd);
        if (rest.includes("/")) candidates.push(`${relative}:${source.slice(0, offset).split("\n").length}`);
      }
    }
    expect(candidates).toEqual([]);
  });

  it("classifies as asked-live exactly the imported names that read Steam's runtime state", () => {
    // The axis, mechanized: the check asks Steam's module registry or a
    // bootstrap global, and a name answered by what Steam has mounted or focused
    // has no verdict to give at start-up. Both directions, because either one
    // alone is a hole — a live name left off the list is a panel refused over a
    // tree Big Picture had not built yet, and a registry search moved onto it is
    // a search nothing asks any more.
    const imported = new Set(importedValueNames());
    expect(runtimeStateNames().filter((name) => imported.has(name))).toEqual(Object.keys(ASKED_LIVE).sort());
  });

  it("asks none of them at start-up", () => {
    // Whatever the intersection above says about what the panel imports, no name
    // the package answers from runtime state may be a search this check refuses
    // to mount over.
    const live = new Set(runtimeStateNames());
    expect(STEAM_LOOKUPS.filter((lookup) => live.has(lookup.name)).map((lookup) => lookup.name)).toEqual([]);
  });

  it("classifies each name once, so a list cannot quietly absorb another's entries", () => {
    const names = classifiedNames();
    expect(names).toHaveLength(new Set(names).size);
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

  it("keeps a search neither copy of @decky/ui owns off the mount-anyway path", () => {
    // What `describeSurvivedMiss` says when nothing that missed is a @decky/ui
    // export: Tender looks these up itself, so a newer Tender is the repair.
    // True of `ControllerGlyph`, which `utils/deckyUiInternals.ts` reaches with
    // a `findModule` predicate of ours in both bundles, and of
    // `NotificationStore`, a global Steam itself installs that no copy of the
    // package reaches — and false of the `SP_*` globals, which a React
    // bootstrap installs and which the frontend cannot attribute to a program
    // from inside the page.
    //
    // What keeps those out of the sentence is their COST, not their names: the
    // panel does not mount without them, so they never reach the line this
    // stands behind. That is the property, and asserting it rather than the
    // short set it produces is what makes a name added tomorrow fail here
    // instead of shipping a repair aimed at the wrong program.
    const ownSearches = new Set(tenderOwnSearchNames());
    expect(ownSearches.size).toBeGreaterThan(0);
    const wrong = STEAM_LOOKUPS.filter(
      (lookup) => lookup.absenceCost !== "panel" && !lookup.deckyUiExport && !ownSearches.has(lookup.name),
    );
    expect(wrong.map((lookup) => lookup.name)).toEqual([]);
  });

  it("sweeps both shapes a lookup of Tender's own is written in, so neither can stop matching in silence", () => {
    // The guard the sweep above needs for itself: it answers a set, and an
    // empty one passes that filter for every name there is. One name per shape,
    // because the two regexes fail independently — a probe read and a global
    // read.
    const ownSearches = new Set(tenderOwnSearchNames());
    expect(ownSearches).toContain("ControllerGlyph");
    expect(ownSearches).toContain("NotificationStore");
  });

  it("has a name at every absence cost, so none of the four is a value nothing uses", () => {
    // `panel` is the status quo and costs no evidence to stay at; moving a name
    // off it is a decision taken per name, against each of its consumers. This
    // says only that all four answers are live — a cost nothing carries is a
    // branch no reader exercises, and the diagnostic one arrived with exactly
    // one name behind it.
    expect([...new Set(STEAM_LOOKUPS.map((lookup) => lookup.absenceCost))].sort()).toEqual([
      "appearance",
      "diagnostic",
      "feature",
      "panel",
    ]);
  });

  it("asks about every lookup a toast is raised through, at the cost that keeps the panel up", () => {
    // The notice on Main and the entries here spell the names from one place
    // (`NOTIFICATION_LOOKUPS`), so this says the check actually carries an
    // entry per name: a notice keyed on a lookup nobody asks about would never
    // appear, whatever Steam did.
    const raisedThrough: readonly string[] = Object.values(NOTIFICATION_LOOKUPS);
    const entries = STEAM_LOOKUPS.filter((lookup) => raisedThrough.includes(lookup.name));
    expect(entries.map((lookup) => lookup.name).sort()).toEqual([...raisedThrough].sort());
    expect(entries.map((lookup) => lookup.absenceCost)).toEqual(raisedThrough.map(() => "feature"));
  });

  it("asks about the toast class map, at the cost an unstyled toast is worth", () => {
    // It is the one lookup behind a toast that does NOT stop the push: every
    // read of it in `utils/steamToast.tsx` is optional, so a miss costs the
    // layout and the toast still says what it says.
    const classes = STEAM_LOOKUPS.find((lookup) => lookup.name === "toastClasses");
    expect(classes?.absenceCost).toBe("appearance");
    expect(Object.values(NOTIFICATION_LOOKUPS)).not.toContain("toastClasses");
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
    expect(describeSurvivedMiss(report, ours)).toBe("");
  });

  it("names the searches that found nothing, and only those", () => {
    const report = checkSteamModules([lookup("Focusable", true), lookup("Tabs", false), lookup("Spinner", false)]);
    expect(report.everySearchAnswered).toBe(false);
    expect(report.panelMayMount).toBe(false);
    expect(report.missing).toEqual(["Tabs", "Spinner"]);
    expect(describeFailure(report, ours)).toContain("Steam has changed");
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
    expect(describeFailure(report, ours)).toContain("Tender started before Steam was ready.");
    expect(describeFailure(report, ours)).not.toContain("Steam has changed");
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
  // The third cost, in the shape the real entry has: a `@decky/ui` export, so
  // the search behind it is Decky's copy's in the coexistence bundle.
  const diagnosticOnly = (name: string, present: boolean): SteamLookup => ({
    name,
    found: () => present,
    deckyUiExport: true,
    absenceCost: "diagnostic",
  });
  // The fourth cost, not a `@decky/ui` export — like two of the three real
  // entries.
  const feature = (name: string, present: boolean): SteamLookup => ({
    name,
    found: () => present,
    deckyUiExport: false,
    absenceCost: "feature",
  });
  // The third: `ErrorBoundary` IS a `@decky/ui` export, so in the coexistence
  // bundle a miss of it alone is DECKY's search and the verdict is `decky`.
  const exportedFeature = (name: string, present: boolean): SteamLookup => ({
    name,
    found: () => present,
    deckyUiExport: true,
    absenceCost: "feature",
  });
  const ours: SearchingCopy = { owner: "tender" };
  const theirs = (report: StartupReport): SearchingCopy =>
    readSearchingCopy(report, "coexistence", () => ({ carries: () => true, version: "v3.2.8" }));

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
    const sentence = describeSurvivedMiss(report, ours);
    expect(sentence).toContain("1 of 2 searches into Steam's interface found nothing");
    expect(sentence).toContain("Nothing that missed is needed to render the panel");
    expect(sentence).toContain("Tender looks these up itself, so a newer Tender is the repair.");
    expect(describeFailure(report, ours)).toBe("");
  });

  it("says the same for a glyph of ours in the coexistence bundle, where Decky's copy ran nothing", () => {
    // The name is not a `@decky/ui` export, so neither copy of the package can
    // own its search — and the sentence must not move with the bundle, or the
    // log sends the user after Decky Loader for a predicate of ours.
    const report = checkSteamModules([blocking("Focusable", true), cosmetic("ControllerGlyph", false)]);
    expect(describeSurvivedMiss(report, theirs(report))).toBe(describeSurvivedMiss(report, ours));
  });

  it("keeps the panel off when a search the panel renders with missed", () => {
    const report = checkSteamModules([blocking("Focusable", false), cosmetic("ControllerGlyph", true)]);
    expect(report.panelMayMount).toBe(false);
    expect(describeFailure(report, ours)).toContain("Steam has changed");
    // The fallback page is doing the reporting, so the log's cosmetic line must
    // not appear beside it saying the panel started.
    expect(describeSurvivedMiss(report, ours)).toBe("");
  });

  it("keeps the panel off when a cosmetic search missed beside a blocking one", () => {
    const report = checkSteamModules([blocking("Focusable", false), cosmetic("ControllerGlyph", false)]);
    expect(report.panelMayMount).toBe(false);
    expect(report.missing).toEqual(["Focusable", "ControllerGlyph"]);
    expect(describeSurvivedMiss(report, ours)).toBe("");
  });

  it("lets the panel mount when all that missed was read by a diagnostic", () => {
    const report = checkSteamModules([blocking("Focusable", true), diagnosticOnly("playSectionClasses", false)]);
    expect(report.panelMayMount).toBe(true);
    expect(report.everySearchAnswered).toBe(false);
    expect(report.missing).toEqual(["playSectionClasses"]);
    expect(describeFailure(report, ours)).toBe("");
  });

  it("keeps the panel off when a diagnostic-only search missed beside a blocking one", () => {
    const report = checkSteamModules([blocking("Focusable", false), diagnosticOnly("playSectionClasses", false)]);
    expect(report.panelMayMount).toBe(false);
    expect(report.missing).toEqual(["Focusable", "playSectionClasses"]);
    expect(describeSurvivedMiss(report, ours)).toBe("");
  });

  it("sends a diagnostic-only miss after the copy that ran its search, which is Decky's in coexistence", () => {
    // The whole reason the third cost could not simply reuse the cosmetic
    // wording: this name IS a `@decky/ui` export, so in the coexistence bundle
    // the predicate behind it belongs to Decky Loader and "a newer Tender"
    // would name a program that ran nothing.
    const report = checkSteamModules([blocking("Focusable", true), diagnosticOnly("playSectionClasses", false)]);
    expect(describeSurvivedMiss(report, ours)).toContain("Tender ran these searches, so a newer Tender is the repair.");
    const coexistence = describeSurvivedMiss(report, theirs(report));
    expect(coexistence).toContain("Decky Loader v3.2.8's copy of @decky/ui ran them, not Tender's own");
    expect(coexistence).toContain("a newer Decky Loader is the repair");
    expect(coexistence).not.toContain("newer Tender");
  });

  it("names neither program alone when the miss spans one search of each", () => {
    // `ControllerGlyph` is ours in both bundles and `playSectionClasses` is the
    // package's, so in the coexistence bundle one of the two missed searches
    // was Decky's and the other was not. Picking either would be a sentence
    // about a program that is only half implicated.
    const report = checkSteamModules([
      blocking("Focusable", true),
      cosmetic("ControllerGlyph", false),
      diagnosticOnly("playSectionClasses", false),
    ]);
    expect(report.panelMayMount).toBe(true);
    const sentence = describeSurvivedMiss(report, theirs(report));
    expect(sentence).toContain("2 of 3 searches into Steam's interface found nothing");
    expect(sentence).toContain("Tender ran some of them itself and Decky Loader v3.2.8's copy of @decky/ui the rest");
    // What missed settles BOTH halves rather than neither: Decky's copy carries
    // every name asked of it and its search still came back empty, and a probe
    // Tender runs itself missed beside it. The line used to call that unsettled.
    expect(sentence).toContain(
      "so both went stale: a lookup of Tender's own missed, and so did a search Decky's copy ran",
    );
    expect(sentence).toContain("Bringing both Tender and Decky Loader to their current versions is the repair.");
    expect(sentence).not.toContain("ran them, not Tender's own");
    expect(sentence).not.toContain("a newer Tender is the repair");
  });

  it("names Tender alone for that same pair in the standalone bundle, where both searches are its own", () => {
    const report = checkSteamModules([
      blocking("Focusable", true),
      cosmetic("ControllerGlyph", false),
      diagnosticOnly("playSectionClasses", false),
    ]);
    const sentence = describeSurvivedMiss(report, readSearchingCopy(report, "standalone"));
    expect(sentence).toContain("Tender ran these searches, so a newer Tender is the repair.");
    expect(sentence).not.toContain("Decky Loader");
  });

  it("calls a mixed miss a package disagreement when Decky's copy lacks one of the names", () => {
    // The precedence between the two answers, which nothing else pins: this
    // miss is both — one search of Tender's own and one of the package's — AND
    // Decky's copy cannot account for the package's. A name Decky's copy does
    // not export is a fact about the two installs, where a name it exports with
    // an empty value is a search result whose cause is inferred, so the
    // disagreement wins and its repair covers the other half anyway. Swap the
    // two branches and this reads "so did a search Decky's copy ran" over a
    // name Decky's copy never carried.
    const report = checkSteamModules([
      blocking("Focusable", true),
      cosmetic("ControllerGlyph", false),
      diagnosticOnly("playSectionClasses", false),
    ]);
    const copy = readSearchingCopy(report, "coexistence", () => ({ carries: () => false, version: "v3.2.8" }));
    expect(searchOwner(report, copy)).toBe("disagreement");
    const sentence = describeSurvivedMiss(report, copy);
    expect(sentence).toContain("does not carry some of the names Tender asks it for");
    expect(sentence).not.toContain("Tender ran some of them itself");
  });

  it("calls a miss Decky's copy cannot account for a disagreement about the package", () => {
    // The third vocabulary reaches this line too: a name Decky's copy does not
    // export is a demonstrated fact about the two installs, not a stale lookup,
    // and its repair is neither program alone.
    const report = checkSteamModules([blocking("Focusable", true), diagnosticOnly("playSectionClasses", false)]);
    const copy = readSearchingCopy(report, "coexistence", () => ({ carries: () => false, version: "v3.2.8" }));
    const sentence = describeSurvivedMiss(report, copy);
    expect(sentence).toContain("Decky Loader v3.2.8's copy of @decky/ui does not carry some of the names");
    expect(sentence).toContain("Bringing both Tender and Decky Loader to their current versions is the repair.");
    expect(sentence).not.toContain("ran them, not Tender's own");
  });

  it("lets the panel mount when all that missed was a whole feature, and says so in the log", () => {
    // The fourth cost: the panel renders entirely well and something outside it
    // is gone, so the mount is right and this line is the only thing anywhere
    // that reports the loss.
    const report = checkSteamModules([blocking("Focusable", true), feature("ToastRenderer", false)]);
    expect(report.panelMayMount).toBe(true);
    expect(describeFailure(report, ours)).toBe("");
    const sentence = describeSurvivedMiss(report, ours);
    expect(sentence).toContain("Nothing that missed is needed to render the panel");
    expect(sentence).toContain("Tender's notifications are off.");
    expect(sentence).toContain("Tender looks these up itself, so a newer Tender is the repair.");
  });

  it("says nothing about the notifications over a feature that is not one of theirs", () => {
    // The mirror of Main's own rule: the sentence reads the names, so a
    // `feature` entry added for something else does not put it in the log.
    const report = checkSteamModules([blocking("Focusable", true), feature("SomeOtherFeature", false)]);
    expect(report.panelMayMount).toBe(true);
    expect(describeSurvivedMiss(report, ours)).not.toContain("notifications are off");
  });

  it("names no repair of its own for the lost notifications, under every verdict", () => {
    // One of the three lookups behind the sentence IS a `@decky/ui` export — so
    // a miss of that one in the coexistence bundle is Decky's search, and a
    // sentence saying "until Tender is updated" would sit beside "a newer Decky
    // Loader is the repair". It states the loss and leaves the repair to the
    // verdict.
    const fixtures: Record<SearchOwner, () => { report: StartupReport; copy: SearchingCopy }> = {
      none: () => {
        const report = checkSteamModules([blocking("Focusable", true), feature("NotificationStore", false)]);
        return { report, copy: theirs(report) };
      },
      tender: () => {
        const report = checkSteamModules([blocking("Focusable", true), exportedFeature("ErrorBoundary", false)]);
        return { report, copy: readSearchingCopy(report, "standalone") };
      },
      disagreement: () => {
        const report = checkSteamModules([blocking("Focusable", true), exportedFeature("ErrorBoundary", false)]);
        return {
          report,
          copy: readSearchingCopy(report, "coexistence", () => ({ carries: () => false, version: "v3.2.8" })),
        };
      },
      mixed: () => {
        const report = checkSteamModules([
          blocking("Focusable", true),
          feature("NotificationStore", false),
          exportedFeature("ErrorBoundary", false),
        ]);
        return { report, copy: theirs(report) };
      },
      decky: () => {
        const report = checkSteamModules([blocking("Focusable", true), exportedFeature("ErrorBoundary", false)]);
        return { report, copy: theirs(report) };
      },
    };

    for (const owner of SEARCH_OWNERS) {
      const { report, copy } = fixtures[owner]();
      expect(searchOwner(report, copy)).toBe(owner);
      expect(report.panelMayMount).toBe(true);
      const sentence = describeSurvivedMiss(report, copy);
      expect(sentence).toContain("Tender's notifications are off.");
      expect(sentence).not.toContain("notifications are off until");
    }
    // And the one verdict that would have contradicted it names Decky alone.
    const { report, copy } = fixtures.decky();
    expect(describeSurvivedMiss(report, copy)).toContain("a newer Decky Loader is the repair");
    expect(describeSurvivedMiss(report, copy)).not.toContain("newer Tender");
  });

  it("leaves that sentence out when nothing a toast is raised through missed", () => {
    // Without this the sentence could be unconditional and every test above
    // would still pass, telling a reader their notifications are off over a
    // missing glyph.
    const report = checkSteamModules([blocking("Focusable", true), cosmetic("ControllerGlyph", false)]);
    expect(describeSurvivedMiss(report, ours)).not.toContain("notifications are off");
  });

  it("keeps the panel off when a feature search missed beside a blocking one", () => {
    const report = checkSteamModules([blocking("Focusable", false), feature("ToastRenderer", false)]);
    expect(report.panelMayMount).toBe(false);
    // The fallback page is doing the reporting; the log's mounted line must not
    // appear beside it claiming Tender has started.
    expect(describeSurvivedMiss(report, ours)).toBe("");
  });

  it("reads the notice on Main off any one of the lookups a toast is raised through", () => {
    for (const name of Object.values(NOTIFICATION_LOOKUPS)) {
      expect(notificationsMissing(checkSteamModules([blocking("Focusable", true), feature(name, false)]))).toBe(true);
    }
    const neither = checkSteamModules([blocking("Focusable", true), cosmetic("ControllerGlyph", false)]);
    expect(notificationsMissing(neither)).toBe(false);
  });

  it("does not claim the notifications went missing over some other feature", () => {
    // It reads the names rather than the cost, so a `feature` entry added for
    // something else cannot put this notice on Main.
    const other = checkSteamModules([blocking("Focusable", true), feature("SomeOtherFeature", false)]);
    expect(notificationsMissing(other)).toBe(false);
  });

  it("still reaches the bootstrap answer when everything missed, cosmetic names included", () => {
    // Everything missing necessarily includes the names the panel renders with,
    // so this answer cannot be reached by a cosmetic miss and is unchanged.
    const report = checkSteamModules([blocking("Focusable", false), cosmetic("ControllerGlyph", false)]);
    expect(describeFailure(report, ours)).toContain("Tender started before Steam was ready.");
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
    expect(sentence).toBe(
      "Steam has changed, and this version of Tender doesn't know its way around the new one yet. " +
        "Updating Tender should fix it.",
    );
  });

  it("sends the user after Decky when Decky's copy searched, and says who else it breaks", () => {
    const copy = readSearchingCopy(stale, "coexistence", () => ({ carries: () => true, version: "v3.2.8" }));
    expect(describeFailure(stale, copy)).toBe(
      "Steam has changed, and Decky Loader v3.2.8 doesn't know its way around the new one yet. " +
        "Tender shares that part with Decky, so it's affected too — and so are Decky's own menu and " +
        "its other plugins. Updating Decky Loader should fix all of it.",
    );
  });

  it("names Decky without a version when the version could not be read", () => {
    const copy = readSearchingCopy(stale, "coexistence", () => ({ carries: () => true, version: null }));
    const sentence = describeFailure(stale, copy);
    expect(sentence).toContain("Steam has changed, and Decky Loader doesn't know its way around");
    expect(sentence).not.toContain("v3.2.8");
    expect(sentence).toContain("Updating Decky Loader should fix all of it.");
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
  // what the branch gives up is bounded; `describeSurvivedMiss` prints the
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
    expect(sentence).toBe(
      "Tender couldn't find the parts it needs from Steam. Please report this — the names below are what helps.",
    );
  });

  it("leaves the glyph in the unnamed rest with no global anywhere in the miss", () => {
    // `ControllerGlyph` never brings this page up alone — its absence costs
    // appearance — but it does not only ever arrive beside a global. A
    // blocking `@decky/ui` name is the other company it can keep, and that is
    // `mixed`, whose "rest" is the glyph and nothing else. The page asks for a
    // report about that rest rather than naming a newer Tender.
    const missed = checkSteamModules([
      { name: "Tabs", found: () => true, deckyUiExport: true, absenceCost: "panel" },
      { name: "Focusable", found: () => false, deckyUiExport: true, absenceCost: "panel" },
      { name: "ControllerGlyph", found: () => false, deckyUiExport: false, absenceCost: "appearance" },
    ]);
    expect(missed.missing).toEqual(["Focusable", "ControllerGlyph"]);
    expect(missed.panelMayMount).toBe(false);
    const copy = readSearchingCopy(missed, "coexistence", () => ({ carries: () => true, version: "v3.2.8" }));
    expect(searchOwner(missed, copy)).toBe("mixed");
    expect(describeFailure(missed, copy)).toBe(
      "Steam has changed, and Decky Loader v3.2.8 doesn't know its way around the new one yet. " +
        "Updating Decky Loader fixes that part. If this page still appears afterwards, please report it.",
    );
  });

  it("calls it a disagreement about the package when Decky's copy lacks the name", () => {
    const copy = readSearchingCopy(stale, "coexistence", () => ({ carries: () => false, version: "v3.2.8" }));
    expect(describeFailure(stale, copy)).toBe(
      "Tender and Decky Loader v3.2.8 are out of step with each other: Tender asks Decky's shared part " +
        "for things it doesn't have. Updating both to their current versions should fix it.",
    );
  });

  it("no longer credits Decky with the search for a global its copy never ran", () => {
    // The defect the shared verdict removed: a missed global beside any
    // `@decky/ui` name used to fall past every earlier branch and say "ran
    // them, not Tender's own" about a set the package's copy ran only part of.
    // `SP_REACTDOM` is not a `@decky/ui` export at all — no copy of the package
    // searched for it.
    const missed = checkSteamModules([
      { name: "Focusable", found: () => true, deckyUiExport: true, absenceCost: "panel" },
      { name: "SP_REACTDOM", found: () => false, deckyUiExport: false, absenceCost: "panel" },
      { name: "Tabs", found: () => false, deckyUiExport: true, absenceCost: "panel" },
    ]);
    const copy = readSearchingCopy(missed, "coexistence", () => ({ carries: () => true, version: "v3.2.8" }));
    expect(searchOwner(missed, copy)).toBe("mixed");
    const sentence = describeFailure(missed, copy);
    expect(sentence).toContain("Updating Decky Loader fixes that part.");
    expect(sentence).toContain("If this page still appears afterwards, please report it.");
    expect(sentence).not.toContain("Tender shares that part with Decky");
  });
});

describe("the verdict the page and the log line share", () => {
  // Both surfaces answer one question about one machine, so the answer is read
  // once and worded twice. This is the join between them, and it fails in both
  // directions a drift can go: a surface that loses a case falls through to a
  // neighbour's wording, which the distinctness assertion catches, and a
  // verdict added to `SEARCH_OWNERS` without a branch on BOTH surfaces does not
  // compile at all, because each wording function switches exhaustively over
  // the union and has no fallthrough return.
  const found = (name: string): SteamLookup => ({
    name,
    found: () => true,
    deckyUiExport: true,
    absenceCost: "panel",
  });
  const missedLookup = (
    name: string,
    deckyUiExport: boolean,
    absenceCost: SteamLookup["absenceCost"],
  ): SteamLookup => ({
    name,
    found: () => false,
    deckyUiExport,
    absenceCost,
  });

  interface Fixture {
    readonly lookups: readonly SteamLookup[];
    readonly bundle: "standalone" | "coexistence";
    readonly carries: boolean;
  }

  // The page is reached only where a blocking name missed, so its fixtures
  // block; the log line only where none did, so its fixtures do not. Each pair
  // is the cheapest report that reaches its verdict, and the verdict each one
  // actually reaches is asserted below rather than assumed.
  const pageFixtures: Record<SearchOwner, Fixture> = {
    none: {
      lookups: [found("Focusable"), missedLookup("SP_REACTDOM", false, "panel")],
      bundle: "coexistence",
      carries: true,
    },
    tender: { lookups: [found("Focusable"), missedLookup("Tabs", true, "panel")], bundle: "standalone", carries: true },
    disagreement: {
      lookups: [found("Focusable"), missedLookup("Tabs", true, "panel")],
      bundle: "coexistence",
      carries: false,
    },
    mixed: {
      lookups: [found("Focusable"), missedLookup("SP_REACTDOM", false, "panel"), missedLookup("Tabs", true, "panel")],
      bundle: "coexistence",
      carries: true,
    },
    decky: { lookups: [found("Focusable"), missedLookup("Tabs", true, "panel")], bundle: "coexistence", carries: true },
  };

  const logFixtures: Record<SearchOwner, Fixture> = {
    none: {
      lookups: [found("Focusable"), missedLookup("ControllerGlyph", false, "appearance")],
      bundle: "coexistence",
      carries: true,
    },
    tender: {
      lookups: [found("Focusable"), missedLookup("playSectionClasses", true, "diagnostic")],
      bundle: "standalone",
      carries: true,
    },
    disagreement: {
      lookups: [found("Focusable"), missedLookup("playSectionClasses", true, "diagnostic")],
      bundle: "coexistence",
      carries: false,
    },
    mixed: {
      lookups: [
        found("Focusable"),
        missedLookup("ControllerGlyph", false, "appearance"),
        missedLookup("playSectionClasses", true, "diagnostic"),
      ],
      bundle: "coexistence",
      carries: true,
    },
    decky: {
      lookups: [found("Focusable"), missedLookup("playSectionClasses", true, "diagnostic")],
      bundle: "coexistence",
      carries: true,
    },
  };

  const read = (fixture: Fixture) => {
    const report = checkSteamModules(fixture.lookups);
    const copy = readSearchingCopy(report, fixture.bundle, () => ({
      carries: () => fixture.carries,
      version: "v3.2.8",
    }));
    return { report, copy };
  };

  // The log line opens with the count of what missed, which says nothing about
  // whose searches they were. Comparing the sentences whole would let two
  // identical answers pass for different ones on the strength of that prefix,
  // so it is taken off — and asserted first, so a changed opening fails here
  // instead of silently cutting a sentence in the wrong place.
  const answerOnly = (sentence: string, prefix: string): string => {
    expect(sentence.startsWith(prefix)).toBe(true);
    return sentence.slice(prefix.length);
  };
  const scaleOf = (report: StartupReport): string =>
    `${report.missing.length} of ${report.checked} searches into Steam's interface found nothing. `;
  const MOUNTED = "Nothing that missed is needed to render the panel, so Tender has started. ";

  it.each(SEARCH_OWNERS)("reaches the %s verdict from both surfaces' fixtures", (owner) => {
    const page = read(pageFixtures[owner]);
    expect(searchOwner(page.report, page.copy)).toBe(owner);
    expect(page.report.panelMayMount).toBe(false);
    const log = read(logFixtures[owner]);
    expect(searchOwner(log.report, log.copy)).toBe(owner);
    expect(log.report.panelMayMount).toBe(true);
  });

  it("gives the page a distinct answer for every verdict, so none has fallen through to another", () => {
    const answers = SEARCH_OWNERS.map((owner) => {
      const { report, copy } = read(pageFixtures[owner]);
      return describeFailure(report, copy);
    });
    expect(answers.every((answer) => answer.length > 0)).toBe(true);
    expect(new Set(answers).size).toBe(SEARCH_OWNERS.length);
  });

  it("gives the log line a distinct answer for every verdict, so none has fallen through to another", () => {
    const answers = SEARCH_OWNERS.map((owner) => {
      const { report, copy } = read(logFixtures[owner]);
      return answerOnly(describeSurvivedMiss(report, copy), scaleOf(report) + MOUNTED);
    });
    expect(answers.every((answer) => answer.length > 0)).toBe(true);
    expect(new Set(answers).size).toBe(SEARCH_OWNERS.length);
  });

  it("names the same program on both surfaces wherever both may name one", () => {
    // The two word the answer differently on purpose — the page is a repair
    // instruction and the log is a record — but they may never point at
    // different programs for the same machine. `none` is excluded because that
    // is the one verdict where the page deliberately names NO update the log
    // names: the three React globals reach the page and cannot reach the log,
    // and the verdict keys on whose copy ran a search, not on who installed a
    // global.
    for (const owner of SEARCH_OWNERS.filter((value) => value !== "none")) {
      const page = read(pageFixtures[owner]);
      const log = read(logFixtures[owner]);
      const pageSentence = describeFailure(page.report, page.copy);
      const logSentence = describeSurvivedMiss(log.report, log.copy);
      expect(pageSentence.includes("Decky Loader")).toBe(logSentence.includes("Decky Loader"));
      expect(pageSentence.includes("Updating Tender")).toBe(logSentence.includes("newer Tender"));
    }
  });

  it.each(SEARCH_OWNERS)("tells the report line whether the %s sentence has already asked for a report", (owner) => {
    // The line under the sentence drops its own request off this predicate, so
    // it must agree with the sentence above it: asked there and not here and
    // the page asks twice in a row, here and not there and it never asks at
    // all. The sentence is searched rather than quoted, because what is being
    // held is whether it asks and not how it is worded.
    const { report, copy } = read(pageFixtures[owner]);
    const sentence = describeFailure(report, copy);
    const asked = sentenceAsksForAReport(report, copy);
    expect(asked).toBe(owner === "none" || owner === "mixed");
    expect(asked).toBe(/please report/i.test(sentence));
    // And every sentence that does NOT ask names an update, which is what lets
    // the other form of the line say "after the update" with no predicate of
    // its own. `mixed` is the one that does both, and takes the short line.
    expect(asked || sentence.includes("Updating")).toBe(true);
  });

  it.each(["standalone", "coexistence"] as const)(
    "asks for a report in its own sentence when nothing at all answered, in the %s bundle",
    (bundle) => {
      const report = checkSteamModules([missedLookup("Focusable", true, "panel"), missedLookup("Tabs", true, "panel")]);
      const copy = readSearchingCopy(report, bundle, () => ({ carries: () => true, version: "v3.2.8" }));
      expect(sentenceAsksForAReport(report, copy)).toBe(true);
      expect(describeFailure(report, copy)).toBe(
        "Tender started before Steam was ready. Restarting Steam is the first thing to try; if this page " +
          "comes back every time, please report it.",
      );
    },
  );
});
