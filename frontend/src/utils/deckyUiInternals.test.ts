/**
 * The predicate Tender's Quick Access entry is found by. The search it runs in
 * reaches Steam's module registry and is the start-up check's to ask
 * (`boot/steamModules.ts`); what the predicate answers for one value is
 * decidable here.
 */

import { describe, expect, it } from "vitest";

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
