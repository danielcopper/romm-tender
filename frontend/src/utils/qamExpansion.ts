/**
 * The two levers that widen the plugin's Quick Access Menu panel from 348 px to
 * 854 px, and the paths that clear them again.
 *
 * Steam ships no API for either. Both are internals with no compatibility
 * promise, measured on the device rather than read from documentation; the
 * decision to use them, and what it rests on, is
 * `docs/adr/0029-wide-qam-pages-drive-steams-friends-expansion.md`.
 *
 * The flag the first lever sets is Steam's own and global, so whoever sets it
 * clears it: a wide page that leaks it leaves Steam's QAM expanded until the
 * Friends tab toggles it back. `useWideQamPanel` covers the three paths a
 * mounted page can observe. `collapseQamOnDismount` was the fourth, reached
 * from Decky's teardown hook; nothing calls it now.
 *
 * The injected sheet carries two rules that are not about width: a focus
 * outline for a DISABLED button, which Steam's own stylesheet omits, and a
 * deliberate override of Steam's bottom padding on a tabbed page's content
 * scroller. Both ride here because the sheet is already scoped to the wide root
 * and a second injector would be a second thing to clear; both are stated in
 * full at `WIDE_PANEL_CSS`.
 */

import { useEffect, type RefObject } from "react";
import { quickAccessMenuClasses } from "./deckyUiInternals";
import { useQuickAccessVisible } from "./quickAccessVisible";

/** Marker class on a wide page's root, matched by the injected `:has()` rule. */
export const WIDE_ROOT_CLASS = "romm-wide-qam-root";

const WIDE_PANEL_STYLE_ID = "romm-wide-qam-styles";

// Steam gives each tab's content panel an id built from that tab's own key:
// measured on the device under Decky, whose key is `QuickAccessTab.Decky = 999`
// and whose panel is `#quickaccess_content_999`, with the `TabGroupPanel` class
// on that same element — which is why walking the DOM by id lands where the CSS
// below matches by class. **The prefix is the whole of what is matched**, and
// deliberately so: the key belongs to whichever entry rendered the page — Decky's
// there, Tender's own (`qam/installEntry.tsx`) behind its entry — so a
// selector naming one would stop matching the moment the other rendered it. What
// id a string key produces has not been measured, and nothing here needs it to
// be.
const PANEL_ID_SELECTOR = '[id^="quickaccess_content_"]';

const TAB_PANEL_SELECTOR = quickAccessMenuClasses?.TabGroupPanel
  ? `.${quickAccessMenuClasses.TabGroupPanel}`
  : PANEL_ID_SELECTOR;

// Steam caps every tab's content panel at 300 px and lifts it only for its own
// Friends panel (ADR-0029). The cap is on the panel element itself: with these
// rules up, the device measured the panel at 806 px — under Decky, where the id
// was `#quickaccess_content_999`; the cap and the lift are properties of the
// panel element and not of whose key named it. The `> *` line covers a child
// carrying a cap of its own, which was never separately measured and costs one
// selector to keep. `:has()` scopes the lift to a panel holding a wide page of
// ours.
//
// The second rule is about focus rather than width, and rides along because it
// needs the same sheet in the same document. A DISABLED button is still a focus
// stop — the device pass walked one with the stick — but Steam's own disabled
// treatment (`opacity: .4` over `rgba(61,67,77,.35)`, `library.css`) leaves
// almost nothing for a focus fill to change, so the reader loses their place on
// a row of buttons where one is disabled. Steam answers this for its own
// variants with `background: #000` on `.DialogButton[disabled].gpfocus`, but
// those rules are scoped to hashed class names ours does not carry. The outline
// is Steam's other focus form, taken verbatim from the one it uses where a fill
// will not read (`outline: outset #fff 2px`, `chunk~2dcc5aaf7.css`), and it is
// scoped to a wide page of ours.
//
// **The third rule overrides Steam's own styling, deliberately. It is not a
// defect fix, and Steam's rule is not wrong** — it is simply applied to a box
// that is ours. Steam's tabbed page gives its content scroller a
// `padding-bottom: 40px` (rule `._1X4dtbZ_AMX_DXT-SGiK01`, read off the live
// stylesheet in the QAM). That scroller sits INSIDE the body `WidePage` measured
// and handed the tab as its height, so on a tabbed page 40 px of a height the
// frame sized to the panel go to a reserve nothing of ours asked for — and the
// reader meets a band under Library that Settings and Sync do not have, those
// being untabbed and rendering no such scroller at all. Measured at the dev
// window's metrics: our body ran to y=752 inside a panel box ending at 764.3
// while the tab's content stopped at 712, and the tab's own scrolling region
// went from a `clientHeight` of 550 to 590 with this rule alone. What overriding
// it is worth is that difference: 40 px of content back on every tabbed wide
// page. The 602 the page reaches today is this rule and the frame's own gap
// going together — two changes, and only one of them is this one.
//
// It is ours to override because the box is ours: the scroller is rendered
// inside a page of ours at a height of our own measuring, and that measurement
// already stops the page at the panel's edge. Nothing outside a wide page is
// touched — the selector is scoped to our root, which is also what wins it: two
// classes against Steam's one, in a sheet appended after Steam's.
//
// **Written against the readable class, not the hashed one.** Steam ships both
// on that element (`_TabContentsScroll` beside `_1X4dtbZ_AMX_DXT-SGiK01`), and
// the hashed name is a build artefact that changes with Steam's bundle. Either
// could go, and the degradation is what makes reaching for someone else's class
// safe here: a selector that stops matching leaves Steam's padding standing,
// which is today's behaviour — the band comes back on tabbed pages and nothing
// else changes. Taking a padding away can only give room back, so there is no
// reading of a missed match that clips or overlaps content.
const WIDE_PANEL_CSS = `
${TAB_PANEL_SELECTOR}:has(.${WIDE_ROOT_CLASS}) { max-width: none; }
${TAB_PANEL_SELECTOR}:has(.${WIDE_ROOT_CLASS}) > * { max-width: none; }
.${WIDE_ROOT_CLASS} button.DialogButton[disabled].gpfocus,
.${WIDE_ROOT_CLASS} button.DialogButton.Disabled.gpfocus { outline: outset #fff 2px; }
.${WIDE_ROOT_CLASS} ._TabContentsScroll { padding-bottom: 0; }
`;

// The stylesheet a wide page has up, held by reference rather than looked up by
// id: it lives in the page's own document, and plugin code runs in a different
// one, so the ambient `document` cannot reach it.
//
// One reference for the module, which holds only while at most one wide page is
// mounted — the panel's router replaces the mounted page, it never stacks two.
// Break that and the reference stops belonging to anyone: the second page's
// mount is a no-op on an expansion it did not take, and its unmount collapses
// the panel under the first — stylesheet dropped, hide message posted, and the
// page still on screen has no path back to wide short of a QAM tab switch.
let injectedStyle: HTMLStyleElement | null = null;

/**
 * Drive Steam's Friends-tab expansion. The FriendsUI store listens for `message`
 * events on the window plugin code runs in and flips one MobX observable, which
 * carries the `Expanded` class that un-shifts the QAM's placeholder.
 *
 * The target origin is `window.origin`: it addresses the message to the one
 * window meant to receive it, and always matches, so the message is always
 * delivered. Neither alternative is wanted — a literal that ever stops matching
 * is checked at delivery and discarded in silence, leaving a panel that simply
 * never widens, and `"*"` is delivered to any document in the window.
 */
export function setQamExpanded(expanded: boolean): void {
  window.postMessage({ message: expanded ? "QamFriendsExpanded" : "QamFriendsHidden" }, window.origin);
}

/**
 * Give the panel's width back, if this plugin ever took it. A no-op otherwise:
 * the flag is Steam's own and global, so posting the hide message unasked would
 * retract a Friends panel the user opened.
 */
function collapseWidePanel(): void {
  const style = injectedStyle;
  if (!style) return;
  injectedStyle = null;
  style.remove();
  setQamExpanded(false);
}

function expandWidePanel(root: HTMLElement): void {
  if (injectedStyle) return;
  setQamExpanded(true);
  const doc = root.ownerDocument;
  const style = doc.createElement("style");
  style.id = WIDE_PANEL_STYLE_ID;
  style.textContent = WIDE_PANEL_CSS;
  doc.head.appendChild(style);
  injectedStyle = style;
}

/**
 * Collapse the panel from outside React's own cleanup.
 *
 * Written for Decky's `onDismount`, which has no caller behind Tender's own
 * Quick Access entry. Kept because the flag it clears is Steam's and global,
 * so a lever that reaches it without a mounted component is worth having.
 */
export function collapseQamOnDismount(): void {
  collapseWidePanel();
}

/**
 * Hold the panel wide for as long as the page owning `rootRef` is mounted, the
 * tab this page sits in is the active QAM tab, and the QAM is not known to be
 * closed. Losing any of the three posts the hide message and drops the
 * stylesheet; regaining it re-expands.
 *
 * **Which tab that is, is never asked.** The question is answered by walking up
 * from the page's own root to the panel around it and reading Steam's
 * active-tab class off that panel's parent — so it is true of Tender's own
 * entry and of Decky's, and of anything else that might one day render this
 * panel, without naming any of them.
 *
 * It is answered from the DOM inside the effect rather than from React state
 * because a panel can be rendered while its tab is not the active one, so a page
 * mounting there would expand on its first pass and retract on the next — a
 * visible flash of Steam's own panel.
 */
export function useWideQamPanel(rootRef: RefObject<HTMLElement | null>): void {
  const qamVisible = useQuickAccessVisible();

  useEffect(() => {
    const root = rootRef.current;
    if (!qamVisible || !root) return;

    const activeTabClass = quickAccessMenuClasses?.ActiveTab;
    const panelParent = root.closest(PANEL_ID_SELECTOR)?.parentElement;

    // True while the question cannot be asked — no panel around us, or a probe
    // that came back undefined so there is no class name to look for. The other
    // default would make every wide page permanently narrow; this one costs a
    // leaked expansion the QAM-close and unmount paths still clear.
    const owningTabActive = () => !activeTabClass || !panelParent || panelParent.classList.contains(activeTabClass);

    const syncPanelWidth = () => (owningTabActive() ? expandWidePanel(root) : collapseWidePanel());
    syncPanelWidth();

    // Switching QAM tabs changes the parent's class and unmounts nothing, so the
    // observer is the only thing that sees it.
    let observer: MutationObserver | null = null;
    const panelView = panelParent?.ownerDocument.defaultView;
    if (activeTabClass && panelParent && panelView) {
      // The view's own constructor, not this module's. Whether a cross-realm
      // observer would deliver here is not established either way — unlike an
      // `instanceof`, which is measurably false — and taking it from the node
      // costs a property read, so the question does not need answering.
      observer = new panelView.MutationObserver(syncPanelWidth);
      observer.observe(panelParent, { attributes: true, attributeFilter: ["class"] });
    }

    return () => {
      observer?.disconnect();
      collapseWidePanel();
    };
  }, [qamVisible, rootRef]);
}
