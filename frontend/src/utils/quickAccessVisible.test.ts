/**
 * The guard the upstream hook does not have, the behaviour it is a copy of, and
 * the original held still beside it.
 *
 * The navigation trees are Steam's and happy-dom has none, so the search is
 * driven through a stub of `deckyUiInternals`: what the tests vary is what that
 * answers, which is exactly the axis the defect sits on.
 *
 * The two branches must stay distinguishable. Both of them let a caller
 * proceed, so a test that only asserted `true` would pass whether the trees
 * were found or not — the menu-is-open branch is pinned through the document
 * visibility it reads and re-reads, which the not-established branch never
 * touches.
 *
 * The last block is a drift lock over `@decky/ui`'s own shipped source rather
 * than over anything here; what a failure there asks for is a reading of that
 * file, never an edit to make it green.
 */

import { readFileSync } from "node:fs";

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, renderHook, act } from "@testing-library/react";
import { createElement, useRef, type FC } from "react";

import { type DeclaredFunction, declaredFunctions } from "../test-utils/jsFunctionScanner";
import type { GamepadNavigationTree } from "./deckyUiInternals";
import { WIDE_ROOT_CLASS, useWideQamPanel } from "./qamExpansion";
import { useQuickAccessVisible } from "./quickAccessVisible";

// The hook is stubbed suite-wide, so that every page test takes the
// menu-is-open branch rather than this module's own answer (`test-setup.ts`).
// This is the one file that has to reach the real thing.
vi.unmock("./quickAccessVisible");

let navTrees: GamepadNavigationTree[] | undefined;

// `quickAccessMenuClasses` is here for `useWideQamPanel`, which the last-but-one
// block renders through this hook; `undefined` is what the probe answers under
// happy-dom anyway.
vi.mock("./deckyUiInternals", () => ({
  getGamepadNavigationTrees: () => navTrees,
  quickAccessMenuClasses: undefined,
}));

/** The menu's own window, reduced to what the hook reads and subscribes to. */
interface FakeQuickAccessWindow {
  document: { hidden: boolean };
  listeners: Set<() => void>;
  /** How often a subscription was given back, so an unmount can be pinned. */
  removals: number;
  addEventListener(name: string, listener: () => void): void;
  removeEventListener(name: string, listener: () => void): void;
}

function fakeQuickAccessWindow(hidden = false): FakeQuickAccessWindow {
  const listeners = new Set<() => void>();
  const view: FakeQuickAccessWindow = {
    document: { hidden },
    listeners,
    removals: 0,
    addEventListener(name, listener) {
      if (name === "visibilitychange") listeners.add(listener);
    },
    removeEventListener(name, listener) {
      if (name !== "visibilitychange") return;
      listeners.delete(listener);
      view.removals += 1;
    },
  };
  return view;
}

/** A tree the hook's lookup accepts, rooted on an element in `view`. */
function treeFor(id: string, view: FakeQuickAccessWindow): GamepadNavigationTree {
  return { id, m_Root: { m_element: { ownerDocument: { defaultView: view } } } } as unknown as GamepadNavigationTree;
}

function fireVisibilityChange(view: FakeQuickAccessWindow, hidden: boolean) {
  act(() => {
    view.document.hidden = hidden;
    for (const listener of view.listeners) listener();
  });
}

describe("useQuickAccessVisible", () => {
  beforeEach(() => {
    navTrees = undefined;
  });

  it("reads the visibility of the window holding the QuickAccess tree", () => {
    const view = fakeQuickAccessWindow(true);
    navTrees = [treeFor("SomeOtherTree", fakeQuickAccessWindow()), treeFor("QuickAccess-NA", view)];

    const { result } = renderHook(() => useQuickAccessVisible());

    // Hidden, and answered as hidden — which is what tells a reading that found
    // the window from one that established nothing, since the latter can never
    // answer `false`. No wide-page test reaches either: `test-setup.ts`
    // replaces this whole module for them.
    expect(result.current).toBe(false);
    expect(view.listeners.size).toBe(1);
  });

  it("re-answers as the menu's document is hidden and shown again", () => {
    const view = fakeQuickAccessWindow();
    navTrees = [treeFor("QuickAccess-NA", view)];

    const { result, unmount } = renderHook(() => useQuickAccessVisible());

    expect(result.current).toBe(true);

    fireVisibilityChange(view, true);
    expect(result.current).toBe(false);

    fireVisibilityChange(view, false);
    expect(result.current).toBe(true);

    unmount();
    expect(view.removals).toBe(1);
  });

  it("renders without throwing while the focus controller holds no trees at all", () => {
    // The reading this module exists for; what upstream does with it, and why a
    // guard at the call site was not available, is at `quickAccessVisible.ts`.
    navTrees = undefined;

    const { result } = renderHook(() => useQuickAccessVisible());

    expect(result.current).toBe(true);
  });

  it("renders without throwing while the trees carry no QuickAccess tree", () => {
    navTrees = [treeFor("GamepadUI_Full_Root", fakeQuickAccessWindow(true))];

    const { result } = renderHook(() => useQuickAccessVisible());

    // The stranger's window is hidden and is not read: an answer of `false`
    // here would mean the lookup matched something that is not the menu.
    expect(result.current).toBe(true);
  });

  it("subscribes to nothing when the menu's window could not be reached", () => {
    const stranger = fakeQuickAccessWindow();
    navTrees = [treeFor("GamepadUI_Full_Root", stranger)];

    const { unmount } = renderHook(() => useQuickAccessVisible());

    expect(stranger.listeners.size).toBe(0);
    // And the teardown of a subscription never taken is a no-op rather than a throw.
    expect(() => unmount()).not.toThrow();
  });
});

describe("a wide page rendered through this hook with no trees", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    // The page's own stylesheet, which the module drops on unmount — cleared
    // here as well so a failing assertion cannot leave one for the next file.
    document.head.replaceChildren();
  });

  // The issue's own wording is about a PAGE, not about the hook: what throws
  // takes the tree down, so the claim worth holding is that a real
  // `useWideQamPanel` render survives the reading. It is the one composition
  // `qamExpansion.test.tsx` cannot make — that file stubs this hook with a store
  // it can flip, where the constant `() => true` of `test-setup.ts` could never
  // reach its clear-on-close path.
  it("renders, and takes the expansion the unestablished reading lets it take", () => {
    navTrees = undefined;
    const post = vi.spyOn(window, "postMessage").mockImplementation(() => {});

    const Page: FC = () => {
      const rootRef = useRef<HTMLDivElement>(null);
      useWideQamPanel(rootRef);
      return createElement("div", { ref: rootRef, className: WIDE_ROOT_CLASS });
    };

    render(createElement(Page));

    // Not merely "did not throw": the reading has to arrive at the consumer as
    // proceed, and the expansion message is the only observable that says so.
    expect(post).toHaveBeenCalledWith({ message: "QamFriendsExpanded" }, window.origin);
  });
});

/** The body this module is a copy of, as the installed package ships it. */
const UPSTREAM_HOOK = `${process.cwd()}/node_modules/@decky/ui/dist/custom-hooks/useQuickAccessVisible.js`;

/** The call whose answer upstream searches unguarded, and the copy's own source. */
const GETTER_CALL = "getGamepadNavigationTrees(";

/**
 * Everything upstream may put between that call and the search while carrying
 * the answer straight to it: the call's own closing paren, then nothing but
 * whitespace, a `;`, a `return`, one opening paren and one identifier.
 *
 * Stated as what IS allowed rather than as a list of guards, because the list
 * is the thing that cannot be finished — `&&`, `|| []`, `?? []`, `?.`,
 * `Array.isArray(…) &&` and an early `if` are six spellings of one fix and
 * there is no reason to think they are all of them. Each needs a character
 * outside the vocabulary above: an operator, a second paren, a keyword or a
 * call. The identifier is optional so that searching the getter's answer
 * inline stays green, which is unguarded too.
 */
const PLUMBING = /^\s*\)\s*;?\s*(?:return\s*)?\(?\s*(?:[A-Za-z_$][\w$]*)?\s*$/;

describe("the upstream hook this module copies", () => {
  // `@decky/ui` is a caret range that Renovate moves, so the original can
  // change under a copy that names its provenance in prose and nothing else.
  // What is held is what the copy DEPENDS on: the names it reads, and that the
  // getter's answer reaches the search unexamined. What a green run is and is
  // not evidence about is stated at `quickAccessVisible.ts`.
  const source = (): string => readFileSync(UPSTREAM_HOOK, "utf8");

  /**
   * Upstream's own reading of the trees, found by what it CALLS rather than by
   * its name — `getQuickAccessWindow` today, and the copy depends on that name
   * no more than on its locals.
   */
  function readingFunction(): DeclaredFunction {
    const reading = declaredFunctions(source()).filter((fn) => fn.body.includes(GETTER_CALL));
    // The names go in the message rather than into the assertion: a failure
    // here says upstream moved or re-split the reading, and the reader needs to
    // see every candidate — where asserting the names would pin the very
    // spelling this lookup exists not to depend on.
    const named = reading.map((fn) => fn.name).join(", ") || "none";
    expect(reading, `functions calling the getter: ${named}`).toHaveLength(1);
    return reading[0]!;
  }

  it("is a file with something in it", () => {
    // Both an absent and an empty file already fail the reads below. What this
    // adds is a first failure line naming the cause, rather than leaving a
    // reader to infer it from what the later assertions happen to say.
    expect(source().length).toBeGreaterThan(0);
  });

  it("still reaches the menu's window by the id and the path this copy reads", () => {
    const upstream = source();
    expect(upstream).toContain(GETTER_CALL);
    expect(upstream).toContain("'QuickAccess-NA'");
    expect(upstream).toContain("m_Root");
    expect(upstream).toContain("m_element");
    expect(upstream).toContain("document.hidden");
    expect(upstream).toContain("'visibilitychange'");
  });

  it("carries the getter's answer to the search as plumbing and nothing else", () => {
    const { body } = readingFunction();
    const afterGetter = body.indexOf(GETTER_CALL) + GETTER_CALL.length;
    const search = body.indexOf(".find(", afterGetter);
    expect(search).toBeGreaterThan(afterGetter);
    expect(body.slice(afterGetter, search)).toMatch(PLUMBING);
  });
});
