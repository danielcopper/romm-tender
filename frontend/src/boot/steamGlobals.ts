/**
 * Steam's React, ReactDOM and JSX runtime, installed by Tender instead of by
 * Decky Loader.
 *
 * Steam does not define `SP_REACT`, `SP_REACTDOM` or `SP_JSX` — Decky's loader
 * does, and until #1896 that was the only reason they existed on a device. The
 * panel bundle maps `react`, `react-dom` and `react/jsx-runtime` onto those
 * three names, so without them it cannot load at all. This module is the
 * smallest thing that makes it load.
 *
 * **This is its own bundle (`dist/globals.js`), and that is not tidiness.**
 * `@decky/ui`'s component half reads React internals while its own modules are
 * evaluated, so it cannot sit in the import graph of the module that creates
 * the globals — ESM evaluates the whole graph before any body runs, and an
 * import cannot be ordered after set-up code in the same file. Only
 * `@decky/ui/dist/webpack` is imported here: the module-cache machinery without
 * the components. Decky splits it in the same place for the same reason.
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

declare global {
  interface Window {
    /** Steam's React. Written here, or by Decky's loader — whoever is first. */
    SP_REACT?: typeof import("react");
    /** Steam's ReactDOM. */
    SP_REACTDOM?: unknown;
    /** Steam's JSX runtime, or the aliased stand-in built below. */
    SP_JSX?: { jsx?: unknown; jsxs?: unknown; Fragment?: unknown };
    /** Decky's own `@decky/ui` namespace. Present only when Decky is running. */
    DFL?: unknown;
  }
}

interface SteamAppInit {
  BFinishedInitBeforeLogin?: () => boolean;
  BFinishedInitStageOne?: () => boolean;
}

/** Has Steam finished the init stage after which its module registry is complete? */
const steamReady = (): boolean => {
  const app = (window as { App?: SteamAppInit }).App;
  return app?.BFinishedInitBeforeLogin?.() ?? app?.BFinishedInitStageOne?.() ?? false;
};

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

/** What `installGlobals` saw and did, for the start-up log line. */
export interface GlobalsReport {
  /** Were all three already set when this ran? Then nothing else happened. */
  alreadyPresent: boolean;
  /** Who set them, when they were already there. */
  source: "decky" | "tender" | "unknown";
  /** Did Steam report itself initialised before the deadline? */
  steamReady: boolean;
  /** Which of the three are set now. */
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
  if (window.SP_REACT && window.SP_JSX && window.SP_REACTDOM) {
    return {
      alreadyPresent: true,
      // `DFL` is Decky's own namespace and nothing else writes it, so its
      // presence is the one thing that tells a Decky install from a second run
      // of this function.
      source: window.DFL ? "decky" : "unknown",
      steamReady: steamReady(),
      installed: { SP_REACT: true, SP_REACTDOM: true, SP_JSX: true },
      reactVersion: window.SP_REACT.version,
    };
  }

  const deadline = Date.now() + STEAM_INIT_DEADLINE_MS;
  while (!steamReady() && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 0));
  const readyInTime = steamReady();

  window.SP_REACT ??= findModule((m) => m.Component && m.PureComponent && m.useLayoutEffect);
  window.SP_REACTDOM ??=
    findModule((m) => m.createPortal && m.createRoot) ||
    findModule((m) => m.createPortal && m.__DOM_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE);

  if (!window.SP_JSX) {
    const jsxModule = findModule((m) => (m.jsx && m.jsxs) || (m.jsx && Object.keys(m).length == 1));
    // A module carrying `jsxs` is used as it stands; one without gets `jsx`
    // aliased into `jsxs`'s place. Decky builds the same stand-in, and it has to
    // stay the same one: whichever of the two runs first is the shape the other
    // renders through.
    window.SP_JSX = jsxModule?.jsxs
      ? jsxModule
      : { jsx: jsxModule?.jsx, jsxs: jsxModule?.jsx, Fragment: window.SP_REACT?.Fragment };
  }

  return {
    alreadyPresent: false,
    source: "tender",
    steamReady: readyInTime,
    installed: {
      SP_REACT: Boolean(window.SP_REACT),
      SP_REACTDOM: Boolean(window.SP_REACTDOM),
      SP_JSX: Boolean(window.SP_JSX),
    },
    reactVersion: window.SP_REACT?.version ?? null,
  };
}

// The injector (#1900) evaluates this bundle and then calls the function. It is
// reachable by name as well, so the same bundle can be driven by hand from the
// CEF debugger — which is how the spike measured it and how a device test
// reproduces one.
(window as { __TENDER_INSTALL_GLOBALS?: typeof installGlobals }).__TENDER_INSTALL_GLOBALS = installGlobals;

export default installGlobals;
