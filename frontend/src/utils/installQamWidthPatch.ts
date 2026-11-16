// coverage-exempt: every line reaches Steam — Big Picture's main window, React's
// live fiber tree and `@decky/ui`'s patcher — none of which the test runner
// has; what is decidable without them is `qamWidthPatch.ts`.
/**
 * Putting the wide pages' wrapper on Steam's QAM host.
 *
 * **Nothing here is reachable from a test**: a suite that faked the document,
 * the fibers and the patcher would assert against a tree written for it and
 * would pass whatever Steam does. `qam/installEntry.tsx` and
 * `bigpicture/patches/installGamePagePatch.ts` are exempt for the same reason.
 *
 * Nothing takes the patch back, for the reason `qam/installEntry.tsx` gives for
 * its own.
 */

import { afterPatch, findInTree, getReactInstance } from "@decky/ui";

import { FriendsUIStore, findSP, quickAccessMenuClasses } from "./deckyUiInternals";
import { isQamHost, type QamFiber, type QamWidthInstall, widenWhileExpanded, wrapFirstQamHost } from "./qamWidthPatch";

/** The QAM host above *placeholder*, walking up React's `return` chain from the placeholder's own fiber. */
function hostAbove(placeholder: Element): QamFiber | undefined {
  const host: unknown = findInTree(getReactInstance(placeholder), isQamHost, { walkable: ["return"] });
  return host ? (host as QamFiber) : undefined;
}

/** Install the wrapper on the QAM host mounted in Big Picture's main window, and answer whether it is on. */
export function installQamWidthPatch(): QamWidthInstall {
  const placeholderClass = quickAccessMenuClasses?.ViewPlaceholder;
  const expandedClass = quickAccessMenuClasses?.Expanded;
  if (!placeholderClass || !expandedClass) return "class-map-incomplete";
  const store = FriendsUIStore;
  if (!store) return "no-friends-ui-store";
  const doc = findSP()?.document;
  if (!doc) return "no-big-picture-window";
  const hosts = Array.from(doc.getElementsByClassName(placeholderClass), hostAbove);
  const handler = widenWhileExpanded(() => store.qamFriendsExpanded === true, expandedClass);
  return wrapFirstQamHost(hosts, (memo) => {
    afterPatch(memo, "type", handler);
  });
}
