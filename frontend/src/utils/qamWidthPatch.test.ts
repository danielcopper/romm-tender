/**
 * The half of the wide pages' wrapper that is decidable without Steam: which
 * fiber is the QAM host, what the patch handler makes of one render of it, the
 * once-per-memo install and its adoption of the mounted fiber, and what the
 * console is told.
 *
 * What happy-dom cannot show is Steam moving the view once the class is there,
 * nor `@decky/ui`'s patcher reaching a live host: *patch* below is a stand-in
 * that does to the memo what `afterPatch` does.
 *
 * Each test loads a fresh copy of the module, because which lines the console
 * has been told is module state, kept for the life of the JS context.
 */

import { createElement, type FC, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import type { QamFiber, SimpleMemo } from "./qamWidthPatch";

type QamWidthPatch = typeof import("./qamWidthPatch");

const EXPANDED = "Expanded_hash";
const PLACEHOLDER = "ViewPlaceholder_hash";

const BrowserViewHost: FC<{ className?: string | undefined; browser?: unknown }> = () => null;
const Backdrop: FC = () => null;

const view = (className: string | undefined) =>
  createElement(BrowserViewHost, { key: "view", className, browser: { SetBounds: () => {} } });

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

/** What `afterPatch` does to the memo: its `type` replaced by one that hands the original's result to *handler*. */
const patchAfter = (handler: (args: unknown[], rendered: unknown) => unknown) =>
  vi.fn((memo: SimpleMemo) => {
    const original = memo.type;
    memo.type = (...args: unknown[]) => handler(args, original(...args));
  });

let mod: QamWidthPatch;
let warn: MockInstance<typeof console.warn>;

beforeEach(async () => {
  vi.resetModules();
  mod = await import("./qamWidthPatch");
  warn = vi.spyOn(console, "warn").mockImplementation(() => {});
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

  it("returns Steam's result when its own work throws, and says so once", () => {
    const handler = mod.widenWhileExpanded(() => {
      throw new Error("store moved");
    }, EXPANDED);
    const steam = hostRender();

    expect(handler([], steam)).toBe(steam);
    handler([], hostRender());

    expect(warn.mock.calls).toEqual([
      ["[Tender] the wide pages stay at Steam's narrow width: the wrapper on the QAM host threw: Error: store moved"],
    ]);
  });

  it.each([
    ["is not an element", null],
    ["has children that are not an array", createElement("div", null, view(PLACEHOLDER))],
    [
      "hands the view to no child",
      createElement("div", null, createElement(Backdrop, { key: "a" }), createElement(Backdrop, { key: "b" })),
    ],
    [
      "hands it to a child with no class to extend",
      createElement("div", null, createElement(Backdrop), view(undefined)),
    ],
  ])("returns Steam's result, and says once that nothing was widened, where it %s", (_shape, steam) => {
    const handler = mod.widenWhileExpanded(() => true, EXPANDED);

    expect(handler([], steam)).toBe(steam);
    handler([], steam);

    expect(warn.mock.calls).toEqual([
      ["[Tender] the wide pages stay at Steam's narrow width: the QAM host rendered no element its view is laid over"],
    ]);
  });

  it("says nothing while the flag is not set, whatever Steam rendered", () => {
    mod.widenWhileExpanded(() => false, EXPANDED)([], null);

    expect(warn).not.toHaveBeenCalled();
  });
});

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

describe("tryQamWidthInstall", () => {
  it.each([
    "class-map-incomplete",
    "no-friends-ui-store",
    "no-placeholder",
    "no-host",
    "host-not-simple-memo",
  ] as const)("says once, with the reason, that the install wrapped nothing (%s)", (reason) => {
    mod.tryQamWidthInstall(() => reason);
    mod.tryQamWidthInstall(() => reason);

    expect(warn.mock.calls).toEqual([
      [`[Tender] the wide pages stay at Steam's narrow width: the width patch wrapped nothing (${reason})`],
    ]);
  });

  it("says once that the install threw, and does not throw itself", () => {
    const install = vi.fn((): "wrapped" => {
      throw new Error("no fiber");
    });

    expect(() => {
      mod.tryQamWidthInstall(install);
    }).not.toThrow();
    mod.tryQamWidthInstall(() => "no-host");

    expect(install).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls).toEqual([
      [
        "[Tender] the wide pages stay at Steam's narrow width: the width patch wrapped nothing (the install threw: Error: no fiber)",
      ],
    ]);
  });

  it("says nothing where Big Picture's window was not found, nor where the host was wrapped", () => {
    mod.tryQamWidthInstall(() => "no-big-picture-window");
    mod.tryQamWidthInstall(() => "wrapped");

    expect(warn).not.toHaveBeenCalled();

    mod.tryQamWidthInstall(() => "no-host");

    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("writes each of its three lines once, apart from the others", () => {
    const throwing = mod.widenWhileExpanded(() => {
      throw new Error("x");
    }, EXPANDED);
    const empty = mod.widenWhileExpanded(() => true, EXPANDED);

    for (let pass = 0; pass < 2; pass++) {
      mod.tryQamWidthInstall(() => "no-host");
      empty([], null);
      throwing([], null);
    }

    expect(warn).toHaveBeenCalledTimes(3);
  });
});
