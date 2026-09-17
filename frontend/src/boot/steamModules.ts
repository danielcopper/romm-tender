/**
 * The start-up check: did every search into Steam's own bundle find something?
 *
 * `@decky/ui` is not a component library. Almost everything the panel renders is
 * a SEARCH PREDICATE run over Steam's minified modules, and a predicate that
 * stops matching returns `undefined` with nothing thrown. React then renders
 * `undefined` as an element type and the panel dies mid-tree, or — worse —
 * renders a hole and says nothing. Steam's output has broken such lookups about
 * five times a year for four years, so this is the ordinary failure of a working
 * install after a client update, not a defect.
 *
 * `boot/` is Steam-specific throughout — what makes it so, and why nothing
 * should be built on it as general start-up code, is stated at `steamGlobals.ts`.
 *
 * **Why a check and not a try/catch.** An empty panel looks EXACTLY like "the
 * backend is not running", and those are two completely different faults with
 * two completely different fixes. The check exists so the panel can say which
 * one happened before it renders anything.
 *
 * **One name missing means one predicate went stale. All of them missing means
 * something more basic** — the globals bundle never ran, or Steam's module
 * registry was read before it was complete. That difference is the single fact
 * that leads to a repair, so it is what `describeFailure` reports — together
 * with WHOSE copy of `@decky/ui` ran the stale predicate, which is not the same
 * in the two panel bundles and is answered by `searchingCopy.ts`.
 *
 * ## What this file can and cannot see
 *
 * A name is checkable here only when its VALUE is the lookup's result. Three of
 * the names the panel imports are wrappers that `@decky/ui` always defines and
 * that reach their lookups inside, so they are truthy whether the lookup found
 * anything or not — checking one would be a green light with nothing behind it.
 * They are listed in {@link UNVERIFIABLE} with what makes each one opaque,
 * rather than left out, because a name that is absent from both lists is the
 * only shape `steamModules.test.ts` fails on.
 */

import {
  ButtonItem,
  ConfirmModal,
  DialogButton,
  Field,
  Focusable,
  Menu,
  MenuItem,
  MenuSeparator,
  ModalRoot,
  Navigation,
  PanelSection,
  PanelSectionRow,
  ProgressBar,
  ScrollPanel,
  Spinner,
  Tabs,
  TextField,
  ToggleField,
  showContextMenu,
} from "@decky/ui";

import {
  ControllerGlyph,
  appActionButtonClasses,
  appDetailsClasses,
  basicAppDetailsSectionStylerClasses,
  findSP,
  playSectionClasses,
  quickAccessMenuClasses,
} from "../utils/deckyUiInternals";
import type { SearchingCopy } from "./searchingCopy";

/** One thing the panel depends on, and how to ask whether it is there. */
export interface SteamLookup {
  /** The name as the panel imports it — what the fallback page prints. */
  readonly name: string;
  /** `true` when the search behind it found something. */
  readonly found: () => boolean;
  /**
   * Is this a name `@decky/ui` exports?
   *
   * It decides whether Decky's own copy of the package can be asked about the
   * name at all (`searchingCopy.ts`), and a wrong answer here is a sentence
   * about the wrong program: `SP_REACT` and the other two globals are installed
   * by a bootstrap rather than exported by anything, and `ControllerGlyph` is a
   * predicate `utils/deckyUiInternals.ts` runs itself because the package does
   * NOT export it — so `"ControllerGlyph" in DFL` is false for a Decky that is
   * perfectly in step with us.
   *
   * Written out rather than derived: what would derive it is a sweep of the
   * source for `@decky/ui` imports, which is a filesystem read and cannot
   * happen on a device. `steamModules.test.ts` runs exactly that sweep and holds
   * every flag here against it.
   */
  readonly deckyUiExport: boolean;
}

const truthy = (name: string, read: () => unknown): SteamLookup => ({
  name,
  found: () => Boolean(read()),
  deckyUiExport: true,
});

/** The same question about a name `@decky/ui` does not export — see {@link SteamLookup.deckyUiExport}. */
const truthyUnexported = (name: string, read: () => unknown): SteamLookup => ({
  ...truthy(name, read),
  deckyUiExport: false,
});

/**
 * Every Steam search the panel depends on, asked one at a time.
 *
 * Each entry READS its value when the check runs, not when this module is
 * imported — which is why `truthy` takes a function and not a value. For
 * `@decky/ui`'s members the difference is theoretical: they resolve while its own
 * modules evaluate, which is before this module's body either way. For the three
 * React globals it is not. Those are installed by a separate bundle, and whether
 * that bundle has run by the time this one is imported is the injector's
 * business rather than this file's; a value captured at import would answer for
 * a moment nobody chose.
 */
export const STEAM_LOOKUPS: readonly SteamLookup[] = [
  // Steam's three React globals. Not `@decky/ui` lookups at all — these are
  // what `steamGlobals.ts` installs, and they are checked here because the
  // panel's failure when they are absent is indistinguishable from a stale
  // predicate, while the repair is completely different.
  //
  // **Only `SP_REACTDOM` can ever report missing from inside this bundle**, and
  // the other two are here for a different reader. Measured on `dist/index.js`:
  // `SP_REACT` is read while the bundle is being EVALUATED (line 852,
  // react-icons' `IconContext`) and so is `SP_JSX` (line 4892, a module-scope
  // `SP_JSX.jsx` in `PlatformDetail.tsx`), both before `definePlugin`'s factory
  // can be obtained — so with either unset the bundle throws at import, this
  // check never runs, and nothing it would have said is written anywhere. The
  // coexistence bundle has the same shape at lines 231 and 4271. `SP_REACTDOM`
  // has no import-time read at all.
  //
  // They are listed anyway because these three names are also what
  // `GlobalsReport.installed` answers for, and that report is there for the
  // injector to read out of `dist/globals.js` — a bundle that imports none of
  // this — before it decides whether to load the panel at all (#1900). Nothing
  // reads it yet. Once something does, a panel that would throw at import is
  // never loaded, which is the whole value of the guard in `steamGlobals.ts`
  // that keeps a missed JSX search from reporting as a hit.
  truthyUnexported("SP_REACT", () => window.SP_REACT),
  truthyUnexported("SP_REACTDOM", () => window.SP_REACTDOM),
  truthyUnexported("SP_JSX", () => window.SP_JSX),

  // Steam's components, each one a predicate over its minified bundle.
  truthy("ButtonItem", () => ButtonItem),
  truthy("ConfirmModal", () => ConfirmModal),
  truthy("DialogButton", () => DialogButton),
  truthy("Field", () => Field),
  truthy("Focusable", () => Focusable),
  truthy("Menu", () => Menu),
  truthy("MenuItem", () => MenuItem),
  truthy("MenuSeparator", () => MenuSeparator),
  truthy("ModalRoot", () => ModalRoot),
  truthy("PanelSection", () => PanelSection),
  truthy("PanelSectionRow", () => PanelSectionRow),
  truthy("ProgressBar", () => ProgressBar),
  truthy("ScrollPanel", () => ScrollPanel),
  truthy("Spinner", () => Spinner),
  truthy("Tabs", () => Tabs),
  truthy("TextField", () => TextField),
  truthy("ToggleField", () => ToggleField),
  truthy("showContextMenu", () => showContextMenu),

  // Not truthiness: `@decky/ui` declares `Navigation` as an empty object and
  // fills it from a lookup inside a `try`, so a miss leaves an object that is
  // perfectly truthy and does nothing.
  { name: "Navigation", found: () => Object.keys(Navigation).length > 0, deckyUiExport: true },

  // Asked by CALLING it: `findSP` is always a function and answers `undefined`
  // when its probe missed, which is why `@decky/ui`'s own callers write
  // `findSP() || window` around it.
  { name: "findSP", found: () => findSP() !== undefined, deckyUiExport: true },

  // The class maps and the glyph, which `deckyUiInternals.ts` already types
  // honestly. A missing class map does not throw — it styles nothing, which is
  // a panel that renders and looks wrong.
  truthy("appActionButtonClasses", () => appActionButtonClasses),
  truthy("appDetailsClasses", () => appDetailsClasses),
  truthy("basicAppDetailsSectionStylerClasses", () => basicAppDetailsSectionStylerClasses),
  truthy("playSectionClasses", () => playSectionClasses),
  truthy("quickAccessMenuClasses", () => quickAccessMenuClasses),
  // `@decky/ui` does not export the controller glyph at all —
  // `deckyUiInternals.ts` reaches it with a `findModule` predicate of our own,
  // so it is our search in both bundles.
  truthyUnexported("ControllerGlyph", () => ControllerGlyph),
];

/**
 * Names the panel imports from `@decky/ui` that this check cannot answer for,
 * each with what makes it opaque.
 *
 * Listed rather than omitted: `steamModules.test.ts` sweeps every value the
 * panel imports from the package and fails on any name that is in neither this
 * list nor {@link STEAM_LOOKUPS} nor {@link PACKAGE_OWN}, so a new import has to
 * be classified before it can ship. Reaching what is behind one of these would
 * mean re-running its predicate here, which is the thing #1899 decided not to do
 * (`@decky/ui` owns the predicates, and we do not keep a second copy).
 */
export const UNVERIFIABLE: Readonly<Record<string, string>> = {
  DropdownItem:
    "an arrow function `@decky/ui` always defines; the lookup it renders " +
    "(`DropdownItemInternal`) is module-private, so the export is truthy either way",
  showModal:
    "an arrow function `@decky/ui` always defines; the lookup it renders " +
    "(`showModalRaw`, `dist/components/Modal.js:3`) is module-private, so the export is " +
    "truthy whether or not that search found anything",
  useQuickAccessVisible:
    "a hook `@decky/ui` always defines; what it reads is a Steam store it reaches " +
    "during render, so an import-time read says nothing about it",
};

/**
 * Names the panel imports from `@decky/ui` that are the package's own code.
 *
 * These are not searches into Steam's bundle and cannot go missing short of the
 * package itself failing to load, which is a different fault with a different
 * symptom — the bundle throws on import rather than rendering an empty panel.
 */
export const PACKAGE_OWN: Readonly<Record<string, string>> = {
  afterPatch: "the package's own patcher",
  createReactTreePatcher: "the package's own tree patcher",
  findInReactTree: "the package's own tree walk",
  findModule: "the module-cache reader itself",
  GamepadButton: "a TypeScript enum, compiled into the bundle",
};

/** What the check found. */
export interface StartupReport {
  /** Did every search answer? */
  readonly ok: boolean;
  /** The names that did not, in the order they are checked. */
  readonly missing: readonly string[];
  /**
   * The subset of {@link missing} that `@decky/ui` exports.
   *
   * These are the only names a copy of the package can be asked about, which is
   * what `searchingCopy.ts` asks Decky's. The rest of `missing` — the three
   * globals, the glyph — would answer "not exported" for a Decky in perfect
   * step with us.
   *
   * Empty while {@link missing} is not is therefore a statement in its own
   * right: nothing that missed was a search either copy of the package ran, and
   * {@link describeFailure} answers that before it asks whose copy anything
   * belongs to.
   */
  readonly missingPackageNames: readonly string[];
  /** How many searches were asked. */
  readonly checked: number;
}

/** Ask every search whether it found something. */
export function checkSteamModules(lookups: readonly SteamLookup[] = STEAM_LOOKUPS): StartupReport {
  const missed = lookups.filter((lookup) => !lookup.found());
  return {
    ok: missed.length === 0,
    missing: missed.map((lookup) => lookup.name),
    missingPackageNames: missed.filter((lookup) => lookup.deckyUiExport).map((lookup) => lookup.name),
    checked: lookups.length,
  };
}

/**
 * The one sentence that leads to a repair.
 *
 * All of them missing is not "many predicates broke at once" — it is the
 * globals bundle never having run, or Steam's registry having been read before
 * it was complete. That answer is about the bootstrap rather than about either
 * copy of `@decky/ui`, so the searching copy does not enter it.
 *
 * Some of them missing is a stale predicate, and then WHOSE predicate decides
 * the repair: our own bundled copy, Decky Loader's copy (which is also breaking
 * Decky's interface and its other plugins at that moment), or a Decky copy that
 * does not carry the name at all, which is a disagreement about the package
 * rather than about Steam. `searchingCopy.ts` decides which; this words it.
 *
 * Unless none of what missed is a search either copy ran. {@link STEAM_LOOKUPS}
 * carries names `@decky/ui` does not export — the React globals and the glyph,
 * marked by {@link SteamLookup.deckyUiExport} — and a miss confined to those
 * belongs to no copy of the package,
 * so the sentence names none. It claims nothing further: which program installed
 * the globals on a machine running both is #1900's question and not one this
 * page may decide, and there is no repair to offer that would follow from an
 * answer we do not have.
 */
export function describeFailure(report: StartupReport, copy: SearchingCopy): string {
  if (report.ok) return "";
  if (report.missing.length === report.checked) {
    return (
      "None of the searches into Steam's interface found anything. That is not a run of " +
      "broken lookups — it means Steam's module registry was not readable when Tender " +
      "read it, or Tender's React bootstrap never ran."
    );
  }
  const scale = `${report.missing.length} of ${report.checked} searches into Steam's interface found nothing. `;
  if (report.missingPackageNames.length === 0) {
    return scale + "None of them is a name @decky/ui exports, so neither copy of the package ran them.";
  }
  if (copy.owner === "tender") {
    return (
      scale +
      "Tender's own copy of @decky/ui ran them, so a Steam client update has moved what " +
      "this version of Tender looks for. A newer Tender is the repair."
    );
  }
  const decky = copy.version === null ? "Decky Loader" : `Decky Loader ${copy.version}`;
  if (!copy.carriesEveryName) {
    return (
      scale +
      `Tender reads them from ${decky}'s copy of @decky/ui, and that copy does not carry ` +
      "some of the names Tender asks it for — the two disagree about the package rather " +
      "than about Steam. Bringing both Tender and Decky Loader to their current versions " +
      "is the repair."
    );
  }
  return (
    scale +
    `${decky}'s copy of @decky/ui ran them, not Tender's own, so a Steam client update has ` +
    "moved what Decky looks for — Decky's own interface and its other plugins are affected " +
    "the same way. A newer Decky Loader is the repair."
  );
}
