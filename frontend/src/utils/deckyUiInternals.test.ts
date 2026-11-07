/**
 * The predicates Tender's Quick Access entry and Steam's controller glyph are
 * found by. The searches they run in reach Steam's module registry and are the
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

describe("ControllerGlyph", () => {
  const memo = () => ({ $$typeof: Symbol.for("react.memo"), type: () => null });

  /**
   * `ControllerGlyph` as this module computes it at evaluation, with
   * `findModule` walking *registry* the way `@decky/ui`'s does: each module's
   * `default` first, then the module itself.
   */
  async function glyphIn(registry: unknown[]): Promise<unknown> {
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
    return (await import("./deckyUiInternals")).ControllerGlyph;
  }

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

  it("goes on searching past a module whose exports throw on read", async () => {
    const throwing = Object.defineProperty({}, "WD", {
      enumerable: true,
      get: () => {
        throw new Error("module evaluation failed");
      },
    });
    const glyph = memo();
    // The Quick Access search runs over the same registry and reads every
    // export of each module it passes, so its own module comes first and that
    // search stops before it reaches the throwing one.
    const quickAccess = { b: { type: { toString: () => "function(){return QuickAccessMenuBrowserView}" } } };

    expect(await glyphIn([quickAccess, throwing, { XH: memo(), WD: glyph, Nq: () => false }])).toBe(glyph);
  });
});
