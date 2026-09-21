/**
 * Is the Quick Access menu on screen?
 *
 * `@decky/ui` answers this with a hook of its own, and this is a copy of its
 * body rather than a call into it, taken from `useQuickAccessVisible` in
 * `@decky/ui@4.12.1`'s `dist/custom-hooks/useQuickAccessVisible.js`. Upstream
 * evaluates its tree search as the ARGUMENT to `useState`, and that search
 * calls `.find` on `getGamepadNavigationTrees()` unguarded — which answers
 * `undefined` where the focus controller's context carries no trees, the
 * desktop client until Big Picture has been opened. So it throws during render,
 * before that first hook has been registered. **Neither a `try`/`catch` around
 * the call nor a conditional call repairs that**: the throwing render registers
 * no hooks where the next registers two, which is a hook-order violation, so
 * catching the throw only converts it into a different one on the following
 * render. Keeping a second copy of another package's predicates is what this
 * repo otherwise refuses; there is no third option here.
 *
 * `quickAccessVisible.test.ts` holds the copy against that file: the names it
 * reads, and that the getter's answer reaches the search unexamined. **The only
 * repair that check can see is an examination of the answer BETWEEN the call and
 * the search** — every other shape a fix could take is outside it. So a green
 * run is not evidence that upstream still throws, only that it has not stopped
 * throwing in that one shape; a failure is the shape it does read, and then this
 * module can go.
 *
 * **Two things beyond the guard differ, and neither changes an answer.** The
 * search runs in a lazy `useState` initialiser where upstream passes it as the
 * argument, so it is asked once rather than on every render. And upstream logs
 * `console.error('Could not get window of QuickAccess menu!')` from its effect
 * on any reading where its lookup came back empty — never with no trees at all,
 * having already thrown. This copy is silent on every one of those, because on
 * the desktop client they are the normal answer and the line would fire on every
 * wide page's mount; `test-setup.ts` also fails any test that emits
 * `console.error`.
 */

import { useEffect, useState } from "react";

import { getGamepadNavigationTrees } from "./deckyUiInternals";

/** Steam's id for the Quick Access menu's own navigation tree. */
const QUICK_ACCESS_TREE_ID = "QuickAccess-NA";

/**
 * The window the Quick Access menu is rendered in, or `undefined` on any of the
 * three readings that reach no window: no navigation trees at all, trees with
 * none of them the menu's, and the menu's own tree with no root mounted on it.
 *
 * Plugin code runs in a different window, and a mounted node of the menu's own
 * would answer this through its `ownerDocument`. The hook has no such node at
 * its first render: the caller's ref is still empty when the initialiser runs,
 * so the trees are the only handle available at the moment the question is put.
 */
function quickAccessWindow(): Window | undefined {
  const trees = getGamepadNavigationTrees();
  const root = trees?.find((tree) => tree?.id === QUICK_ACCESS_TREE_ID)?.m_Root?.m_element;
  return root?.ownerDocument.defaultView ?? undefined;
}

/**
 * Whether the Quick Access menu is on screen, re-answered as its document's
 * visibility changes.
 *
 * **Answers `true` wherever `quickAccessWindow` reaches no window**, on any of
 * the readings it names. The answer is what a caller proceeds on, and the other
 * default makes a page that cannot find the menu permanently inert; the same
 * default, for the same reason, is taken by `useWideQamPanel`'s
 * `owningTabActive`.
 */
export function useQuickAccessVisible(): boolean {
  const [hidden, setHidden] = useState(() => quickAccessWindow()?.document.hidden ?? false);

  useEffect(() => {
    const view = quickAccessWindow();
    if (!view) return;

    const onVisibilityChange = () => setHidden(view.document.hidden);
    view.addEventListener("visibilitychange", onVisibilityChange);
    return () => view.removeEventListener("visibilitychange", onVisibilityChange);
  }, []);

  return !hidden;
}
