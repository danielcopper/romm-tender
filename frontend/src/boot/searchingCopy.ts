/**
 * Whose copy of `@decky/ui` ran the searches that came back empty.
 *
 * The panel is built out of search predicates over Steam's own minified bundle,
 * and `steamModules.ts` asks whether each one found something. This file answers
 * the question that decides what a miss MEANS: the predicates belong to
 * `@decky/ui`, and which installed copy of that package ran them is not the same
 * in the two panel bundles: the standalone one carries its own, and the
 * coexistence one takes the package from Decky Loader through the `DFL` global.
 * So the same empty search is "update Tender" in one and "update Decky" in the
 * other, and a page that said the first in both would send a user after Tender
 * for a fault in someone else's program — one that is breaking Decky's own
 * interface and its other plugins at the same moment.
 *
 * `boot/` is Steam-specific throughout — what makes it so, and why nothing
 * should be built on it as general start-up code, is stated at `steamGlobals.ts`.
 *
 * ## Which bundle this is, is stamped by the build
 *
 * `BUNDLE_KIND` comes from a module the build serves (see
 * `src/types/virtual-bundle-kind.d.ts`), not from a probe. A probe was the
 * obvious alternative and is wrong: `typeof DFL !== "undefined"` is true of a
 * STANDALONE bundle loaded on a machine where Decky happens to be running, and
 * the page would then credit Decky's copy with work our own copy did — the exact
 * misattribution this file exists to remove, pointing the other way.
 *
 * ## What can be asked of Decky's copy
 *
 * Two different things, on two different axes:
 *
 * - **Does it export the name at all?** Measured on the device against Decky
 *   v3.2.8: `window.DFL` is an ESM module namespace object (137 own keys,
 *   `Symbol.toStringTag` `"Module"`, not extensible), and `in` discriminates —
 *   `"DialogButton" in DFL` is true, a generated non-existent name is false.
 *   A name it does not carry is not a stale Steam lookup at all: it is two
 *   independently installed programs disagreeing about the package.
 * - **Did its search find anything?** That is the value, and it is what
 *   `steamModules.ts` already asks.
 *
 * The first question is put only about a name whose search MISSED, so the two
 * names in `steamModules.ts`'s `UNVERIFIABLE` never reach it — nothing asks
 * them, so they are never among the missing. Asking `in` of them would be
 * meaningful (a name the package does not export is absent whatever its value
 * would have been) and would be a different check from this one: it would
 * report a Decky that lacks a name whose search we never ran.
 *
 * The second state — the name present with an `undefined` value — is an
 * inference from how `@decky/ui` declares its exports (`const X =
 * findModuleExport(...)`, an unmatched predicate yielding `undefined`), not
 * something the device run saw: all 33 names the coexistence bundle reads off
 * `DFL` were present with a real value on that install.
 *
 * ## The version is an enrichment and never a requirement
 *
 * `DeckyPluginLoader.deckyState._versionInfo.current` is an internal field
 * behind an underscore, reached through the loader's state object — and a newer
 * Decky renaming it is EXACTLY the skew this file diagnoses, so the read is
 * allowed to fail and every sentence is complete without it. Only `current` is
 * read: `remote` beside it is GitHub release data about the published version,
 * and a release existing does not mean it is installed. Nothing here calls
 * anything on Decky's objects, writes to them, or reaches the network.
 */

import { BUNDLE_KIND } from "virtual:tender-bundle-kind";

import type { StartupReport } from "./steamModules";

/** Which of the two panel bundles this code was built into. */
export type BundleKind = typeof BUNDLE_KIND;

/** What could be read off Decky Loader, if anything. */
export interface DeckyCopy {
  /**
   * Does Decky's `@decky/ui` export this name?
   *
   * `null` where the question could not be put at all — there is no `DFL`, or
   * reading it threw. An absence has to be demonstrated: a reading that did not
   * happen is not evidence that Decky is missing anything, and treating it as
   * one would put a package disagreement on screen that nobody established. For
   * the same reason a single name the question throws on answers `true`.
   */
  readonly carries: ((name: string) => boolean) | null;
  /** Decky Loader's installed version, or `null` when it could not be read. */
  readonly version: string | null;
}

/** Whose copy of `@decky/ui` ran the searches, and what it could be asked. */
export type SearchingCopy =
  | { readonly owner: "tender" }
  | {
      readonly owner: "decky";
      /**
       * Does Decky's copy export every name the missed searches stand for?
       *
       * `true` also where nothing could be asked — see {@link DeckyCopy.carries}.
       */
      readonly carriesEveryName: boolean;
      readonly version: string | null;
    };

interface DeckyWindow {
  /** Decky's own `@decky/ui` namespace. Present only when Decky is running. */
  DFL?: unknown;
  /** Decky's loader object, whose state carries the version it is running. */
  DeckyPluginLoader?: { deckyState?: { _versionInfo?: { current?: unknown } } };
}

/**
 * Decky Loader's installed version, or `null`.
 *
 * Every step of the path is optional and the whole of it is guarded: these are
 * another program's internals, and a getter on a future Decky may throw where
 * today's is a plain field.
 */
function readDeckyVersion(w: DeckyWindow): string | null {
  try {
    const current = w.DeckyPluginLoader?.deckyState?._versionInfo?.current;
    return typeof current === "string" && current.length > 0 ? current : null;
  } catch {
    return null;
  }
}

/**
 * What Decky's `@decky/ui` carries, or `null` where it cannot be questioned.
 *
 * Guarded on both axes, for the reason stated at {@link readDeckyVersion} and
 * with a sharper cost here: `definePlugin`'s factory reads this BEFORE it
 * returns anything, so a throw takes the failure page and the log line with it
 * — the one screen that tells a stale search apart from a backend that is not
 * running, gone in the moment it exists for. `DFL` is another program's global,
 * so a future Decky may answer for it with a getter rather than store it, and
 * may install something `in` cannot be asked of.
 *
 * A name the question throws on answers `true`, not `false`: a throw
 * demonstrates nothing, and an absence has to be demonstrated.
 */
function readDeckyExports(w: DeckyWindow): DeckyCopy["carries"] {
  try {
    const dfl = w.DFL;
    if (typeof dfl !== "object" || dfl === null) return null;
    return (name: string) => {
      try {
        return name in dfl;
      } catch {
        return true;
      }
    };
  } catch {
    return null;
  }
}

/** Ask Decky Loader what it is and what its `@decky/ui` carries. */
export function readDeckyCopy(): DeckyCopy {
  const w = window as unknown as DeckyWindow;
  return { carries: readDeckyExports(w), version: readDeckyVersion(w) };
}

/**
 * Decide whose copy ran the missed searches, and what state it is in.
 *
 * Both readings are parameters rather than reads inside the body: the bundle
 * kind is a build constant, so a test could otherwise only ever see one of the
 * two answers, and the Decky reading is a thunk so that the standalone bundle —
 * where there is nothing to ask — does not touch `window` at all.
 *
 * One absent name is enough to report the disagreement, and that is a
 * precedence rather than a tally: a name Decky's copy does not export is a
 * demonstrated fact about the two installs, where a name it exports with an
 * empty value is one more stale predicate. The repair it names covers both,
 * since bringing the pair to current updates Decky too.
 */
export function readSearchingCopy(
  report: StartupReport,
  bundle: BundleKind = BUNDLE_KIND,
  readCopy: () => DeckyCopy = readDeckyCopy,
): SearchingCopy {
  if (bundle === "standalone") return { owner: "tender" };
  const decky = readCopy();
  const carries = decky.carries;
  return {
    owner: "decky",
    carriesEveryName: carries === null || report.missingPackageNames.every((name) => carries(name)),
    version: decky.version,
  };
}
