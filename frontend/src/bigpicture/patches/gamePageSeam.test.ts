/**
 * The half of the game-page install that can be answered without Steam.
 *
 * Everything the install does with these three answers — reaching Steam's
 * registry, patching what it names, walking the live fiber tree — is
 * `installGamePagePatch.ts`'s and can only be exercised on a device. What is
 * held here is the three decisions taken before any of that.
 */

import { describe, expect, it, vi } from "vitest";

import { matchesAppDetailsFactory, patchableMemos, soleMatchingFactory, wrapRouteRenderFunc } from "./gamePageSeam";

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

  it("stops reading the registry at the second match", () => {
    // The scan reads the source text of every module Steam ships, so the answer
    // is settled where it is settled rather than after another two thousand
    // reads.
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

describe("picking the exports a patch can be installed on", () => {
  it("takes every memo whose type is a function", () => {
    // Both, rather than the route component picked out: nothing on an export
    // says which one it is, and the handler on the other one finds nothing to
    // do.
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
