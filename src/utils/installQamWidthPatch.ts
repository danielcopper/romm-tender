/**
 * Putting the wide pages' wrapper on Steam's QAM host, and taking it back.
 *
 * Past the class-map, store and window checks nothing here is reachable from a
 * test: from there every line reaches Steam — Big Picture's main window,
 * React's live fiber tree and `@decky/ui`'s patcher — none of which the test
 * runner has, and a suite that faked them would assert against a tree written
 * for it and would pass whatever Steam does. What is decidable without them is
 * `qamWidthPatch.ts`. This module is therefore excluded from coverage, as
 * `src/patches/` is for the same reason (`sonar-project.properties`).
 */

import { afterPatch, findInTree, getReactInstance } from "@decky/ui";

import { FriendsUIStore, findSP, quickAccessMenuClasses } from "./deckyUiInternals";
import {
  isQamHost,
  type QamFiber,
  type QamWidthInstall,
  takeBackQamWidthPatch,
  widenWhileExpanded,
  wrapFirstQamHost,
} from "./qamWidthPatch";

/** The QAM host above *placeholder*, walking up React's `return` chain from the placeholder's own fiber. */
function hostAbove(placeholder: Element): QamFiber | undefined {
  const host: unknown = findInTree(getReactInstance(placeholder), isQamHost, { walkable: ["return"] });
  return host ? (host as QamFiber) : undefined;
}

/** The host above each of *doc*'s placeholders, one entry per placeholder. */
const hostsIn = (doc: Document, placeholderClass: string): (QamFiber | undefined)[] =>
  Array.from(doc.getElementsByClassName(placeholderClass), hostAbove);

/** Install the wrapper on the QAM host in Big Picture's main window, and answer whether it is on, or why not. */
export function installQamWidthPatch(): QamWidthInstall {
  const placeholderClass = quickAccessMenuClasses?.ViewPlaceholder;
  const expandedClass = quickAccessMenuClasses?.Expanded;
  if (!placeholderClass || !expandedClass) return "class-map-incomplete";
  const store = FriendsUIStore;
  if (!store) return "no-friends-ui-store";
  const doc = findSP()?.document;
  if (!doc) return "no-big-picture-window";
  const handler = widenWhileExpanded(() => store.qamFriendsExpanded === true, expandedClass);
  return wrapFirstQamHost(hostsIn(doc, placeholderClass), (memo) => {
    const patch = afterPatch(memo, "type", handler);
    return () => patch.unpatch();
  });
}

/**
 * Take the wrapper back, from the plugin's `onDismount`, with the hosts in Big
 * Picture's main window as they stand now.
 */
export function unregisterQamWidthPatch(): void {
  takeBackQamWidthPatch(() => {
    const placeholderClass = quickAccessMenuClasses?.ViewPlaceholder;
    const doc = findSP()?.document;
    return placeholderClass && doc ? hostsIn(doc, placeholderClass) : [];
  });
}
