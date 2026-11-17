/**
 * The half of the wide pages' wrapper that is decidable without Steam: which
 * fiber is the QAM host, what the patch handler makes of one render of it, the
 * once-per-memo install and its adoption of the mounted fiber, taking it back,
 * and what the console and the plugin log are told.
 *
 * What happy-dom cannot show is Steam moving the view once the class is there,
 * nor `@decky/ui`'s patcher reaching a live host: `patchAfter` below is a
 * stand-in that does to the memo what `afterPatch` does.
 *
 * Each test loads a fresh copy of the module, because which lines have been
 * written, and whether the wrapper was taken back, is module state, kept for
 * the life of the JS context.
 */

import { createElement, type FC, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import type { QamFiber, QamWidthInstall, SimpleMemo } from "./qamWidthPatch";

type QamWidthPatch = typeof import("./qamWidthPatch");

const logWarn = vi.hoisted(() => vi.fn());
vi.mock("../api/backend", () => ({ logWarn }));

const EXPANDED = "Expanded_hash";
const NARROW = "the wide pages stay at Steam's narrow width";
const PLACEHOLDER = "ViewPlaceholder_hash";

const BrowserViewHost: FC<{ className?: string | undefined; browser?: unknown }> = () => null;
const Backdrop: FC<{ className?: string }> = () => null;

const view = (className: string | undefined, browser: unknown = { SetBounds: () => {} }) =>
  createElement(BrowserViewHost, { key: "view", className, browser });

/** Steam's QAM host as one render returns it: a container whose children are an array, one of them handed the view. */
const hostRender = (viewClass: string = PLACEHOLDER): ReactElement =>
  createElement("div", { className: "Container" }, createElement(Backdrop, { key: "backdrop" }), view(viewClass));

/** The className the view's element was handed in *rendered*. */
function viewClassOf(rendered: unknown): string | undefined {
  const children = ((rendered as ReactElement).props as { children: ReactElement[] }).children;
  const found = children.find((child) => child.type === BrowserViewHost);
  return (found?.props as { className?: string } | undefined)?.className;
}

/** A host mounted the way React mounts a simple `memo`: its render function on the fiber and its alternate. */
function mountedHost(shape: "simple" | "memo" = "simple"): { memo: SimpleMemo; host: QamFiber } {
  const render = vi.fn(() => hostRender());
  const memo = { $$typeof: Symbol.for("react.memo"), type: render };
  const onFiber = shape === "simple" ? render : memo;
  const host: QamFiber = {
    type: onFiber,
    elementType: memo,
    memoizedProps: { active: true, ModalManager: {} },
    alternate: { type: onFiber },
  };
  return { memo, host };
}

/** A getter or proxy trap that throws, as one on a value from a module whose evaluation failed does. */
const unreadable = (): never => {
  throw new Error("unreadable");
};

/**
 * What `afterPatch` does to the memo: its `type` replaced by one that hands the
 * original's result to *handler*, answering the handle that puts the original
 * back.
 */
const patchAfter = (handler: (args: unknown[], rendered: unknown) => unknown, unpatch = vi.fn()) =>
  vi.fn((memo: SimpleMemo) => {
    const original = memo.type;
    memo.type = (...args: unknown[]) => handler(args, original(...args));
    return () => {
      unpatch();
      memo.type = original;
    };
  });

let mod: QamWidthPatch;
let warn: MockInstance<typeof console.warn>;

beforeEach(async () => {
  vi.resetModules();
  mod = await import("./qamWidthPatch");
  warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  logWarn.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the wrapper's handler on Steam's QAM host", () => {
  it("adds the Expanded class to the element carrying the view while the flag is set", () => {
    const steam = hostRender();

    const rendered = mod.widenWhileExpanded(() => true, EXPANDED)([], steam) as ReactElement;

    expect(viewClassOf(rendered)).toBe(`${PLACEHOLDER} ${EXPANDED}`);
    const children = (rendered.props as { children: ReactElement[] }).children;
    expect(children[0]?.type).toBe(Backdrop);
    expect(children.map((child) => child.key)).toEqual(["backdrop", "view"]);
    expect((rendered.props as { className?: string }).className).toBe("Container");
  });

  it("widens the element carrying the view, not an earlier sibling with a class of its own, and keeps its view", () => {
    const browser = { SetBounds: () => {} };
    const steam = createElement(
      "div",
      null,
      createElement(Backdrop, { key: "backdrop", className: "Backdrop_hash" }),
      view(PLACEHOLDER, browser),
    );

    const rendered = mod.widenWhileExpanded(() => true, EXPANDED)([], steam) as ReactElement;

    const [backdrop, widenedView] = (rendered.props as { children: ReactElement[] }).children;
    expect((backdrop?.props as { className?: string }).className).toBe("Backdrop_hash");
    expect(viewClassOf(rendered)).toBe(`${PLACEHOLDER} ${EXPANDED}`);
    expect((widenedView?.props as { browser?: unknown }).browser).toBe(browser);
  });

  it("returns Steam's result untouched while the flag is not set", () => {
    const steam = hostRender();

    expect(mod.widenWhileExpanded(() => false, EXPANDED)([], steam)).toBe(steam);
  });

  it("reads the flag at every render, so a render after the flag was cleared returns Steam's result untouched", () => {
    let expanded = true;
    const handler = mod.widenWhileExpanded(() => expanded, EXPANDED);

    expect(viewClassOf(handler([], hostRender()))).toBe(`${PLACEHOLDER} ${EXPANDED}`);

    expanded = false;
    const steam = hostRender();

    expect(handler([], steam)).toBe(steam);
  });

  it("does not add the class a second time when Steam has already set it", () => {
    // On the Friends tab Steam puts the class there itself.
    const steam = hostRender(`${PLACEHOLDER} ${EXPANDED}`);

    expect(mod.widenWhileExpanded(() => true, EXPANDED)([], steam)).toBe(steam);
  });

  it("matches the class by whole name, not as part of a longer one", () => {
    const rendered = mod.widenWhileExpanded(() => true, EXPANDED)([], hostRender(`${PLACEHOLDER} ${EXPANDED}Wide`));

    expect(viewClassOf(rendered)).toBe(`${PLACEHOLDER} ${EXPANDED}Wide ${EXPANDED}`);
  });

  it("returns Steam's result when its own work throws, and says so once, with the error itself", () => {
    const thrown = new Error("store moved");
    const handler = mod.widenWhileExpanded(() => {
      throw thrown;
    }, EXPANDED);
    const steam = hostRender();

    expect(handler([], steam)).toBe(steam);
    handler([], hostRender());

    expect(warn.mock.calls).toEqual([
      ["[Tender] the wide pages stay at Steam's narrow width: the wrapper on the QAM host threw", thrown],
    ]);
  });

  it("still says so where a later throw is a different error, and not where it is the same one again", () => {
    let next: unknown;
    const handler = mod.widenWhileExpanded(() => {
      throw next;
    }, EXPANDED);
    const first = new Error("store moved");
    const renamed = new TypeError("store moved");
    const reworded = new Error("store gone");

    for (const thrown of [first, new Error("store moved"), renamed, reworded, new TypeError("store moved")]) {
      next = thrown;
      handler([], hostRender());
    }

    const line = "[Tender] the wide pages stay at Steam's narrow width: the wrapper on the QAM host threw";
    expect(warn.mock.calls).toEqual([
      [line, first],
      [line, renamed],
      [line, reworded],
    ]);
  });

  it.each([
    [
      "an error whose message getter throws",
      () => Object.defineProperty(new Error("x"), "message", { get: unreadable }),
    ],
    ["a proxy whose every read throws", () => new Proxy({}, { get: unreadable })],
    ["null", () => null],
    ["a string", () => "store moved"],
    ["a symbol", () => Symbol("store moved")],
  ])("returns Steam's result and says so where the thrown value is %s", (_value, make) => {
    const thrown = make();
    const handler = mod.widenWhileExpanded(() => {
      throw thrown;
    }, EXPANDED);
    const steam = hostRender();

    expect(handler([], steam)).toBe(steam);
    // Compared by identity: a deep comparison would read the proxy and throw.
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]?.[0]).toBe(
      "[Tender] the wide pages stay at Steam's narrow width: the wrapper on the QAM host threw",
    );
    expect(warn.mock.calls[0]?.[1]).toBe(thrown);
  });

  it.each([
    ["is not an element", null, "not-an-element"],
    ["has children that are not an array", createElement("div", null, view(PLACEHOLDER)), "children-not-an-array"],
    [
      "hands the view to no child",
      createElement("div", null, createElement(Backdrop, { key: "a" }), createElement(Backdrop, { key: "b" })),
      "no-child-handed-the-view",
    ],
    [
      "hands it to a child with no class to extend",
      createElement("div", null, createElement(Backdrop), view(undefined)),
      "view-without-a-class",
    ],
  ])("returns Steam's result, and says once which part was not as expected, where it %s", (_shape, steam, part) => {
    const handler = mod.widenWhileExpanded(() => true, EXPANDED);

    expect(handler([], steam)).toBe(steam);
    handler([], steam);

    expect(warn.mock.calls).toEqual([
      [`[Tender] ${NARROW}: the QAM host rendered no element to widen while the QAM's Friends flag was set (${part})`],
    ]);
  });

  it("still says so where a later render misses in a different part", () => {
    const handler = mod.widenWhileExpanded(() => true, EXPANDED);

    handler([], null);
    handler([], createElement("div", null, view(PLACEHOLDER)));
    handler([], null);

    const missed = `[Tender] ${NARROW}: the QAM host rendered no element to widen while the QAM's Friends flag was set`;
    expect(warn.mock.calls.map(([line]) => line)).toEqual([
      `${missed} (not-an-element)`,
      `${missed} (children-not-an-array)`,
    ]);
  });

  it("says nothing while the flag is not set, whatever Steam rendered", () => {
    mod.widenWhileExpanded(() => false, EXPANDED)([], null);

    expect(warn).not.toHaveBeenCalled();
  });
});

const TAKE_OFF_FAILED =
  "[Tender] the wrapper on the QAM host could not be taken off; a host still rendering through it gets Steam's render " +
  "back untouched until Steam's JS context is rebuilt";
const POINT_BACK_FAILED =
  "[Tender] the QAM hosts could not all be pointed back at Steam's render; one still on the wrapper gets Steam's render " +
  "back untouched until it is mounted again";

describe("isQamHost", () => {
  it("is the fiber handed both `active` and `ModalManager`", () => {
    expect(mod.isQamHost({ memoizedProps: { active: false, ModalManager: {} } })).toBe(true);
  });

  it("is not a fiber between the host and the placeholder, which is handed the view", () => {
    expect(mod.isQamHost({ memoizedProps: { browser: {}, className: PLACEHOLDER } })).toBe(false);
    expect(mod.isQamHost({ memoizedProps: { active: true } })).toBe(false);
    expect(mod.isQamHost({ memoizedProps: null })).toBe(false);
  });
});

describe("wrapFirstQamHost", () => {
  const handler = (_args: unknown[], rendered: unknown) => rendered;

  it("wraps the component once when installed twice", () => {
    const { memo, host } = mountedHost();
    const patch = patchAfter(handler);

    expect(mod.wrapFirstQamHost([host], patch)).toBe("wrapped");
    const wrapped = memo.type;
    expect(mod.wrapFirstQamHost([host], patch)).toBe("wrapped");

    expect(patch).toHaveBeenCalledTimes(1);
    expect(memo.type).toBe(wrapped);
  });

  it("puts what the patch put on the memo on the mounted fiber and its alternate", () => {
    const { memo, host } = mountedHost();
    const original = memo.type;

    mod.wrapFirstQamHost([host], patchAfter(handler));

    expect(memo.type).not.toBe(original);
    expect(host.type).toBe(memo.type);
    expect(host.alternate?.type).toBe(memo.type);
  });

  it("wraps a host mounted without an alternate", () => {
    const { memo, host } = mountedHost();
    const original = memo.type;
    const lone: QamFiber = { ...host, alternate: null };

    expect(mod.wrapFirstQamHost([lone], patchAfter(handler))).toBe("wrapped");
    expect(memo.type).not.toBe(original);
    expect(lone.type).toBe(memo.type);
  });

  it("wraps nothing where the host is not mounted as a simple memo", () => {
    // A `memo` with a comparator or default props is mounted with the memo
    // itself as the fiber's type; putting a function there would break it.
    const { memo, host } = mountedHost("memo");
    const original = memo.type;
    const patch = patchAfter(handler);

    expect(mod.wrapFirstQamHost([host], patch)).toBe("host-not-simple-memo");
    expect(patch).not.toHaveBeenCalled();
    expect(memo.type).toBe(original);
    expect(host.type).toBe(memo);
  });

  it("wraps nothing where the fiber holds another function than the memo's own", () => {
    const { memo, host } = mountedHost();
    const original = memo.type;
    const patch = patchAfter(handler);

    expect(mod.wrapFirstQamHost([{ ...host, type: () => null }], patch)).toBe("host-not-simple-memo");
    expect(patch).not.toHaveBeenCalled();
    expect(memo.type).toBe(original);
  });

  it("goes on to the next placeholder's host where one is not wrappable", () => {
    const refused = mountedHost("memo");
    const { memo, host } = mountedHost();
    const original = memo.type;

    expect(mod.wrapFirstQamHost([undefined, refused.host, host], patchAfter(handler))).toBe("wrapped");
    expect(memo.type).not.toBe(original);
  });

  it("answers why nothing was wrapped", () => {
    expect(mod.wrapFirstQamHost([], patchAfter(handler))).toBe("no-placeholder");
    expect(mod.wrapFirstQamHost([undefined, undefined], patchAfter(handler))).toBe("no-host");
  });

  it("renders through the patch it was handed once wrapped", () => {
    const { memo, host } = mountedHost();
    mod.wrapFirstQamHost([host], patchAfter(mod.widenWhileExpanded(() => true, EXPANDED)));

    expect(viewClassOf(memo.type({}))).toBe(`${PLACEHOLDER} ${EXPANDED}`);
  });
});

describe("takeBackQamWidthPatch", () => {
  const handler = (_args: unknown[], rendered: unknown) => rendered;

  it("takes the wrapper off the memo through the handle the patch answered, once", () => {
    const { memo, host } = mountedHost();
    const original = memo.type;
    const unpatch = vi.fn();
    mod.wrapFirstQamHost([host], patchAfter(handler, unpatch));

    mod.takeBackQamWidthPatch(() => []);
    mod.takeBackQamWidthPatch(() => []);

    expect(unpatch).toHaveBeenCalledTimes(1);
    expect(memo.type).toBe(original);
  });

  it("points the mounted fiber and its alternate back at what the memo carries afterwards", () => {
    const { memo, host } = mountedHost();
    const original = memo.type;
    mod.wrapFirstQamHost([host], patchAfter(handler));

    mod.takeBackQamWidthPatch(() => []);

    expect(host.type).toBe(original);
    expect(host.alternate?.type).toBe(original);
  });

  it("leaves a fiber alone that no longer holds the wrapper", () => {
    const { host } = mountedHost();
    mod.wrapFirstQamHost([host], patchAfter(handler));
    const elsewhere = () => null;
    host.type = elsewhere;

    mod.takeBackQamWidthPatch(() => []);

    expect(host.type).toBe(elsewhere);
  });

  it("lets a later install wrap the memo afresh", () => {
    const { memo, host } = mountedHost();
    const original = memo.type;
    const patch = patchAfter(handler);
    mod.wrapFirstQamHost([host], patch);
    mod.takeBackQamWidthPatch(() => []);

    expect(mod.wrapFirstQamHost([host], patch)).toBe("wrapped");

    expect(patch).toHaveBeenCalledTimes(2);
    expect(memo.type).not.toBe(original);
    expect(host.type).toBe(memo.type);
  });

  it("says so where taking it off the memo throws, and what that leaves, does not throw itself, and does not try a second time", () => {
    const { host } = mountedHost();
    const error = new Error("already unpatched");
    const unpatch = vi.fn(() => {
      throw error;
    });
    mod.wrapFirstQamHost([host], () => unpatch);

    expect(() => mod.takeBackQamWidthPatch(() => [])).not.toThrow();
    mod.takeBackQamWidthPatch(() => []);

    expect(unpatch).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls).toEqual([[TAKE_OFF_FAILED, error]]);
  });

  it("disarms the handler: once taken back it hands Steam's result back untouched without reading the flag", () => {
    const isExpanded = vi.fn(() => true);
    const widen = mod.widenWhileExpanded(isExpanded, EXPANDED);
    const { host } = mountedHost();
    mod.wrapFirstQamHost([host], patchAfter(widen));

    mod.takeBackQamWidthPatch(() => []);
    const steam = hostRender();

    expect(widen([], steam)).toBe(steam);
    expect(isExpanded).not.toHaveBeenCalled();
  });

  it("points a host mounted since the wrap, and its alternate, back at what the memo carries afterwards", () => {
    const { memo, host } = mountedHost();
    const original = memo.type;
    mod.wrapFirstQamHost([host], patchAfter(handler));
    // Mounted from the memo after the wrap: React copies the memo's render function onto it.
    const later: QamFiber = { ...host, type: memo.type, alternate: { type: memo.type } };

    mod.takeBackQamWidthPatch(() => [undefined, later]);

    expect(later.type).toBe(original);
    expect(later.alternate?.type).toBe(original);
  });

  it("points a fiber still on the wrapper at another plugin's patch stacked on it, not at the function from before", () => {
    const { memo, host } = mountedHost();
    const other = vi.fn();
    // Taking ours off from under another plugin's patch leaves that patch on the memo.
    mod.wrapFirstQamHost([host], (wrapped) => {
      wrapped.type = vi.fn();
      return () => {};
    });
    memo.type = other;

    mod.takeBackQamWidthPatch(() => []);

    expect(host.type).toBe(other);
    expect(host.alternate?.type).toBe(other);
  });

  it("points the fibers back where taking the wrapper off the memo threw after putting Steam's function back", () => {
    const { memo, host } = mountedHost();
    const original = memo.type;
    mod.wrapFirstQamHost([host], (wrapped) => {
      wrapped.type = vi.fn();
      return () => {
        wrapped.type = original;
        throw new Error("half done");
      };
    });

    mod.takeBackQamWidthPatch(() => []);

    expect(host.type).toBe(original);
    expect(host.alternate?.type).toBe(original);
  });

  it("points the fibers it recorded back, and says so, where finding the hosts on screen throws", () => {
    const { memo, host } = mountedHost();
    const original = memo.type;
    const unpatch = vi.fn();
    mod.wrapFirstQamHost([host], patchAfter(handler, unpatch));
    const error = new Error("no fiber");

    expect(() => {
      mod.takeBackQamWidthPatch(() => {
        throw error;
      });
    }).not.toThrow();

    expect(unpatch).toHaveBeenCalledTimes(1);
    expect(host.type).toBe(original);
    expect(warn.mock.calls).toEqual([[POINT_BACK_FAILED, error]]);
  });

  it("does not look for the hosts on screen where nothing was wrapped", () => {
    const findHosts = vi.fn(() => []);

    mod.takeBackQamWidthPatch(findHosts);

    expect(findHosts).not.toHaveBeenCalled();
  });
});

describe("tryQamWidthInstall", () => {
  it.each([
    "no-big-picture-window",
    "class-map-incomplete",
    "no-friends-ui-store",
    "no-placeholder",
    "no-host",
    "host-not-simple-memo",
  ] as const)("says once, with the reason, that the install wrapped nothing (%s)", (reason) => {
    mod.tryQamWidthInstall(() => reason);
    mod.tryQamWidthInstall(() => reason);

    expect(warn.mock.calls).toEqual([[`[Tender] ${NARROW}: the width patch wrapped nothing (${reason})`]]);
  });

  it("still says so where a later install wrapped nothing for a different reason", () => {
    mod.tryQamWidthInstall(() => "no-big-picture-window");
    mod.tryQamWidthInstall(() => "no-host");
    mod.tryQamWidthInstall(() => "no-big-picture-window");

    expect(warn.mock.calls).toEqual([
      ["[Tender] the wide pages stay at Steam's narrow width: the width patch wrapped nothing (no-big-picture-window)"],
      ["[Tender] the wide pages stay at Steam's narrow width: the width patch wrapped nothing (no-host)"],
    ]);
  });

  it("says once that the install threw, with the error itself, and does not throw itself", () => {
    const thrown = new Error("no fiber");
    const install = vi.fn((): "wrapped" => {
      throw thrown;
    });

    expect(() => {
      mod.tryQamWidthInstall(install);
    }).not.toThrow();
    mod.tryQamWidthInstall(install);

    expect(install).toHaveBeenCalledTimes(2);
    expect(warn.mock.calls).toEqual([
      [
        "[Tender] the wide pages stay at Steam's narrow width: the install of the wrapper on the QAM host threw",
        thrown,
      ],
    ]);
  });

  it("still says so where a later install throws a different error, and not where it is the same one again", () => {
    const first = new Error("no fiber");
    const other = new RangeError("no fiber");
    for (const thrown of [first, new Error("no fiber"), other]) {
      mod.tryQamWidthInstall(() => {
        throw thrown;
      });
    }

    const line =
      "[Tender] the wide pages stay at Steam's narrow width: the install of the wrapper on the QAM host threw";
    expect(warn.mock.calls).toEqual([
      [line, first],
      [line, other],
    ]);
  });

  it("says nothing where the host was wrapped", () => {
    mod.tryQamWidthInstall(() => "wrapped");

    expect(warn).not.toHaveBeenCalled();
  });

  it("keeps each of its lines apart from the others, so one written does not silence another", () => {
    const throwing = mod.widenWhileExpanded(() => {
      throw new Error("x");
    }, EXPANDED);
    const empty = mod.widenWhileExpanded(() => true, EXPANDED);
    const installThrowing = (): QamWidthInstall => {
      throw new Error("x");
    };

    for (let pass = 0; pass < 2; pass++) {
      mod.tryQamWidthInstall(() => "no-host");
      mod.tryQamWidthInstall(installThrowing);
      empty([], null);
      throwing([], null);
    }

    expect(warn).toHaveBeenCalledTimes(4);
  });
});

describe("the plugin log", () => {
  it("is told each console line once, without the prefix, and the error as its name and message", () => {
    const throwing = mod.widenWhileExpanded(() => {
      throw new TypeError("store moved");
    }, EXPANDED);
    const empty = mod.widenWhileExpanded(() => true, EXPANDED);

    for (let pass = 0; pass < 2; pass++) {
      throwing([], hostRender());
      empty([], null);
      mod.tryQamWidthInstall(() => "no-host");
      mod.tryQamWidthInstall(() => {
        throw new RangeError("no fiber");
      });
    }

    expect(logWarn.mock.calls).toEqual([
      [`${NARROW}: the wrapper on the QAM host threw: TypeError: store moved`],
      [`${NARROW}: the QAM host rendered no element to widen while the QAM's Friends flag was set (not-an-element)`],
      [`${NARROW}: the width patch wrapped nothing (no-host)`],
      [`${NARROW}: the install of the wrapper on the QAM host threw: RangeError: no fiber`],
    ]);
    expect(warn).toHaveBeenCalledTimes(4);
  });

  it("throwing does not make the wrapper throw out of Steam's render, nor the take-back out of onDismount", () => {
    logWarn.mockImplementation(() => {
      throw new Error("loader gone");
    });
    const steam = hostRender();
    const { host } = mountedHost();
    mod.wrapFirstQamHost([host], () => () => {
      throw new Error("already unpatched");
    });

    expect(
      mod.widenWhileExpanded(() => {
        throw new Error("store moved");
      }, EXPANDED)([], steam),
    ).toBe(steam);
    expect(() => {
      mod.takeBackQamWidthPatch(() => []);
    }).not.toThrow();
    expect(warn).toHaveBeenCalledTimes(2);
  });

  it("is told where the take-back fails, with the error as text", () => {
    const { host } = mountedHost();
    mod.wrapFirstQamHost([host], () => () => {
      throw new Error("already unpatched");
    });

    mod.takeBackQamWidthPatch(() => {
      throw new Error("no fiber");
    });

    expect(logWarn.mock.calls).toEqual([
      [`${TAKE_OFF_FAILED.replace("[Tender] ", "")}: Error: already unpatched`],
      [`${POINT_BACK_FAILED.replace("[Tender] ", "")}: Error: no fiber`],
    ]);
  });
});
