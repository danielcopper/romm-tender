/**
 * The half of the game-page install that can be answered without Steam.
 *
 * Everything the install does with these three answers — reaching Steam's
 * registry, patching what it names, walking the live fiber tree — is
 * `installGamePagePatch.ts`'s and can only be exercised on a device. What is
 * held here is the three decisions taken before any of that.
 */

import { describe, expect, it, vi } from "vitest";

import {
  hasRouteModuleShape,
  matchesAppDetailsFactory,
  patchableMemos,
  soleMatchingFactory,
  wrapRouteRenderFunc,
} from "./gamePageSeam";

const APP_DETAILS_SOURCE = 'e.renderFunc&&e.renderFunc(t),n(r.AppDetailsOverviewPanel),i(o.InnerContainer,"x")';

const memo = (type: unknown): object => ({ $$typeof: Symbol.for("react.memo"), type });

describe("recognising the factory Steam draws the game page from", () => {
  it("takes a factory carrying all three property names", () => {
    expect(matchesAppDetailsFactory(APP_DETAILS_SOURCE)).toBe(true);
  });

  it.each(["renderFunc", "AppDetailsOverviewPanel", "InnerContainer"])(
    "refuses a factory that carries everything but %s",
    (missing) => {
      // Each name on its own, because a predicate that had lost one would still
      // match the module today and would start matching others the day Steam
      // splits it.
      expect(matchesAppDetailsFactory(APP_DETAILS_SOURCE.split(missing).join("Gone"))).toBe(false);
    },
  );
});

describe("picking the one factory out of the registry", () => {
  const sources = (...entries: readonly (readonly [string, string])[]) => entries;

  it("answers with the id of the only factory that matches", () => {
    const id = soleMatchingFactory(
      sources(["40477", "nothing to see"], ["40478", APP_DETAILS_SOURCE], ["40479", "renderFunc alone"]),
      matchesAppDetailsFactory,
    );
    expect(id).toBe("40478");
  });

  it("answers with nothing when no factory matches", () => {
    const id = soleMatchingFactory(sources(["1", "renderFunc"], ["2", "InnerContainer"]), matchesAppDetailsFactory);
    expect(id).toBeUndefined();
  });

  it("answers with nothing when two factories match, rather than taking the first", () => {
    // A second match means the predicate no longer names one module. Taking the
    // first would install the section into whichever module Steam happened to
    // emit earlier — a fault nobody could see, where an empty answer is one the
    // start-up check reports.
    const id = soleMatchingFactory(
      sources(["1", APP_DETAILS_SOURCE], ["2", APP_DETAILS_SOURCE]),
      matchesAppDetailsFactory,
    );
    expect(id).toBeUndefined();
  });

  it("names the same factory over a narrowed set as over the whole one", () => {
    // The two-stage search hands it the shaped modules first and every module
    // second, and it is told nothing about which it is reading. Each side names
    // the id rather than being compared to the other, because two refusals
    // compare equal and would pin nothing.
    const whole = sources(["1", "nothing to see"], ["2", APP_DETAILS_SOURCE], ["3", "renderFunc alone"]);
    expect(soleMatchingFactory(whole, matchesAppDetailsFactory)).toBe("2");
    // The narrowed side is a generator, which is what both production call
    // sites pass — the sources are read one at a time rather than collected.
    function* narrowed(): Generator<readonly [string, string]> {
      yield ["2", APP_DETAILS_SOURCE];
    }
    expect(soleMatchingFactory(narrowed(), matchesAppDetailsFactory)).toBe("2");
  });

  it("stops reading the registry at the second match", () => {
    // Reading a factory's source is the expensive half of the search, so the
    // answer is settled where it is settled rather than after every remaining
    // module has been read.
    const read = vi.fn();
    function* watched(): Generator<readonly [string, string]> {
      for (const id of ["1", "2", "3"]) {
        read(id);
        yield [id, APP_DETAILS_SOURCE];
      }
    }
    expect(soleMatchingFactory(watched(), matchesAppDetailsFactory)).toBeUndefined();
    expect(read.mock.calls.flat()).toEqual(["1", "2"]);
  });
});

describe("recognising the shape the route module has", () => {
  const throwingExport = (exports: object, name: string): object => {
    Object.defineProperty(exports, name, {
      enumerable: true,
      get() {
        throw new Error("module evaluation failed");
      },
    });
    return exports;
  };

  it("takes a module whose every export is a patchable memo, of which there are two", () => {
    expect(hasRouteModuleShape({ xA: memo(() => null), kg: memo(() => null) })).toBe(true);
  });

  it("takes one with more than two, since the shape is a floor and not a count", () => {
    // The code depends on no number: what it rests on is that the exports are
    // all memos. A predicate written to the count would stop matching the day
    // Steam adds a third component to the same module.
    expect(hasRouteModuleShape({ a: memo(() => null), b: memo(() => null), c: memo(() => null) })).toBe(true);
  });

  it("refuses one carrying anything that is not a patchable memo", () => {
    // The point of the shape is that it is cheap and specific. A module with a
    // plain function beside a memo is most of Steam's registry.
    expect(hasRouteModuleShape({ route: memo(() => null), helper: () => null })).toBe(false);
    expect(hasRouteModuleShape({ route: memo(() => null), text: "InnerContainer" })).toBe(false);
    expect(hasRouteModuleShape({ route: memo(() => null), bare: memo("not a function") })).toBe(false);
  });

  it("refuses a single export, however memo-shaped", () => {
    expect(hasRouteModuleShape({ only: memo(() => null) })).toBe(false);
  });

  it("refuses one whose getter throws, rather than letting the throw out", () => {
    // Every module in Steam's registry is asked this, so one that cannot be
    // read has to answer rather than end the walk.
    const exports = throwingExport({ route: memo(() => null) }, "broken");
    expect(() => hasRouteModuleShape(exports)).not.toThrow();
    expect(hasRouteModuleShape(exports)).toBe(false);
  });

  it("refuses it even beside enough readable memos, since unreadable is not a memo", () => {
    // The distinction a shorter test misses: refusing the module is not the
    // same as passing the unreadable export over. Skipping it would call a
    // module that carries something nobody could read the shape this narrows
    // to — and the full scan behind the shape pass reaches that module anyway.
    const exports = throwingExport({ route: memo(() => null), page: memo(() => null) }, "broken");
    expect(hasRouteModuleShape(exports)).toBe(false);
  });

  it("refuses exports that are not an object, and an empty one", () => {
    expect(hasRouteModuleShape(undefined)).toBe(false);
    expect(hasRouteModuleShape(null)).toBe(false);
    expect(hasRouteModuleShape(() => null)).toBe(false);
    expect(hasRouteModuleShape({})).toBe(false);
  });
});

describe("picking the exports a patch can be installed on", () => {
  it("takes every memo whose type is a function", () => {
    // Every one of them, rather than the route component picked out: nothing on
    // an export says which one it is, and on any other export the handler finds
    // nothing to do.
    const route = memo(() => null);
    const page = memo(() => null);
    expect(patchableMemos({ xA: route, kg: page })).toEqual([route, page]);
  });

  it("leaves out an export that is not a memo, and a memo with no function type", () => {
    const route = memo(() => null);
    expect(
      patchableMemos({
        route,
        plain: () => null,
        text: "AppDetailsOverviewPanel",
        lazy: { $$typeof: Symbol.for("react.lazy"), type: () => null },
        forwarded: { $$typeof: Symbol.for("react.memo"), render: () => null },
      }),
    ).toEqual([route]);
  });

  it("passes over an export whose getter throws instead of ending the search", () => {
    // A webpack namespace defines its exports as getters, and one belonging to a
    // module whose own evaluation failed throws on read. Reading them all at
    // once would lose the exports after it.
    const route = memo(() => null);
    const exports = { broken: undefined as unknown, route };
    Object.defineProperty(exports, "broken", {
      enumerable: true,
      get() {
        throw new Error("module evaluation failed");
      },
    });
    expect(patchableMemos(exports)).toEqual([route]);
  });

  it("answers with nothing for exports that are not an object", () => {
    expect(patchableMemos(undefined)).toEqual([]);
    expect(patchableMemos(null)).toEqual([]);
  });
});

describe("what one render of the route component is owed", () => {
  it("wraps the renderFunc of props it has not seen", () => {
    const wrap = vi.fn();
    const props = { renderFunc: () => null };

    expect(wrapRouteRenderFunc([props], new WeakSet(), wrap)).toBe(true);
    expect(wrap).toHaveBeenCalledWith(props);
  });

  it("wraps the same props object once, however often it is rendered", () => {
    // Without Decky the router hands the route component the same props object
    // until it re-renders, so every render after the first would stack another
    // wrapper on the same function.
    const wrap = vi.fn();
    const wrapped = new WeakSet<object>();
    const props = { renderFunc: () => null };

    expect(wrapRouteRenderFunc([props], wrapped, wrap)).toBe(true);
    expect(wrapRouteRenderFunc([props], wrapped, wrap)).toBe(false);
    expect(wrap).toHaveBeenCalledTimes(1);
  });

  it("wraps a second props object carrying the same renderFunc", () => {
    // Beside a Decky the route's child is cloned per render, so the props object
    // is fresh every time while the function inside it is not. A guard kept per
    // component — or per renderFunc — would wrap the first render and no other,
    // and the section would appear once and never again.
    const wrap = vi.fn();
    const wrapped = new WeakSet<object>();
    const renderFunc = () => null;

    expect(wrapRouteRenderFunc([{ renderFunc }], wrapped, wrap)).toBe(true);
    expect(wrapRouteRenderFunc([{ renderFunc }], wrapped, wrap)).toBe(true);
    expect(wrap).toHaveBeenCalledTimes(2);
  });

  it("leaves props carrying no renderFunc alone", () => {
    // The other memo export of the same module is rendered with props of its
    // own, and so is the route component on the desktop client's library
    // router, which passes no renderFunc at all.
    const wrap = vi.fn();
    expect(wrapRouteRenderFunc([{ children: null }], new WeakSet(), wrap)).toBe(false);
    expect(wrapRouteRenderFunc([{ renderFunc: "not a function" }], new WeakSet(), wrap)).toBe(false);
    expect(wrap).not.toHaveBeenCalled();
  });

  it("leaves a render with no props object at all alone", () => {
    const wrap = vi.fn();
    expect(wrapRouteRenderFunc([], new WeakSet(), wrap)).toBe(false);
    expect(wrapRouteRenderFunc([null], new WeakSet(), wrap)).toBe(false);
    expect(wrap).not.toHaveBeenCalled();
  });
});
