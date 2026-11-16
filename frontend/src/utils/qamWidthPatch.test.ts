/**
 * The wrapper on Steam's QAM host, over a host shaped the way Steam's is
 * mounted: a `memo` resolved onto its fiber, found above the placeholder the
 * QAM's view is laid over, rendering that view among its children.
 *
 * What happy-dom cannot show is Steam moving the view once the class is there;
 * what the wrapper hands React is all decided here.
 */

import { createElement, type FC, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const probe: {
  classes: Record<string, string> | undefined;
  store: { readonly qamFriendsExpanded?: unknown } | undefined;
  sp: Window | undefined;
} = { classes: undefined, store: undefined, sp: undefined };

vi.mock("./deckyUiInternals", () => ({
  get quickAccessMenuClasses() {
    return probe.classes;
  },
  get FriendsUIStore() {
    return probe.store;
  },
  findSP: () => probe.sp,
}));

import { installQamWidthPatch, widenQamHostRender } from "./qamWidthPatch";

const PLACEHOLDER = "ViewPlaceholder_hash";
const EXPANDED = "Expanded_hash";

const BrowserViewHost: FC<{ className?: string | undefined; browser?: unknown }> = () => null;
const Backdrop: FC = () => null;

interface Fiber {
  type?: unknown;
  elementType?: unknown;
  alternate?: Fiber | null;
  return?: Fiber | null;
  memoizedProps?: unknown;
}

interface HostProps {
  viewClass: string;
}

/**
 * Steam's QAM host as one render returns it: a container whose children are
 * an array, one of which is handed the QAM's view.
 */
const steamRender = vi.fn((props: HostProps) =>
  createElement(
    "div",
    { className: "Container" },
    createElement(Backdrop, { key: "backdrop" }),
    createElement(BrowserViewHost, {
      key: "view",
      className: props.viewClass,
      browser: { SetBounds: () => {} },
    }),
  ),
);

interface MountedHost {
  memo: { $$typeof: symbol; type: unknown };
  host: Fiber;
  /** A memo mounted between the host and the placeholder, which is not the host. */
  between: { $$typeof: symbol; type: unknown };
  placeholder: HTMLElement;
}

/**
 * The host mounted the way React mounts a simple `memo`: the resolved render
 * function on the fiber and its alternate, the memo itself as `elementType`,
 * and the placeholder's own fiber two levels below it.
 */
function mountHost(hostType: "simple" | "memo" = "simple"): MountedHost {
  const memo = { $$typeof: Symbol.for("react.memo"), type: steamRender as unknown };
  const fiberType = hostType === "simple" ? steamRender : memo;
  const host: Fiber = {
    type: fiberType,
    elementType: memo,
    memoizedProps: { active: true, ModalManager: {} },
    alternate: { type: fiberType },
    return: { type: "div", memoizedProps: {}, return: null },
  };
  const between = { $$typeof: Symbol.for("react.memo"), type: BrowserViewHost as unknown };
  const betweenFiber: Fiber = {
    type: BrowserViewHost,
    elementType: between,
    memoizedProps: { browser: {} },
    return: host,
  };
  const placeholderFiber: Fiber = { type: "div", memoizedProps: { className: PLACEHOLDER }, return: betweenFiber };
  const placeholder = document.createElement("div");
  placeholder.className = PLACEHOLDER;
  (placeholder as unknown as Record<string, unknown>)["__reactFiber$test"] = placeholderFiber;
  document.body.appendChild(placeholder);
  return { memo, host, between, placeholder };
}

/** One render through whatever the memo holds now, as React would call it. */
const renderThrough = (memo: MountedHost["memo"], viewClass = "ViewPlaceholder_hash") =>
  (memo.type as (props: HostProps) => ReactElement)({ viewClass });

/** The className the view's element was handed in *rendered*. */
function viewClassOf(rendered: ReactElement): string | undefined {
  const children = (rendered.props as { children: ReactElement[] }).children;
  const view = children.find((child) => child.type === BrowserViewHost);
  return (view?.props as { className?: string } | undefined)?.className;
}

let expanded = false;

beforeEach(() => {
  expanded = false;
  probe.classes = { ViewPlaceholder: PLACEHOLDER, Expanded: EXPANDED };
  probe.store = {
    get qamFriendsExpanded() {
      return expanded;
    },
  };
  probe.sp = window;
  steamRender.mockClear();
});

afterEach(() => {
  document.body.replaceChildren();
});

describe("the wrapper on Steam's QAM host", () => {
  it("adds the Expanded class to the element carrying the view while the flag is set", () => {
    const { memo } = mountHost();
    installQamWidthPatch();
    expanded = true;

    const rendered = renderThrough(memo);

    expect(viewClassOf(rendered)).toBe(`${PLACEHOLDER} ${EXPANDED}`);
    const children = (rendered.props as { children: ReactElement[] }).children;
    expect(children[0]?.type).toBe(Backdrop);
    expect(children.map((child) => child.key)).toEqual(["backdrop", "view"]);
  });

  it("returns Steam's result untouched while the flag is not set", () => {
    const { memo } = mountHost();
    installQamWidthPatch();

    const rendered = renderThrough(memo);

    expect(rendered).toBe(steamRender.mock.results[0]?.value);
    expect(viewClassOf(rendered)).toBe(PLACEHOLDER);
  });

  it("does not add the class a second time when Steam has already set it", () => {
    const { memo } = mountHost();
    installQamWidthPatch();
    expanded = true;

    // On the Friends tab Steam puts the class there itself.
    const rendered = renderThrough(memo, `${PLACEHOLDER} ${EXPANDED}`);

    expect(rendered).toBe(steamRender.mock.results[0]?.value);
    expect(viewClassOf(rendered)).toBe(`${PLACEHOLDER} ${EXPANDED}`);
  });

  it("returns Steam's result when its own work throws", () => {
    const { memo } = mountHost();
    probe.store = {
      get qamFriendsExpanded(): boolean {
        throw new Error("store moved");
      },
    };
    installQamWidthPatch();

    const rendered = renderThrough(memo);

    expect(rendered).toBe(steamRender.mock.results[0]?.value);
  });

  it("wraps the component once when installed twice", () => {
    const { memo } = mountHost();

    expect(installQamWidthPatch()).toBe(true);
    const wrapped = memo.type;
    expect(installQamWidthPatch()).toBe(true);

    expect(memo.type).toBe(wrapped);
    renderThrough(memo);
    expect(steamRender).toHaveBeenCalledTimes(1);
  });

  it("wraps the host, not a component mounted between it and the placeholder", () => {
    const { memo, between } = mountHost();

    installQamWidthPatch();

    expect(memo.type).not.toBe(steamRender);
    expect(between.type).toBe(BrowserViewHost);
  });

  it("puts the wrapper on the mounted fiber and its alternate as well as on the memo", () => {
    const { memo, host } = mountHost();

    installQamWidthPatch();

    expect(memo.type).not.toBe(steamRender);
    expect(host.type).toBe(memo.type);
    expect(host.alternate?.type).toBe(memo.type);
  });
});

describe("widenQamHostRender", () => {
  const view = (className?: string) =>
    createElement(BrowserViewHost, { key: "view", className, browser: { SetBounds: () => {} } });

  it("answers with what it was handed where that is not an element", () => {
    expect(widenQamHostRender(null, EXPANDED)).toBeNull();
  });

  it("answers with the element where its children are not an array", () => {
    const single = createElement("div", null, view(PLACEHOLDER));

    expect(widenQamHostRender(single, EXPANDED)).toBe(single);
  });

  it("answers with the element where no child is handed the view", () => {
    const bare = createElement(
      "div",
      null,
      createElement(Backdrop, { key: "a" }),
      createElement(Backdrop, { key: "b" }),
    );

    expect(widenQamHostRender(bare, EXPANDED)).toBe(bare);
  });

  it("answers with the element where the view's child carries no class to extend", () => {
    const unclassed = createElement("div", null, createElement(Backdrop, { key: "a" }), view());

    expect(widenQamHostRender(unclassed, EXPANDED)).toBe(unclassed);
  });
});

describe("what the install leaves alone", () => {
  it("wraps nothing when the class map has no Expanded class", () => {
    const { memo } = mountHost();
    probe.classes = { ViewPlaceholder: PLACEHOLDER };

    expect(installQamWidthPatch()).toBe(false);
    expect(memo.type).toBe(steamRender);
  });

  it("wraps nothing without the FriendsUI store", () => {
    const { memo } = mountHost();
    probe.store = undefined;

    expect(installQamWidthPatch()).toBe(false);
    expect(memo.type).toBe(steamRender);
  });

  it("wraps nothing while Big Picture's main window cannot be found", () => {
    const { memo } = mountHost();
    probe.sp = undefined;

    expect(installQamWidthPatch()).toBe(false);
    expect(memo.type).toBe(steamRender);
  });

  it("wraps nothing where no host stands above the placeholder", () => {
    const { memo, placeholder } = mountHost();
    (placeholder as unknown as Record<string, unknown>)["__reactFiber$test"] = { type: "div", return: null };

    expect(installQamWidthPatch()).toBe(false);
    expect(memo.type).toBe(steamRender);
  });

  it("wraps nothing where the host is not mounted as a simple memo", () => {
    // A `memo` with a comparator or default props is mounted with the memo
    // itself as the fiber's type; putting a function there would break it.
    const { memo, host } = mountHost("memo");

    expect(installQamWidthPatch()).toBe(false);
    expect(memo.type).toBe(steamRender);
    expect(host.type).toBe(memo);
  });
});
