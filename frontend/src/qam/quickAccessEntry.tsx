/**
 * Tender's own entry in Steam's Quick Access tab strip.
 *
 * Steam ships no API for adding one, so the two renderers that draw the menu are
 * patched and the entry is pushed into the tab array they hand back. Decky
 * Loader does the same thing to the same renderers; the two compose, because
 * `afterPatch` chains handlers rather than replacing them.
 *
 * **Nothing here touches Decky.** Not `window.__TABS_HOOK_INSTANCE` — Decky's
 * constructor calls `deinit()` on whatever it finds there, so writing it breaks
 * Decky's next boot — and not its `add()` either: Decky's own render counts the
 * `decky`-marked entries against its list length, and a foreign entry
 * desynchronises that guard into re-pushing every tab, twice per re-render, with
 * no convergence (measured in #1897). Our entry carries our own marker and our
 * own key and is invisible to that count. Neither is a presence check: the
 * bundle switch already asked whether Decky is serving
 * (`backend/host/inject/machine.py`) and the frontend does not re-ask.
 *
 * ## The three lifetime rules
 *
 * Every Quick Access remount — Gaming Mode to Desktop and back, Big Picture
 * opening in a window — replaces the menu's browser view and with it the React
 * tree, the tab array and the document. Each rule below is that one fact seen
 * from a different side, and none of them is visible to a unit test.
 *
 * 1. **No tab array is held at all.** The spike kept every array it had ever
 *    pushed into, so that it could take its entries back out; that set only
 *    grows — one dead array per remount, each holding the strip's entries and
 *    the React elements hanging off them, for the life of the process. There is
 *    nothing to take back out here ({@link installQuickAccessEntry} states why),
 *    so the strongest form of the rule is available: this module holds no array,
 *    and every dead one is collectable the moment its view is.
 * 2. **Re-assert the position on every pass** ({@link keepLast}).
 * 3. **Anything bound to the menu's own window is bound from inside the menu's
 *    React tree**, so the remount re-binds it. Nothing in this module binds
 *    anything there; the one binding the entry makes is the visibility listener
 *    inside `TabIcon`'s `useQuickAccessVisible`, which lives in an effect of a
 *    component the menu mounts. A listener attached here at module scope would
 *    be attached to a view that is already gone by the second remount.
 */

import type { ReactNode } from "react";
import { afterPatch, createReactTreePatcher, findInReactTree, findModuleByExport, getReactRoot } from "@decky/ui";
import type { Plugin } from "../api/host";
import { PLUGIN_NAME } from "../utils/toast";
import { PanelErrorBoundary } from "./PanelErrorBoundary";

/**
 * The key Steam files the entry's panel under, and the property that marks the
 * entry as ours.
 *
 * Both are Tender's own. The key is what Steam builds the panel element's id
 * from — `quickaccess_content_tender`, which `utils/qamExpansion.ts` matches by
 * prefix. The marker is what makes a render pass over an array we have already
 * pushed into a no-op, and it has to be the entry's own property rather than a
 * count kept here: a pass is not the only thing that can repeat, and an entry
 * that states what it is answers for an array this module has never seen.
 */
export const TENDER_TAB_KEY = "tender";
const TENDER_TAB_MARK = "tender";

/** What Steam's tab strip renders one entry from, plus our own marker. */
export interface QuickAccessTabEntry {
  key: string;
  title: string;
  /** Drawn in the strip. */
  tab: ReactNode;
  /** Drawn in the panel below it. */
  panel: ReactNode;
  [TENDER_TAB_MARK]: true;
}

const isOurs = (tab: unknown): tab is QuickAccessTabEntry =>
  typeof tab === "object" && tab !== null && (tab as Record<string, unknown>)[TENDER_TAB_MARK] === true;

/**
 * Move our entry to the end of the array, on every pass rather than at creation.
 *
 * **Where the entry sits is not ours to choose.** `afterPatch` runs the previous
 * handler first and the new one second, so whoever patches LAST pushes last and
 * lands lowest. Measured both ways in #1897: patched before Decky booted, Tender
 * came out above it; re-patched beside a running Decky, below. Install order is
 * a property of which program starts first, which nothing here decides — so the
 * placement is re-asserted instead of relied upon. The menu re-renders often (33
 * passes over a few opens, measured), and moving an entry is invisible to
 * Decky's own guard, which counts marked entries and never asks where they sit.
 *
 * Reads no marker but our own: "last" is a statement about our entry, so it
 * holds against any number of other tab providers rather than against Decky
 * specifically.
 */
function keepLast(tabs: unknown[]): void {
  for (let i = tabs.length - 2; i >= 0; i--) {
    if (isOurs(tabs[i])) tabs.push(...tabs.splice(i, 1));
  }
}

/**
 * Put the entry in `tabs` if it is not there, and put it last either way.
 *
 * Exported for the suite, which is the only thing that can drive it with an
 * array of its own — in Steam it is reached from the tree patcher below.
 */
export function syncEntry(tabs: unknown[], entry: QuickAccessTabEntry): void {
  if (!tabs.some(isOurs)) tabs.push(entry);
  keepLast(tabs);
}

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
 * nothing that is already on screen — predicted from `@decky/ui`'s source and
 * then measured in #1897, where without this the patch handler never ran at all.
 * Decky does the same thing for the same reason. It was briefly suspected of
 * three Steam failures during that spike and then ruled out: the cause was
 * `initModuleCache()` running on import, confirmed by an A/B against Decky's own
 * copy of the package.
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
  const plugin = factory();
  const entry: QuickAccessTabEntry = {
    key: TENDER_TAB_KEY,
    title: plugin.name,
    // The glyph the plugin declares, not one chosen here: `icon` is what the
    // factory answers with and the fallback page answers with a different node,
    // so drawing a second copy would put the wrong one in the strip on exactly
    // the start-up this cut cannot test.
    tab: plugin.icon,
    // The boundary wraps the panel and not the icon: a throw in the strip would
    // take the whole menu down with no panel mounted to catch it, and the icon
    // renders two constants and a boolean.
    panel: <PanelErrorBoundary>{plugin.content}</PanelErrorBoundary>,
    [TENDER_TAB_MARK]: true,
  };

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
