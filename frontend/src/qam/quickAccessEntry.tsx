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
 * desynchronises that guard into re-pushing every tab with no convergence. Both
 * of those are read off Decky's source rather than observed — #1897 deliberately
 * did not take that route, so its runaway never ran. Our entry carries our own marker and our
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
 *    anything there, and neither does the glyph — it is static and reads no
 *    state; what does is inside effects of the pages the panel mounts. A
 *    listener attached here at module scope would be attached to a view that is
 *    already gone by the second remount.
 */

import type { ReactNode } from "react";
import type { Plugin } from "../api/host";
import { quickAccessMenuClasses } from "../utils/deckyUiInternals";
import { PanelErrorBoundary } from "./PanelErrorBoundary";

/**
 * The key Steam files the entry's panel under, and the property that marks the
 * entry as ours.
 *
 * Both are Tender's own. The key is what Steam builds the panel element's id
 * from; what id a string key produces has not been measured, which is why
 * `utils/qamExpansion.ts` matches the `quickaccess_content_` prefix and never
 * the whole. The marker is what makes a render pass over an array we have already
 * pushed into a no-op, and it has to be the entry's own property rather than a
 * count kept here: a pass is not the only thing that can repeat, and an entry
 * that states what it is answers for an array this module has never seen.
 */
export const TENDER_TAB_KEY = "tender";
const TENDER_TAB_MARK = "tender";

/** What Steam's tab strip renders one entry from, plus our own marker. */
export interface QuickAccessTabEntry {
  key: string;
  /** Drawn above the panel as the entry's heading. */
  title: ReactNode;
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
 * placement is re-asserted instead of relied upon. #1897 proved the re-assertion
 * rather than assuming it: the entry was shoved to index 0 by hand and the next
 * pass put it back at the end. Moving an entry is invisible to Decky's own
 * guard, which counts marked entries and never asks where they sit.
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
 * The entry Steam's tab strip renders Tender from.
 *
 * Everything here is decided from `plugin` and from one read of Steam's own
 * bundle — `quickAccessMenuClasses`, for the heading — so answering for it
 * without Steam means handing it that class map, which is what the suite mocks.
 * Putting the entry in the strip is {@link installQuickAccessEntry}'s, in
 * `installEntry.tsx`.
 */
export function buildEntry(plugin: Plugin): QuickAccessTabEntry {
  return {
    key: TENDER_TAB_KEY,
    // An element carrying Steam's own heading class, because Steam's tabs hand
    // it one: a bare string lands as a text node in the panel container at body
    // size, well under what the tabs beside it are drawn at — the two readings
    // are in `docs/architecture/qam-panel.md`, The entry. Without the class map
    // the start-up check has already refused the panel, so this heads the
    // fallback page, and it costs that page its styling rather than its heading.
    title: <div className={quickAccessMenuClasses?.Title}>{plugin.name}</div>,
    // The glyph the plugin declares, not one chosen here: `icon` is what the
    // factory answers with and the fallback page answers with a different node,
    // so drawing a second copy would put the wrong one in the strip on exactly
    // the start-up this cut cannot test.
    tab: plugin.icon,
    // The boundary wraps the panel and not the icon, because a boundary in the
    // strip has no panel mounted to render its fallback into — a throw there
    // takes the menu down whatever we do. That is the whole of the reason, and
    // it would stand for an icon of any size: this one is static artwork that
    // reads no state, but a busier one would be left unwrapped for the same
    // reason.
    panel: <PanelErrorBoundary>{plugin.content}</PanelErrorBoundary>,
    [TENDER_TAB_MARK]: true,
  };
}
