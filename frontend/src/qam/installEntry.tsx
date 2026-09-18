// coverage-exempt: every line reaches Steam through `@decky/ui`, and the suite
// replaces that package wholesale — see the module docstring below.
/**
 * Putting Tender's entry into Steam's Quick Access strip, and keeping it there.
 *
 * **Nothing here is reachable from a test.** Every line reaches Steam through
 * `@decky/ui` — finding
 * the two renderers among minified exports, patching them, and walking the live
 * React tree of a menu that is already mounted. The suite replaces `@decky/ui`
 * wholesale and can build no such tree, so a test here would assert against a
 * tree written for it and would pass whatever Steam does. What IS decidable
 * without Steam — which entry to put there, and what a render pass does to the
 * array it lands in — is `quickAccessEntry.tsx`, and that half is covered.
 * `bigpicture/patches/gameDetailPatch.tsx` is exempt for the same reason and is
 * the game page's half of the same job.
 *
 * The rules this obeys, and why there is no unpatch, are stated on the module it
 * imports from.
 */

import { afterPatch, createReactTreePatcher, findInReactTree, findModuleByExport, getReactRoot } from "@decky/ui";
import type { Plugin } from "../api/host";
import { PLUGIN_NAME } from "../utils/toast";
import { buildEntry, syncEntry } from "./quickAccessEntry";

/**
 * The renderers Steam draws the Quick Access menu with: the browser view Gaming
 * Mode uses, and the embedded one.
 *
 * Both come from one module, found by the source text of its exports' render
 * functions — the same predicate Decky uses, and the only handle there is: the
 * module's exports are minified single letters and the component names survive
 * only inside the function bodies.
 */
function findQuickAccessRenderers(): { browserView: unknown; embedded: unknown } {
  const named = (name: string) => (value: unknown) =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- Steam's minified module exports; runtime shape is dynamic, no upstream types ship
    Boolean((value as any)?.type?.toString?.()?.includes(name));
  const module: unknown = findModuleByExport(named("QuickAccessMenuBrowserView"));
  if (typeof module !== "object" || module === null) return { browserView: undefined, embedded: undefined };
  const exports = Object.values(module);
  return {
    browserView: exports.find(named("QuickAccessMenuBrowserView")),
    embedded: exports.find(named("QuickAccessMenuEmbedded")),
  };
}

/**
 * Make an already-mounted menu pick the patch up, instead of waiting for the
 * next remount.
 *
 * React flattens the renderer's `memo` wrapper when it mounts and carries the
 * resolved type on the fiber, so swapping the module's export afterwards reaches
 * nothing that is already on screen. That is React's own behaviour, read off its
 * source rather than `@decky/ui`'s, which supplies the patcher and not the
 * flattening — and #1897 confirmed the consequence on the device: the patched
 * renderer's own handler was never entered (`outerCalls: 0`). Without the
 * adoption the entry would arrive at the menu's next remount, which follows from
 * the remount building a new array off the patched export rather than from an
 * observation. Decky adopts for the same reason.
 *
 * It was briefly suspected of the four Steam failures during that spike and then
 * ruled out: the cause was `initModuleCache()` running on import, confirmed by
 * an A/B against Decky's own copy of the package. The spike nonetheless left the
 * adoption off by default and labelled it unproven, so this is the first cut
 * that ships it.
 */
function adoptMountedMenu(renderers: { browserView: unknown; embedded: unknown }): void {
  const host = document.getElementById("root");
  if (!host) return;
  const root: unknown = getReactRoot(host);
  if (!root) return;
  const node = findInReactTree(
    root,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- Steam's React fiber; runtime shape is dynamic, no upstream types ship
    (fiber: any) =>
      fiber?.elementType === renderers.browserView ||
      (renderers.embedded !== undefined && fiber?.elementType === renderers.embedded),
  );
  if (!node?.elementType?.type) return;
  node.type = node.elementType.type;
  if (node.alternate) node.alternate.type = node.type;
}

/** What an install answers with. */
export interface QuickAccessEntryHandle {
  /** Did either renderer get patched? False means the entry will never appear. */
  readonly patched: boolean;
}

/**
 * Build Tender's entry, put it in the Quick Access strip, and keep it there.
 *
 * Takes the FACTORY rather than a built panel, because calling it exactly once
 * is part of the contract `definePlugin` describes: it registers Steam patches,
 * backend listeners and interceptors that are process-wide, and a second call
 * would install a second set of them.
 *
 * **There is no unpatch, and that is a consequence of how the panel is loaded
 * rather than an omission.** The injector plants `window.__tender_panel__`
 * before it imports anything and refuses to load the panel into a context that
 * already carries it (`backend/host/inject/bootstrap.py`); what clears that
 * marker is a JS-context rebuild, which takes this module, its patches, the
 * menu's tree and every tab array with it. So a second installation into a live
 * context is unreachable, and an unpatch would be a teardown nothing could ever
 * call — including `plugin.onDismount`, which has had no caller since the panel
 * stopped being loaded by Decky.
 */
export function installQuickAccessEntry(factory: () => Plugin): QuickAccessEntryHandle {
  const entry = buildEntry(factory());

  const renderers = findQuickAccessRenderers();
  const handler = createReactTreePatcher(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- Steam's internal React tree; runtime shape is dynamic, no upstream types ship
    [(tree: any) => findInReactTree(tree, (node: any) => node?.props?.onFocusNavDeactivated)],
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- Steam's internal React tree; runtime shape is dynamic, no upstream types ship
    (_args: unknown[], ret: any) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- Steam's internal React tree; runtime shape is dynamic, no upstream types ship
      const holder = findInReactTree(ret, (node: any) => node?.props?.tabs);
      if (Array.isArray(holder?.props?.tabs)) syncEntry(holder.props.tabs, entry);
      return ret;
    },
    PLUGIN_NAME,
  );

  let patched = false;
  for (const renderer of [renderers.browserView, renderers.embedded]) {
    if (renderer) {
      afterPatch(renderer, "type", handler);
      patched = true;
    }
  }
  if (patched) adoptMountedMenu(renderers);
  return { patched };
}
