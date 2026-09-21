/**
 * Steam's React, ReactDOM and JSX runtime, installed by Tender instead of by
 * Decky Loader.
 *
 * ## `boot/` is about STEAM, not about starting up
 *
 * This file is the entry point of `dist/globals.js` and the reason the directory
 * exists, so the statement about the directory lives here; `steamModules.ts` and
 * `StartupFailurePanel.tsx` point at it.
 *
 * Everything under `boot/` is specific to a frontend running **inside Steam's
 * own client**, and the specificity is concrete rather than a matter of
 * emphasis: this module reads `window.webpackChunksteamui`, Steam's own module
 * registry, and writes `SP_REACT` / `SP_REACTDOM` / `SP_JSX`, three globals that
 * exist only because a plugin loader puts them there; `steamModules.ts` asks
 * whether a list of searches into that same registry found anything, and
 * `StartupFailurePanel.tsx` words the answer for a user of the Quick Access
 * menu. A frontend that were not Steam's would use none of it — it would take
 * React from npm and have nothing to look up.
 *
 * **So this is not a general start-up package and nothing should be built on it
 * as one.** There is no second frontend; if one ever arrives, the thing to reuse
 * is the idea of refusing to mount rather than any file here.
 *
 * ## What this module does
 *
 * Steam does not define `SP_REACT`, `SP_REACTDOM` or `SP_JSX` — Decky's loader
 * does, and until #1896 that was the only reason they existed on a device. The
 * panel bundle maps `react`, `react-dom` and `react/jsx-runtime` onto those
 * three names, so without them it cannot load at all. This module is the
 * smallest thing that makes it load.
 *
 * **It carries `@decky/ui`'s module sweep, and the guard below does not stop
 * it.** `@decky/ui/dist/webpack` calls `initModuleCache()` unguarded at module
 * scope, so importing this file re-executes every module in Steam's registry
 * whatever `installGlobals` then decides — the short-circuit is in the FUNCTION
 * and the sweep is in the IMPORT, which ESM runs first. That is harmless unless
 * something is already RENDERING from those modules, which is the condition
 * measured on the device; Decky's interface is such a consumer, Steam's own is
 * not. So this bundle must not be loaded beside a running Decky. Who loads which
 * bundle is #1900's decision, not this file's.
 *
 * **This is its own bundle (`dist/globals.js`), and that is not tidiness.**
 * `@decky/ui`'s component half reads React internals while its own modules are
 * evaluated, so it cannot sit in the import graph of the module that creates
 * the globals — ESM evaluates the whole graph before any body runs, and an
 * import cannot be ordered after set-up code in the same file. Only
 * `@decky/ui/dist/webpack` is imported here: the module-cache machinery without
 * the components. Decky splits it in the same place for the same reason.
 *
 * **ONE bootstrap with a live branch, not two artifacts.** The two panel
 * bundles are two artifacts because their difference is decided at BUILD time —
 * whether `@decky/ui`'s code is inside them — and a build is the only place that
 * can be decided. This module's difference is not like that. Without Decky it
 * must find and set the three globals; with Decky running it must leave them
 * exactly as they are, because Decky skips its own globals block once
 * `SP_REACT` is set and would then render through ours. That is a question about
 * the machine at the moment this runs, and **whoever loads this cannot answer it
 * in advance**: Decky may be started after Tender, or restarted mid-session, so
 * a caller that picked a "with-Decky" artifact would be picking on a fact that
 * can change between the pick and the run. So the branch is a read of live state
 * — the first thing `installGlobals` does — and the function is idempotent by
 * construction, which is also what lets the injector re-run it after Steam
 * restarts its JS context.
 *
 * **The predicates below are not ours to choose.** Decky's loader skips its
 * entire globals block when `window.SP_REACT` is already set, so if Tender
 * installs these first, Decky's own frontend renders through them. A predicate
 * that differs from Decky's therefore breaks DECKY's interface, not Tender's,
 * on a machine that has both. `decky-globals-block.txt` beside this file holds
 * Decky's block verbatim with its provenance, and `steamGlobals.test.ts` reads
 * BOTH files as text and holds the predicates against each other — which is why
 * each one is written inline here, in Decky's own spelling, rather than lifted
 * into a named constant that would read better and compare worse.
 */
import { findModule } from "@decky/ui/dist/webpack";

interface SteamAppInit {
  BFinishedInitBeforeLogin?: () => boolean;
  BFinishedInitStageOne?: () => boolean;
}

/**
 * The window as this module has to see it: the three globals absent until
 * somebody installs them.
 *
 * `@decky/ui` declares all three on `Window` as always-present
 * (`dist/utils/react/react.d.ts`), which is the very claim this module exists
 * because it is false — without Decky they are `undefined`, and that is the
 * whole reason the panel could not load. TypeScript cannot re-type an existing
 * global member through declaration merging, so the honest view is a local
 * interface and one cast, the same move `utils/deckyUiInternals.ts` makes for
 * the package's other always-present lies.
 */
interface SteamWindow {
  /** Steam's React. Written here, or by Decky's loader — whoever is first. */
  SP_REACT?: typeof import("react");
  /** Steam's ReactDOM. */
  SP_REACTDOM?: unknown;
  /** Steam's JSX runtime, or the aliased stand-in built below. */
  SP_JSX?: { jsx?: unknown; jsxs?: unknown; Fragment?: unknown };
  /** Decky's own `@decky/ui` namespace. Present only when Decky is running. */
  DFL?: unknown;
  /** Steam's own boot object, which reports when its registry is complete. */
  App?: SteamAppInit;
  /** Where a hand-driven CEF session reaches this module's one function. */
  __TENDER_INSTALL_GLOBALS?: typeof installGlobals;
}

const w = window as unknown as SteamWindow;

/** Has Steam finished the init stage after which its module registry is complete? */
const steamReady = (): boolean => w.App?.BFinishedInitBeforeLogin?.() ?? w.App?.BFinishedInitStageOne?.() ?? false;

/**
 * How long to wait for Steam's init before installing the globals anyway.
 *
 * Decky waits without a deadline. Tender cannot: Decky's boot IS Steam's boot,
 * while Tender's is a fetch from a loopback port that may arrive at any point in
 * a session — including one where `window.App` is a shape this predicate has
 * never seen, in which case an unbounded wait is an unbounded hang with nothing
 * on screen to say so. Running past the deadline is the worse of two answers and
 * is reported rather than hidden: a search that reads an incomplete cache
 * answers `undefined`, which is what the start-up check in `steamModules.ts`
 * turns into the fallback page.
 */
const STEAM_INIT_DEADLINE_MS = 30_000;

/** What `installGlobals` saw and did — the answer its caller gets back. */
export interface GlobalsReport {
  /** Were all three already set when this ran? Then nothing else happened. */
  alreadyPresent: boolean;
  /**
   * Who set them — a fact about THIS module instance, not about the session.
   *
   * A second instance of this module (a re-import, a re-injection) finds the
   * globals set, finds no `DFL`, and answers `"unknown"` even where the first
   * instance installed them itself: nothing carries the attribution across
   * instances, and `"tender"` is returned only on the path that installs.
   * Measured on the device.
   */
  source: "decky" | "tender" | "unknown";
  /** Did Steam report itself initialised before the deadline? */
  steamReady: boolean;
  /**
   * Which of the three are set now. Every key of it must be true or the panel
   * is not imported — the injector's bootstrap gates on the keys this object
   * carries rather than on a list of its own, so a fourth one added here is a
   * fourth one that has to be installed.
   */
  installed: { SP_REACT: boolean; SP_REACTDOM: boolean; SP_JSX: boolean };
  /** Steam's React version, when it could be read. */
  reactVersion: string | null;
}

/**
 * Install `SP_REACT`, `SP_REACTDOM` and `SP_JSX` if they are not there already.
 *
 * Idempotent, and deliberately so: the injector re-runs it after Steam restarts
 * its JS context, and Decky may have set them in between.
 */
export async function installGlobals(): Promise<GlobalsReport> {
  if (w.SP_REACT && w.SP_JSX && w.SP_REACTDOM) {
    return {
      alreadyPresent: true,
      // `DFL` is Decky's own namespace and nothing else writes it, so its
      // presence is the one thing that tells a Decky install from a second run
      // of this function.
      source: w.DFL ? "decky" : "unknown",
      steamReady: steamReady(),
      installed: { SP_REACT: true, SP_REACTDOM: true, SP_JSX: true },
      reactVersion: w.SP_REACT.version,
    };
  }

  const deadline = Date.now() + STEAM_INIT_DEADLINE_MS;
  while (!steamReady() && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 0));
  const readyInTime = steamReady();

  w.SP_REACT ??= findModule((m) => m.Component && m.PureComponent && m.useLayoutEffect);
  w.SP_REACTDOM ??=
    findModule((m) => m.createPortal && m.createRoot) ||
    findModule((m) => m.createPortal && m.__DOM_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE);

  if (!w.SP_JSX) {
    const jsxModule = findModule((m) => (m.jsx && m.jsxs) || (m.jsx && Object.keys(m).length == 1));
    // The guard is what keeps a MISSED search from reporting as a hit. Building
    // the stand-in unconditionally leaves `{ jsx: undefined, jsxs: undefined }`,
    // which is a perfectly truthy object — so `installed.SP_JSX` would say true
    // when nothing was found.
    //
    // **That report is the whole of what this buys**: the injector's bootstrap
    // reads it out of THIS bundle and imports the panel only where every global
    // the report names is installed. The panel itself gets no chance to notice: a
    // module-scope `SP_JSX.jsx` sits in its import graph (`dist/index.js:4892`,
    // from `PlatformDetail.tsx`), so with `SP_JSX` unset it throws while being
    // evaluated — before `definePlugin`'s factory exists, and long before any
    // check inside it could run.
    //
    // `SP_REACT` and `SP_REACTDOM` have no such hole: a miss leaves them unset.
    // Decky has none either — its block reads `jsxModule.jsxs` bare and throws
    // here.
    //
    // A module carrying `jsxs` is used as it stands; one without gets `jsx`
    // aliased into `jsxs`'s place. Decky builds the same stand-in, and it has to
    // stay the same one: whichever of the two runs first is the shape the other
    // renders through.
    if (jsxModule) {
      w.SP_JSX = jsxModule.jsxs
        ? jsxModule
        : { jsx: jsxModule.jsx, jsxs: jsxModule.jsx, Fragment: w.SP_REACT?.Fragment };
    }
  }

  return {
    alreadyPresent: false,
    source: "tender",
    steamReady: readyInTime,
    installed: {
      SP_REACT: Boolean(w.SP_REACT),
      SP_REACTDOM: Boolean(w.SP_REACTDOM),
      SP_JSX: Boolean(w.SP_JSX),
    },
    reactVersion: w.SP_REACT?.version ?? null,
  };
}

// The injector's bootstrap calls this by name after importing the bundle and
// before importing the panel, and imports the panel only where the report says
// every global it names is installed. The same name is what drives this bundle
// by hand from the CEF debugger.
w.__TENDER_INSTALL_GLOBALS = installGlobals;

export default installGlobals;
