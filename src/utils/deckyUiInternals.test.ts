/**
 * The predicate Steam's FriendsUI store is found by. The search it runs in
 * reaches Steam's module registry, which the suite does not have; what the
 * predicate answers for a registry handed to it is decidable here.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const logWarn = vi.hoisted(() => vi.fn());
vi.mock("../api/backend", () => ({ logWarn }));

/**
 * This module as it evaluates with `findModuleExport` asking *exports* in
 * order without catching a throwing filter — the search `passingOverThrows`
 * is written for.
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
    logWarn.mockReset();
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

  it("tells the plugin log too, with the error as text", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const throwing = new Proxy(
      {},
      {
        has: () => {
          throw new TypeError("trap");
        },
      },
    );

    await storeIn([throwing, new Store()]);

    expect(logWarn.mock.calls).toEqual([
      ["the search for Steam's FriendsUI store passed over an export that threw: TypeError: trap"],
    ]);
  });

  it("tells the plugin log once for each distinct error, and the console each time", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const throwingWith = (error: Error) =>
      new Proxy(
        {},
        {
          has: () => {
            throw error;
          },
        },
      );

    await storeIn([
      throwingWith(new TypeError("trap")),
      throwingWith(new TypeError("trap")),
      throwingWith(new RangeError("trap")),
      new Store(),
    ]);

    expect(logWarn.mock.calls).toEqual([
      ["the search for Steam's FriendsUI store passed over an export that threw: TypeError: trap"],
      ["the search for Steam's FriendsUI store passed over an export that threw: RangeError: trap"],
    ]);
    expect(warn).toHaveBeenCalledTimes(3);
  });

  it("goes on to the next export where the plugin log throws", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    logWarn.mockImplementation(() => {
      throw new Error("loader gone");
    });
    const throwing = new Proxy(
      {},
      {
        has: () => {
          throw new TypeError("trap");
        },
      },
    );
    const store = new Store();

    expect(await storeIn([throwing, store])).toBe(store);
  });
});
