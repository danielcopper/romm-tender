/**
 * The predicates Tender's lookups of Steam's components and stores are found
 * by. The searches they run in reach Steam's module registry and are the
 * start-up check's to ask (`boot/steamModules.ts`); what a predicate answers for
 * one value, or for a registry handed to it, is decidable here.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { rendersNamed } from "./deckyUiInternals";

describe("rendersNamed", () => {
  const component = (source: string) => ({ type: { toString: () => source } });

  it("answers for a component whose render function's source names the component", () => {
    expect(rendersNamed("QuickAccessMenuBrowserView")(component("function(){return QuickAccessMenuBrowserView}"))).toBe(
      true,
    );
  });

  it("answers no for a component that names another, and for a value with no render function", () => {
    const named = rendersNamed("QuickAccessMenuBrowserView");
    expect(named(component("function(){return QuickAccessMenuEmbedded}"))).toBe(false);
    expect(named({})).toBe(false);
    expect(named(null)).toBe(false);
  });

  it("answers no rather than ending the search where reading the source throws", () => {
    const throwing = {
      type: {
        toString: () => {
          throw new Error("boom");
        },
      },
    };
    expect(rendersNamed("QuickAccessMenuBrowserView")(throwing)).toBe(false);
  });
});

/**
 * This module as it evaluates with `findModule` walking *registry* the way
 * `@decky/ui`'s does: each module's `default` first, then the module itself.
 */
async function evaluatedOver(registry: unknown[]): Promise<typeof import("./deckyUiInternals")> {
  vi.resetModules();
  const decky = await import("@decky/ui");
  vi.mocked(decky.findModule).mockImplementation((filter: (m: unknown) => boolean) => {
    for (const m of registry) {
      const fallback = (m as { default?: unknown }).default;
      if (fallback && filter(fallback)) return fallback;
      if (filter(m)) return m;
    }
    return undefined;
  });
  return import("./deckyUiInternals");
}

/** A module whose export getter throws, as one belonging to a module whose own evaluation failed does. */
const throwingModule = (name: string) =>
  Object.defineProperty({}, name, {
    enumerable: true,
    get: () => {
      throw new Error("module evaluation failed");
    },
  });

describe("QuickAccessMenuBrowserView", () => {
  afterEach(() => {
    vi.resetModules();
  });

  it("goes on searching past a module whose exports throw on read", async () => {
    const quickAccess = { b: { type: { toString: () => "function(){return QuickAccessMenuBrowserView}" } } };

    const evaluated = await evaluatedOver([throwingModule("a"), quickAccess]);

    expect(evaluated.QuickAccessMenuBrowserView).toBe(quickAccess);
  });
});

describe("ControllerGlyph", () => {
  const memo = () => ({ $$typeof: Symbol.for("react.memo"), type: () => null });

  const glyphIn = async (registry: unknown[]): Promise<unknown> => (await evaluatedOver(registry)).ControllerGlyph;

  const unrelated = () => [{ a: memo() }, { jsx: () => null, jsxs: () => null }, { default: { Component: class {} } }];

  afterEach(() => {
    vi.resetModules();
  });

  it("finds the glyph in a registry shaped like today's Steam", async () => {
    const glyph = memo();
    const registry = [...unrelated(), { XH: memo(), WD: glyph, Nq: () => false }];

    expect(await glyphIn(registry)).toBe(glyph);
  });

  it("finds nothing in a registry without the glyph's module", async () => {
    expect(await glyphIn(unrelated())).toBeUndefined();
  });

  it("finds nothing where only the old two-export shape W and X stands", async () => {
    expect(await glyphIn([...unrelated(), { W: memo(), X: memo() }])).toBeUndefined();
  });

  it("finds nothing where WD is not a React.memo", async () => {
    expect(await glyphIn([...unrelated(), { XH: memo(), WD: () => null, Nq: () => false }])).toBeUndefined();
  });

  it("passes over a module carrying WD without the legend beside it", async () => {
    const glyph = memo();
    const decoys = [
      { WD: memo(), XH: () => null },
      { WD: memo(), Q: memo() },
    ];

    expect(await glyphIn([...decoys, { XH: memo(), WD: glyph, Nq: () => false }])).toBe(glyph);
    expect(await glyphIn(decoys)).toBeUndefined();
  });

  it("goes on searching past a module whose exports throw on read", async () => {
    const glyph = memo();

    expect(await glyphIn([throwingModule("WD"), { XH: memo(), WD: glyph, Nq: () => false }])).toBe(glyph);
  });
});

/**
 * This module as it evaluates with `findModuleExport` asking *exports* in
 * order without catching a throwing filter, as a `@decky/ui` before 4.11.1
 * does — the copy a Decky Loader built against one hands the coexistence
 * bundle.
 */
async function exportsSearched(exports: unknown[]): Promise<typeof import("./deckyUiInternals")> {
  vi.resetModules();
  const decky = await import("@decky/ui");
  vi.mocked(decky.findModuleExport).mockImplementation((filter: (e: unknown) => boolean) => exports.find(filter));
  return import("./deckyUiInternals");
}

describe("FriendsUIStore", () => {
  const storeIn = async (exports: unknown[]): Promise<unknown> => (await exportsSearched(exports)).FriendsUIStore;

  /** The store as Steam's class makes it: the field on the instance, the getter on its prototype. */
  class Store {
    m_bQamFriendsExpanded = false;
    get qamFriendsExpanded(): boolean {
      return this.m_bQamFriendsExpanded;
    }
  }

  afterEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
  });

  it("finds the store by the field its message handler sets and the getter the wrapper reads", async () => {
    const store = new Store();

    expect(await storeIn([{ qamFriendsExpanded: false }, () => null, store])).toBe(store);
  });

  it("does not take an export that has the field but not the getter", async () => {
    const store = new Store();

    expect(await storeIn([{ m_bQamFriendsExpanded: false }, store])).toBe(store);
    expect(await storeIn([{ m_bQamFriendsExpanded: false }])).toBeUndefined();
  });

  it("answers no for an export that throws on being asked, goes on to the next, and says so", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const thrown = new Error("trap");
    const throwing = new Proxy(
      {},
      {
        has: () => {
          throw thrown;
        },
      },
    );
    const store = new Store();

    expect(await storeIn([throwing, store])).toBe(store);
    expect(warn.mock.calls).toEqual([
      ["[Tender] the search for Steam's FriendsUI store passed over an export that threw", thrown],
    ]);
  });
});

describe("ToastRenderer", () => {
  const rendererIn = async (exports: unknown[]): Promise<unknown> => (await exportsSearched(exports)).ToastRenderer;

  const renderer = () => ({ toString: () => 'function(){return{controller:"notification",method:0}}' });

  afterEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
  });

  it("finds the renderer by the string its body contains", async () => {
    const found = renderer();

    expect(await rendererIn([{ toString: () => "function(){}" }, found])).toBe(found);
  });

  it("answers no for an export whose source throws on being read, goes on to the next, and says so", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const thrown = new Error("no source");
    const found = renderer();
    const throwing = {
      toString: () => {
        throw thrown;
      },
    };

    expect(await rendererIn([throwing, found])).toBe(found);
    expect(warn.mock.calls).toEqual([
      ["[Tender] the search for Steam's toast renderer passed over an export that threw", thrown],
    ]);
  });
});
