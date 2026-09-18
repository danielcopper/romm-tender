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
 * **Did every search answer, and may the panel mount, are two questions.** They
 * used to be one, and that spent the whole user interface on a decoration: a
 * missing `ControllerGlyph` was enough to take the panel off the air, in a
 * codebase where `WidePage.tsx` already draws `‹ Back` in its place. So each
 * entry states what its absence costs ({@link SteamLookup.absenceCost}), the
 * panel mounts when nothing that missed was needed to render it, and
 * {@link describeSurvivedMiss} puts the miss in the log, which is then the only
 * thing that says so.
 *
 * ## What this file can and cannot see
 *
 * **Nothing this check asks is answered by Steam's RUNTIME STATE.** Every entry
 * below reads Steam's module registry or a global our own bootstrap installed —
 * one registry, the same one in Big Picture and in the desktop client — and not
 * what Steam has mounted or focused. A name answered that way has no verdict to
 * give at the moment this runs and belongs in {@link ASKED_LIVE}, whose entries
 * are asked by their own consumers when they are needed.
 *
 * A name is checkable here only when its VALUE is the lookup's result. Two of
 * the names the panel imports are wrappers that `@decky/ui` always defines and
 * that reach their lookups inside, so they are truthy whether the lookup found
 * anything or not — checking one would be a green light with nothing behind it.
 * They are listed in {@link UNVERIFIABLE} with what makes each one opaque,
 * rather than left out, because a name that is absent from all four lists is the
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
  playSectionClasses,
  quickAccessMenuClasses,
} from "../utils/deckyUiInternals";
import type { SearchingCopy } from "./searchingCopy";

/**
 * What one name's absence costs.
 *
 * - `panel` — the panel cannot be trusted to render without it, so nothing
 *   mounts. This is the status quo answer, and staying here costs no evidence:
 *   it is what the check did for every name.
 * - `appearance` — the panel renders and only looks poorer, because the one
 *   place that reads the name already draws something else when it is missing.
 * - `diagnostic` — nothing a user can see changes at all. Every read of the
 *   name is a diagnostic that already prints `UNDEFINED` in its place, so what
 *   the absence costs is a debug line naming a class instead of the class.
 *
 * Moving a name off `panel` is what needs evidence, one name at a time: its
 * every consumer, read, and found either to cope with the absence or to be a
 * diagnostic. **Nothing in the program branches on the difference between the
 * last two** — `checkSteamModules`'s `!== "panel"` is the only reading of this
 * field there is — so what the value records today is WHY a name was moved off
 * blocking, not an answer anything consults. They are kept apart because they
 * are different questions, and a name that answered the wrong one would be
 * hard to catch later: a decoration whose absence a reader can see is not a
 * name whose absence nothing renders at all. Neither keeps the panel off the
 * air, which is what the check asks separately
 * ({@link StartupReport.panelMayMount}).
 *
 * Nothing derives this. The type is the mechanism — the field is required, so a
 * new entry does not compile until somebody answers — and what
 * `steamModules.test.ts` holds is not the set but the property the log sentence
 * rests on ({@link describeSurvivedMiss}).
 */
export type AbsenceCost = "panel" | "appearance" | "diagnostic";

/** One thing the panel depends on, and how to ask whether it is there. */
export interface SteamLookup {
  /** The name as the panel imports it — what the fallback page prints. */
  readonly name: string;
  /** `true` when the search behind it found something. */
  readonly found: () => boolean;
  /**
   * What this name's absence costs — see {@link AbsenceCost}.
   *
   * Required rather than defaulted, and the type is the whole of the mechanism:
   * a new entry does not compile until it says which it is, where a default
   * would let one arrive without anybody deciding.
   */
  readonly absenceCost: AbsenceCost;
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

const truthy = (name: string, absenceCost: AbsenceCost, read: () => unknown): SteamLookup => ({
  name,
  found: () => Boolean(read()),
  deckyUiExport: true,
  absenceCost,
});

/** The same question about a name `@decky/ui` does not export — see {@link SteamLookup.deckyUiExport}. */
const truthyUnexported = (name: string, absenceCost: AbsenceCost, read: () => unknown): SteamLookup => ({
  ...truthy(name, absenceCost, read),
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
  // the other two are here for a different reader. Measured by parsing both
  // built artefacts and keeping the references that stand outside every function
  // body: `SP_REACT` and `SP_JSX` are each read at one such site and
  // `SP_REACTDOM` at none, in both bundles. In `dist/index.js` the two sites are
  // lines 852 (react-icons' `IconContext`) and 4969 (a module-scope `SP_JSX.jsx`
  // from `PlatformDetail.tsx`); in `dist/index-coexistence.js` the same two are
  // at 231 and 4348. All four run before `definePlugin`'s factory can be
  // obtained, so with either global unset the bundle throws at import, this
  // check never runs, and nothing it would have said is written anywhere.
  //
  // They are listed anyway because these three names are also what
  // `GlobalsReport.installed` answers for, and that report is there for the
  // injector to read out of `dist/globals.js` — a bundle that imports none of
  // this — before it decides whether to load the panel at all (#1900). Nothing
  // reads it yet. Once something does, a panel that would throw at import is
  // never loaded, which is the whole value of the guard in `steamGlobals.ts`
  // that keeps a missed JSX search from reporting as a hit.
  truthyUnexported("SP_REACT", "panel", () => window.SP_REACT),
  truthyUnexported("SP_REACTDOM", "panel", () => window.SP_REACTDOM),
  truthyUnexported("SP_JSX", "panel", () => window.SP_JSX),

  // Steam's components, each one a predicate over its minified bundle.
  truthy("ButtonItem", "panel", () => ButtonItem),
  truthy("ConfirmModal", "panel", () => ConfirmModal),
  truthy("DialogButton", "panel", () => DialogButton),
  truthy("Field", "panel", () => Field),
  truthy("Focusable", "panel", () => Focusable),
  truthy("Menu", "panel", () => Menu),
  truthy("MenuItem", "panel", () => MenuItem),
  truthy("MenuSeparator", "panel", () => MenuSeparator),
  truthy("ModalRoot", "panel", () => ModalRoot),
  truthy("PanelSection", "panel", () => PanelSection),
  truthy("PanelSectionRow", "panel", () => PanelSectionRow),
  truthy("ProgressBar", "panel", () => ProgressBar),
  truthy("ScrollPanel", "panel", () => ScrollPanel),
  truthy("Spinner", "panel", () => Spinner),
  truthy("Tabs", "panel", () => Tabs),
  truthy("TextField", "panel", () => TextField),
  truthy("ToggleField", "panel", () => ToggleField),
  truthy("showContextMenu", "panel", () => showContextMenu),

  // Not truthiness: `@decky/ui` declares `Navigation` as an empty object and
  // fills it from a lookup inside a `try`, so a miss leaves an object that is
  // perfectly truthy and does nothing.
  { name: "Navigation", found: () => Object.keys(Navigation).length > 0, deckyUiExport: true, absenceCost: "panel" },

  // The class maps and the glyph, which `deckyUiInternals.ts` already types
  // honestly. A missing class map does not throw: every read is either
  // optional-chained or guarded by one that is — `qamExpansion.ts:39` is a
  // plain member read, reached only from the true arm of line 38's `?.` test.
  //
  // What the absence costs is per map and is a table rather than a paragraph,
  // because a paragraph is summarised and a cell is checked. Every row below
  // was swept from every non-test module under `frontend/src/`; the enclosing
  // function is named because two of the maps do different things at different
  // reads, and a cell that named only the file would hide that.
  //
  // | Map | Read at | Falls back to | What its absence costs |
  // | --- | --- | --- | --- |
  // | `appActionButtonClasses` | every button branch of `CustomPlayButton` (`bigpicture/CustomPlayButton.tsx:1426-1860`), for `PlayButtonContainer` / `PlayButton` / `Green` / `Throbber` | the class is simply absent — dropped from the list (`.filter(Boolean)`), replaced by `""`, or left undefined where it is the whole of the prop | our own play and download buttons keep their shape and lose Steam's, so a box of ours no longer matches the ones beside it |
  // | `appDetailsClasses` | `InnerContainer` in `findInsertionPoint` (`bigpicture/patches/gameDetailPatch.tsx:88`); `AppDetailsOverviewPanel` in the patch handler `registerGameDetailPatch` installs (`:231`, and its debug line `:242`) | `findInsertionPoint` returns `undefined`; the wrapper takes `""` | `InnerContainer` is a mark on a node of STEAM's, so without it the handler finds no insertion point and returns the tree untouched — no Tender section on the game page at all. Without `AppDetailsOverviewPanel` the wrapper is still inserted, outside `InnerContainer`'s flex and scroll layout |
  // | `basicAppDetailsSectionStylerClasses` | `PlaySection` in an unnamed `useEffect` of `CustomPlayButton` (`CustomPlayButton.tsx:220`) and on our own row in `RomMPlaySection` (`bigpicture/RomMPlaySection.tsx:1048`); also `dumpTree` (`gameDetailPatch.tsx:145-154`) | the effect does not call `hideNativePlaySection`; the row takes `""`; the dump prints `UNDEFINED` | the same member is both kinds at once: it names a node of Steam's for the hide, so Steam's own play section stays on screen beside ours, and a node of ours for the row's styling |
  // | `playSectionClasses` | `Container` in `dumpTree` alone (`gameDetailPatch.tsx:133-141`) | the dump prints `UNDEFINED` and skips the tree search it guards | one line of a debug dump that runs at most once per load names no class. Nothing a user can see |
  // | `quickAccessMenuClasses` | `TabGroupPanel` at module scope (`utils/qamExpansion.ts:38-40`), read into the selectors of the injected sheet (`:92-93`); `ActiveTab` in `useWideQamPanel`'s effect (`:175`) | `TAB_PANEL_SELECTOR` becomes `PANEL_ID_SELECTOR`, the panel's id; `deckyTabActive` defaults to true (`:182`, the default argued at `:178-181`) and the `MutationObserver` guarded at `:191` is never constructed | the sheet matches the panel by id instead of by class, and the expansion is taken whether or not Decky's tab is the active one and is not re-synced on a tab switch — a leaked expansion the QAM-close, unmount and dismount paths still clear |
  //
  // Every map but `playSectionClasses` blocks the panel, and that is the status
  // quo rather than a reading of the table: see {@link AbsenceCost} for what
  // moving one off it takes.
  truthy("appActionButtonClasses", "panel", () => appActionButtonClasses),
  truthy("appDetailsClasses", "panel", () => appDetailsClasses),
  truthy("basicAppDetailsSectionStylerClasses", "panel", () => basicAppDetailsSectionStylerClasses),
  // The only one of the five maps that styles nothing at all — the table above
  // has its reads and what they cost. What is not in the table: it is a
  // `@decky/ui` export, which is what separates it from the glyph below. In the
  // coexistence bundle the search behind it is DECKY's, so its repair is not
  // Tender's to name.
  truthy("playSectionClasses", "diagnostic", () => playSectionClasses),
  truthy("quickAccessMenuClasses", "panel", () => quickAccessMenuClasses),
  // `@decky/ui` does not export the controller glyph at all —
  // `deckyUiInternals.ts` reaches it with a `findModule` predicate of our own,
  // so it is our search in both bundles.
  //
  // The one name whose absence is cosmetic. `layout/WidePage.tsx` is its only
  // consumer and already renders `‹ Back` where the glyph would be, so a miss
  // costs one chip its button picture and nothing else.
  truthyUnexported("ControllerGlyph", "appearance", () => ControllerGlyph),
];

/**
 * Names the panel imports from `@decky/ui` that this check cannot answer for,
 * each with what makes it opaque.
 *
 * Listed rather than omitted: `steamModules.test.ts` sweeps every value the
 * panel imports from the package and fails on any name that is in none of this
 * list, {@link STEAM_LOOKUPS}, {@link ASKED_LIVE} and {@link PACKAGE_OWN}, so a
 * new import has to be classified before it can ship. Reaching what is behind
 * one of these would mean re-running its predicate here, which is the thing
 * #1899 decided not to do (`@decky/ui` owns the predicates, and we do not keep
 * a second copy).
 */
export const UNVERIFIABLE: Readonly<Record<string, string>> = {
  DropdownItem:
    "an arrow function `@decky/ui` always defines; the lookup it reaches inside " +
    "(`DropdownItemInternal`) is module-private, so the export is truthy either way",
  showModal:
    "an arrow function `@decky/ui` always defines; the lookup it reaches inside " +
    "(`showModalRaw`, `dist/components/Modal.js:3`) is module-private, so the export is " +
    "truthy whether or not that search found anything",
};

/**
 * Names the panel imports from `@decky/ui` whose answer is Steam's RUNTIME
 * STATE, asked by their own consumers at the moment they are needed.
 *
 * `findSP` is a real search that can come back empty, which is what keeps it out
 * of {@link UNVERIFIABLE}; `useQuickAccessVisible` is a hook the package always
 * defines, and is here because what it reads is the same trees.
 *
 * What separates both from {@link STEAM_LOOKUPS} is the axis they search. A
 * module registry is one registry, the same one in Big Picture and in the
 * desktop client. What Steam has mounted and what has focus is neither — the
 * same question answers differently a second later — so a reading taken when the
 * injector evaluates this bundle answers for a moment nobody chose, and refusing
 * to mount the panel on it reports a fault that does not exist.
 *
 * `steamModules.test.ts` derives this set from `@decky/ui`'s own shipped source
 * rather than trusting the list, so a name whose implementation starts reading
 * the navigation trees cannot stay in {@link STEAM_LOOKUPS}.
 */
export const ASKED_LIVE: Readonly<Record<string, string>> = {
  findSP:
    "answers `window` when `document.title` is `SP` and otherwise searches the focus " +
    "controller's active (else last active) context for the `GamepadUI_Full_Root` / " +
    "`root_1_` navigation tree (`dist/utils/index.js`) — a context that carries no Big " +
    "Picture tree until Big Picture has been opened. Its two consumers, " +
    "`utils/styleInjector.ts`'s `hideNativePlaySection` and `showNativePlaySection`, ask " +
    "it when the play button mounts and unmounts on a game page, and do nothing when it " +
    "answers nothing",
  useQuickAccessVisible:
    "a hook `@decky/ui` always defines; it reaches the same navigation trees through its " +
    "own `getQuickAccessWindow` during render (`dist/custom-hooks/useQuickAccessVisible.js`), " +
    "so a reading taken at import says nothing about it",
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
  /**
   * Did every search find something?
   *
   * A report of the machine, and nothing follows from it on its own: some names
   * can be absent with the panel perfectly able to run. What may mount is
   * {@link panelMayMount}, and the two are separate fields because one field
   * answering both is what took the whole interface off the air for a glyph.
   */
  readonly everySearchAnswered: boolean;
  /**
   * May the panel mount?
   *
   * `true` while nothing that missed was needed to render it — including when
   * nothing missed at all. `false` is the fallback page: a name the pages
   * below are written against is not there, and a half-working panel acts on
   * what it cannot see.
   */
  readonly panelMayMount: boolean;
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
    everySearchAnswered: missed.length === 0,
    panelMayMount: missed.every((lookup) => lookup.absenceCost !== "panel"),
    missing: missed.map((lookup) => lookup.name),
    missingPackageNames: missed.filter((lookup) => lookup.deckyUiExport).map((lookup) => lookup.name),
    checked: lookups.length,
  };
}

/**
 * Which copy of `@decky/ui` the missed searches belong to — the one verdict,
 * worded separately by the page and by the log line.
 *
 * - `none` — nothing that missed is a name the package exports, so neither
 *   copy ran any of them.
 * - `tender` — our own bundled copy ran them.
 * - `disagreement` — Decky's copy does not carry a name Tender asks it for:
 *   two separately installed programs disagreeing about the package.
 * - `mixed` — some of what missed is the package's and some is not. Decky's
 *   copy carries every name asked of it and its searches for them still came
 *   back empty, so it is demonstrated stale; who the REST implicates depends on
 *   which name it is, and the two surfaces are not in the same position about
 *   that (see {@link describeFailure} and {@link describeSurvivedMiss}).
 * - `decky` — every missed search is one Decky Loader's copy ran, and that
 *   copy carries all of them.
 *
 * **Both surfaces read this rather than deciding for themselves.** They used to
 * branch on the same facts twice, and the copies had already drifted apart: the
 * log had the `mixed` case and the page did not, so on the page a missed
 * `SP_REACTDOM` beside any `@decky/ui` name fell through to `decky` and told
 * the user Decky's copy had run searches Decky ran none of. A verdict two
 * surfaces word is a verdict they cannot answer differently.
 *
 * The ORDER these are asked in is {@link searchOwner}'s, and is stated there:
 * this list is a vocabulary and nothing reads its order.
 */
export const SEARCH_OWNERS = ["none", "tender", "disagreement", "mixed", "decky"] as const;

/** One of {@link SEARCH_OWNERS}, which states what each answer means. */
export type SearchOwner = (typeof SEARCH_OWNERS)[number];

/**
 * Read the verdict off one report and one reading of the machine.
 *
 * **The chain below is a precedence, not a tally**: each line answers only what
 * the ones above it did not, so moving any of them changes what it answers.
 * Two of the orderings are decisions rather than consequences of the shapes.
 *
 * `none` is asked first because {@link STEAM_LOOKUPS} carries names `@decky/ui`
 * does not export at all ({@link SteamLookup.deckyUiExport}) — the three React
 * globals and the glyph — and a miss confined to those belongs to no copy of
 * the package, so no line below may name one.
 *
 * `disagreement` is asked before `mixed` because a name Decky's copy does not
 * export is a fact about the two INSTALLS, where a name it exports with an
 * empty value is a search result whose cause — Steam having moved what the
 * predicate looks for — is inferred. Its repair covers whatever else went stale
 * beside it, since bringing the pair to current updates both programs, which is
 * why the sentence for it must not claim the disagreement is the whole of what
 * happened.
 *
 * The third ordering is the type's rather than this function's: only the
 * `decky` arm of {@link SearchingCopy} carries `carriesEveryName` at all, so
 * `tender` is answered before anything reads that field.
 */
export function searchOwner(report: StartupReport, copy: SearchingCopy): SearchOwner {
  if (report.missingPackageNames.length === 0) return "none";
  if (copy.owner === "tender") return "tender";
  if (!copy.carriesEveryName) return "disagreement";
  if (report.missingPackageNames.length < report.missing.length) return "mixed";
  return "decky";
}

/**
 * Decky Loader, named with the version it is running where that could be read.
 *
 * Every sentence below is complete without the version, which is why the
 * unreadable case is the bare name rather than a missing one: `_versionInfo` is
 * another program's internal and a newer Decky renaming it is exactly the skew
 * being diagnosed (`searchingCopy.ts`). A copy that is not Decky's has no
 * version to read and answers the bare name too. Both callers hoist this above
 * their switch, so every verdict asks and the two that name no program discard
 * the answer unread — which is what keeps the bare-name fallback harmless
 * rather than any branch declining to ask.
 */
const deckyName = (copy: SearchingCopy): string =>
  copy.owner === "decky" && copy.version !== null ? `Decky Loader ${copy.version}` : "Decky Loader";

/**
 * The one sentence that leads to a repair, for the page that replaces the panel.
 *
 * Asked only where the panel may not mount, which is the moment that page
 * exists for; a miss the panel survives is worded by
 * {@link describeSurvivedMiss} instead, in the log.
 *
 * All of them missing is not "many predicates broke at once" — it is the
 * globals bundle never having run, or Steam's registry having been read before
 * it was complete. That answer is about the bootstrap rather than about either
 * copy of `@decky/ui`, so the searching copy does not enter it.
 *
 * Some of them missing is a stale predicate, and then whose predicate decides
 * the repair: {@link searchOwner} answers that, and this words each of its
 * answers for a user who is looking at a page instead of a panel.
 *
 * `none` and `mixed` stay silent about a repair — the first about the whole of
 * what missed, the second about part of it — and the reason is the same in both.
 * {@link STEAM_LOOKUPS} carries names `@decky/ui` does not export, and for the
 * three React globals among them there is no repair to offer: which
 * program installed them on a machine running both is #1900's question and not
 * one this page may decide — and in the standalone bundle, having that answer
 * would not settle it anyway, since a missing `SP_REACTDOM` there is
 * `globals.js` never having run or the ReactDOM predicate in `steamGlobals.ts`
 * having gone stale, a load-order fault and a version fault behind one symptom
 * with different repairs. So `none` names no repair at all, and `mixed` names
 * one for the package's share of the miss and stays silent about the rest.
 *
 * `ControllerGlyph` can appear in that silence too, and there it is a real loss
 * — `utils/deckyUiInternals.ts` reaches it with a `findModule` predicate of OURS
 * in both bundles, so a newer Tender is its repair, and keying the branch on
 * whose COPY ran the search cannot say so. What bounds that is only that the
 * glyph never brings this page up by itself: its absence costs appearance, so
 * it arrives beside a name that does cost the panel. Which verdict it lands in
 * follows from WHICH name that is, and both happen — beside a global it is the
 * `none` answer, whose silence is right anyway; beside a package name Decky's
 * copy carries it is `mixed`, and the glyph is that answer's unnamed rest with
 * no global anywhere in the miss. Its own sentence is printed by
 * {@link describeSurvivedMiss}.
 */
export function describeFailure(report: StartupReport, copy: SearchingCopy): string {
  if (report.panelMayMount) return "";
  if (report.missing.length === report.checked) {
    return (
      "None of the searches into Steam's interface found anything. That is not a run of " +
      "broken lookups — it means Steam's module registry was not readable when Tender " +
      "read it, or Tender's React bootstrap never ran."
    );
  }
  const scale = `${report.missing.length} of ${report.checked} searches into Steam's interface found nothing. `;
  const decky = deckyName(copy);
  switch (searchOwner(report, copy)) {
    case "none":
      return scale + "None of them is a name @decky/ui exports, so neither copy of the package ran them.";
    case "tender":
      return (
        scale +
        "Tender's own copy of @decky/ui ran them, so a Steam client update has moved what " +
        "this version of Tender looks for. A newer Tender is the repair."
      );
    case "disagreement":
      return (
        scale +
        `Tender reads them from ${decky}'s copy of @decky/ui, and that copy does not carry ` +
        "some of the names Tender asks it for — two separately installed programs disagreeing " +
        "about the package, whatever else went stale beside it. Bringing both Tender and Decky " +
        "Loader to their current versions is the repair."
      );
    case "mixed":
      return (
        scale +
        `${decky}'s copy of @decky/ui ran some of them, not Tender's own, so a Steam client ` +
        "update has moved what Decky looks for — Decky's own interface and its other plugins " +
        "are affected the same way, and a newer Decky Loader is the repair for those. The rest " +
        "are not names @decky/ui exports, so neither copy of the package ran them."
      );
    case "decky":
      return (
        scale +
        `${decky}'s copy of @decky/ui ran them, not Tender's own, so a Steam client update has ` +
        "moved what Decky looks for — Decky's own interface and its other plugins are affected " +
        "the same way. A newer Decky Loader is the repair."
      );
  }
}

/**
 * The sentence for a miss the panel survives — the log's, not the page's.
 *
 * The panel mounts, so nothing on screen says anything happened: this line is
 * the only record that a search went stale, and the next reader of it is
 * whoever is asked why a button lost its glyph, or why a debug dump prints
 * `UNDEFINED` where a class name belongs.
 *
 * It answers the same question the page answers — {@link searchOwner}'s, read
 * from the same verdict — rather than naming a repair of its own. It used to
 * name one unconditionally ("a newer Tender"), which was sound only while
 * nothing that could arrive here was a name the package exports.
 * `playSectionClasses` can now, and in the coexistence bundle the search behind
 * it is DECKY's, so that sentence would have sent the user after the wrong
 * program.
 *
 * Both surfaces answer every verdict; where they come apart is the REPAIR. On
 * `none` this line names one and the page names none at all: nothing that
 * missed is a name `@decky/ui` exports, so every one of them is a search Tender
 * runs with a module probe of its own and a newer Tender is the repair. The
 * page has to stay silent there because the three React globals reach ITS
 * `none`, and which program installed those on a machine running both is
 * #1900's question. They cannot reach HERE — their absence costs the panel —
 * and that is the property `steamModules.test.ts` holds, rather than the short
 * set of names it happens to produce today. `mixed` is the other one: here it
 * names a repair covering both programs, where the page names Decky's and stays
 * silent about the rest, for the same reason.
 */
export function describeSurvivedMiss(report: StartupReport, copy: SearchingCopy): string {
  if (report.everySearchAnswered || !report.panelMayMount) return "";
  return (
    `${report.missing.length} of ${report.checked} searches into Steam's interface found nothing. ` +
    "Nothing that missed is needed to render the panel, so Tender has started. " +
    describeSurvivedSearches(report, copy)
  );
}

/** Whose searches missed, and the repair that follows — for {@link describeSurvivedMiss}. */
function describeSurvivedSearches(report: StartupReport, copy: SearchingCopy): string {
  const decky = deckyName(copy);
  switch (searchOwner(report, copy)) {
    case "none":
      return "None of them is a name @decky/ui exports — Tender runs these searches itself, so a newer Tender is the repair.";
    case "tender":
      return "Tender ran these searches, so a newer Tender is the repair.";
    case "disagreement":
      return (
        `${decky}'s copy of @decky/ui does not carry some of the names Tender asks it for — two ` +
        "separately installed programs disagreeing about the package, whatever else went stale " +
        "beside it. Bringing both Tender and Decky Loader to their current versions is the repair."
      );
    case "mixed":
      return (
        `Tender ran some of them itself and ${decky}'s copy of @decky/ui the rest, so both went ` +
        "stale: a probe of Tender's own missed, and so did a search Decky's copy ran. Bringing " +
        "both Tender and Decky Loader to their current versions is the repair."
      );
    case "decky":
      return (
        `${decky}'s copy of @decky/ui ran them, not Tender's own, so a newer Decky Loader is the ` +
        "repair — Decky's own interface and its other plugins are affected the same way."
      );
  }
}
