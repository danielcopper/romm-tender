/**
 * The drift lock between Tender's React globals and Decky Loader's.
 *
 * Decky skips its whole globals block when `SP_REACT` is already set, so on a
 * machine carrying both, whichever runs first decides the shape BOTH render
 * through. That makes this a cross-project invariant with no diff that shows
 * both halves: `steamGlobals.ts` is ours, `decky-globals-block.txt` is
 * upstream's, and nothing but this file holds them against each other.
 *
 * What it compares is the SEARCH PREDICATES, because they are the shape: two
 * searches reading different properties answer with different modules, and a
 * different React is what breaks Decky's interface. Everything else in the two
 * files — the wait, the logging, the report — may differ freely and does.
 *
 * **Both sides are read as TEXT, including ours.** Reading our own predicates
 * back through `Function.prototype.toString` would compare whatever the test
 * transform emitted rather than what a reviewer sees, and it would need the
 * module imported — which drags in `@decky/ui`'s module-cache sweep for a
 * question that is purely about source. Reading the file keeps the two sides
 * symmetric: one extractor, applied twice.
 *
 * A failure here is not a test to fix. It means one of the two has moved, and
 * which one is the question to answer before anything is edited.
 */

import { readFileSync } from "node:fs";

import { afterEach, describe, expect, it, vi } from "vitest";

// The module-cache half of `@decky/ui` runs its sweep of Steam's webpack
// registry in its own module body, against a `window.webpackChunksteamui` that
// exists only inside Steam. The global stub in `test-setup.ts` covers
// `@decky/ui` and not this subpath, so the import has to be answered here.
vi.mock("@decky/ui/dist/webpack", () => ({ findModule: vi.fn() }));

// The Vitest root, which is this package's directory. `import.meta.url` is not a
// `file:` URL under the happy-dom environment, so `fileURLToPath` throws on it —
// the same reason `test-utils/componentSources.ts` resolves its root this way.
const BOOT_DIR = `${process.cwd()}/src/boot`;

/**
 * A source file with its comments removed, so the lock reads what the compiler
 * reads.
 *
 * Without this the extractors below search prose: a comment that spells out the
 * shape it is describing — and the one above the JSX stand-in does exactly that,
 * because the hole it warns about is worth naming — is found before the code and
 * compared as if it were the code. That is not a hypothetical; it is what this
 * helper was added for.
 *
 * The rule is narrow and stated because a general JavaScript comment stripper is
 * not: block comments, plus lines whose first non-space characters are `//`.
 * Both files satisfy it — every `//` comment in either starts its own line, and
 * the only `//` that does not (`https://` in the pinned file's provenance
 * header) is outside the markers.
 */
const withoutComments = (source: string): string =>
  source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^[ \t]*\/\/.*$/gm, "");

const PINNED = withoutComments(readFileSync(`${BOOT_DIR}/decky-globals-block.txt`, "utf8"));
const OURS = withoutComments(readFileSync(`${BOOT_DIR}/steamGlobals.ts`, "utf8"));

const BEGIN = "--- BEGIN decky-loader frontend/src/index.ts (globals block) ---";
const END = "--- END decky-loader frontend/src/index.ts (globals block) ---";

/** Decky's block, exactly as pinned — the text between the two markers. */
function pinnedBlock(): string {
  const from = PINNED.indexOf(BEGIN);
  const to = PINNED.indexOf(END);
  if (from < 0 || to < 0) throw new Error("decky-globals-block.txt has lost its markers");
  return PINNED.slice(from + BEGIN.length, to);
}

/**
 * Every module-search argument in *source*, in order.
 *
 * Parentheses are balanced rather than matched by a regex: the JSX predicate
 * contains three nested pairs of its own, so a lazy regex stops inside the first
 * of them and a greedy one swallows the next call whole.
 *
 * The marker has no leading dot, which is what lets one extractor read both
 * files: Decky calls the finder off its namespace import and we call it bare.
 */
function searchPredicates(source: string): string[] {
  const found: string[] = [];
  const marker = "findModule(";
  for (let at = source.indexOf(marker); at >= 0; at = source.indexOf(marker, at + 1)) {
    let depth = 1;
    let cursor = at + marker.length;
    const start = cursor;
    while (cursor < source.length && depth > 0) {
      if (source[cursor] === "(") depth += 1;
      else if (source[cursor] === ")") depth -= 1;
      cursor += 1;
    }
    if (depth !== 0) throw new Error(`unbalanced module search at index ${at}`);
    found.push(source.slice(start, cursor - 1));
  }
  return found;
}

/**
 * Two predicates spelled the same way, whatever whitespace each carries.
 *
 * Collapsing whitespace is the ONLY licence taken, and it is needed because each
 * file is Prettier's wrapping of a different line length. Nothing else is
 * normalised: a renamed parameter, a reordered clause, or a `&&` turned into a
 * `?.` is drift, and is meant to fail.
 */
const canonical = (text: string): string => text.replace(/\s+/g, " ").trim();

describe("the React globals Tender installs", () => {
  it("searches for the same four things as Decky Loader, in the same order", () => {
    // Asserted as whole arrays rather than entry by entry, because the ORDER is
    // part of the claim: Decky's second ReactDOM search is a fallback behind the
    // first, and a build where they swapped would answer with the other module.
    expect(searchPredicates(OURS).map(canonical)).toEqual(searchPredicates(pinnedBlock()).map(canonical));
  });

  it("finds four searches in each file, so an extraction that stopped working cannot pass", () => {
    // Without this, a marker block or a call spelling that stopped matching
    // would yield two empty lists, the comparison above would pass, and the lock
    // would retire itself in silence.
    expect(searchPredicates(pinnedBlock())).toHaveLength(4);
    expect(searchPredicates(OURS)).toHaveLength(4);
  });

  it("assigns each search's answer to the same global Decky assigns it to", () => {
    // The predicates being identical is not enough on its own: a bundle that
    // searched the same way and put ReactDOM's answer on `SP_REACT` would pass
    // the comparison above and hand Decky the wrong module.
    // The trailing `[^=]` is what keeps a comparison out: `??=` and `=` are
    // assignments, `==` and `===` are not, and the two spellings differ only in
    // what follows.
    const assignments = (source: string) =>
      [...source.matchAll(/(SP_(?:REACTDOM|REACT|JSX))\s*\?{0,2}=[^=]/g)].map((match) => match[1]);
    expect(new Set(assignments(pinnedBlock()))).toEqual(new Set(["SP_REACT", "SP_REACTDOM", "SP_JSX"]));
    expect(new Set(assignments(OURS))).toEqual(new Set(assignments(pinnedBlock())));
  });

  it("builds the JSX stand-in out of the same keys, aliased the same way", () => {
    // When ours runs first, this object IS what Decky's own components are
    // compiled against, so its shape is as load-bearing as the predicates.
    // Two things are stripped before comparing, and neither is part of the
    // shape: optional chaining, which says what happens when the search MISSED
    // and is the start-up check's question rather than this object's; and
    // whatever each file calls the window, since ours reaches it through a
    // locally-typed view and Decky's through the global.
    const standIn = (source: string): Record<string, string> => {
      const match = /\{\s*jsx:[\s\S]*?\}/.exec(source);
      if (!match) throw new Error("no JSX stand-in found in the source");
      const body = canonical(match[0])
        .slice(1, -1)
        .replace(/\?\./g, ".")
        .replace(/\b(?:window|w)\.(?=SP_)/g, "");
      return Object.fromEntries(
        body
          .split(",")
          .map((entry) => entry.trim())
          .filter(Boolean)
          .map((entry) => [entry.slice(0, entry.indexOf(":")).trim(), entry.slice(entry.indexOf(":") + 1).trim()]),
      );
    };
    const theirs = standIn(pinnedBlock());
    expect(Object.keys(theirs).sort()).toEqual(["Fragment", "jsx", "jsxs"]);
    // The aliasing itself, stated rather than assumed: `jsxs` takes `jsx`'s
    // value, which is the whole reason the stand-in exists.
    expect(theirs.jsxs).toBe(theirs.jsx);
    expect(standIn(OURS)).toEqual(theirs);
  });
});

/**
 * What `installGlobals` actually DOES, as against what its source says.
 *
 * The lock above compares text, which is the only way to compare against a file
 * that is not ours. These run the function — and run OUR predicates for real,
 * because the finder is mocked as what the real one is: the first module in a
 * registry that the predicate accepts. A registry of plausible Steam modules is
 * then a test of the searches themselves, not of a stub agreeing with itself.
 */
describe("installing the globals", () => {
  /** Everything this module reads off or writes to the window. */
  const TOUCHED = ["SP_REACT", "SP_REACTDOM", "SP_JSX", "DFL", "App", "__TENDER_INSTALL_GLOBALS"] as const;
  const win = () => window as unknown as Record<string, unknown>;
  const read = (name: string) => win()[name];

  const STEAM_IS_UP = { App: { BFinishedInitBeforeLogin: () => true } };
  const REACT = { Component: 1, PureComponent: 1, useLayoutEffect: 1, Fragment: "the-fragment", version: "19.1.1" };

  /**
   * Load a fresh copy of the module with `window` carrying exactly `state` and
   * Steam's registry holding exactly `registry`.
   */
  async function install(state: Record<string, unknown>, registry: Record<string, unknown>[]) {
    for (const key of TOUCHED) delete win()[key];
    Object.assign(window, state);
    vi.resetModules();
    const webpack = await import("@decky/ui/dist/webpack");
    vi.mocked(webpack.findModule).mockImplementation(((filter: (m: unknown) => unknown) =>
      registry.find((module) => Boolean(filter(module)))) as never);
    const module = await import("./steamGlobals");
    return module.installGlobals();
  }

  afterEach(() => {
    for (const key of TOUCHED) delete win()[key];
    vi.useRealTimers();
  });

  it("touches nothing when all three are already set, and says Decky set them", async () => {
    const theirs = { version: "19.1.1" };
    const report = await install({ ...STEAM_IS_UP, SP_REACT: theirs, SP_REACTDOM: {}, SP_JSX: {}, DFL: {} }, []);
    expect(report.alreadyPresent).toBe(true);
    expect(report.source).toBe("decky");
    expect(report.reactVersion).toBe("19.1.1");
    // The whole point of the short-circuit: Decky's React stays Decky's React.
    expect(win().SP_REACT).toBe(theirs);
  });

  it("does not name Decky when Decky's own namespace is absent", async () => {
    const report = await install({ ...STEAM_IS_UP, SP_REACT: {}, SP_REACTDOM: {}, SP_JSX: {} }, []);
    expect(report.source).toBe("unknown");
  });

  it("finds all three in Steam's registry when none of them is set", async () => {
    const reactDom = { createPortal: 1, createRoot: 1 };
    const jsx = { jsx: "the-jsx", jsxs: "the-jsxs" };
    const report = await install({ ...STEAM_IS_UP }, [{ unrelated: 1 }, REACT, reactDom, jsx]);

    expect(win().SP_REACT).toBe(REACT);
    expect(win().SP_REACTDOM).toBe(reactDom);
    // A module that already carries `jsxs` is used as it stands — no stand-in.
    expect(win().SP_JSX).toBe(jsx);
    expect(report).toMatchObject({
      alreadyPresent: false,
      source: "tender",
      steamReady: true,
      installed: { SP_REACT: true, SP_REACTDOM: true, SP_JSX: true },
      reactVersion: "19.1.1",
    });
  });

  it("falls back to the second ReactDOM search when the first finds nothing", async () => {
    // React 19 moved `createRoot` off the module the first predicate matches, so
    // this is the branch that answers on a current Steam rather than a spare one.
    const reactDom = { createPortal: 1, __DOM_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE: 1 };
    await install({ ...STEAM_IS_UP }, [REACT, reactDom, { jsx: "j", jsxs: "js" }]);
    expect(win().SP_REACTDOM).toBe(reactDom);
  });

  it("aliases jsx into jsxs when Steam's JSX module carries only the one", async () => {
    // The stand-in Decky builds, built the same way: whichever of the two runs
    // first is the shape the other renders through.
    await install({ ...STEAM_IS_UP }, [REACT, { createPortal: 1, createRoot: 1 }, { jsx: "the-only-one" }]);
    expect(win().SP_JSX).toEqual({ jsx: "the-only-one", jsxs: "the-only-one", Fragment: "the-fragment" });
  });

  it("leaves SP_JSX unset when the search finds nothing, rather than a hollow stand-in", async () => {
    // A stand-in built from a module that was never found is
    // `{ jsx: undefined, jsxs: undefined }` — a perfectly truthy object — so the
    // report would say SP_JSX was installed when nothing was found. That report
    // is there for the injector to read out of `dist/globals.js`, which this
    // module is built into, before it decides whether to load the panel (#1900)
    // — nothing reads it yet — and the panel gets no chance to correct it: a
    // module-scope `SP_JSX.jsx` in its import graph throws while it is being
    // evaluated, before any check inside it can run. The other two globals have
    // no such hole, and neither does Decky, whose block reads `jsxModule.jsxs`
    // bare and throws here.
    const report = await install({ ...STEAM_IS_UP }, [REACT, { createPortal: 1, createRoot: 1 }]);

    expect(read("SP_JSX")).toBeUndefined();
    expect(report.installed).toEqual({ SP_REACT: true, SP_REACTDOM: true, SP_JSX: false });
  });

  it("installs anyway once the wait for Steam runs out, and says the wait ran out", async () => {
    // A hang with nothing on screen is the worse of the two answers, so the
    // deadline exists — and what it produces is reported rather than hidden: a
    // search over an incomplete registry answers undefined, which is what the
    // start-up check turns into the fallback page.
    vi.useFakeTimers();
    const pending = install({ App: { BFinishedInitStageOne: () => false } }, [REACT]);
    await vi.advanceTimersByTimeAsync(31_000);
    const report = await pending;
    expect(report.steamReady).toBe(false);
    expect(report.installed.SP_REACTDOM).toBe(false);
  });
});
