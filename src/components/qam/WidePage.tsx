/**
 * The shell every wide QAM page renders inside: the marker class that lets the
 * injected rule lift Steam's 300 px tab-panel cap, one header line carrying Back
 * and the title, an optional L1/R1 tab bar, and a body of a definite, measured
 * height.
 *
 * Back on the gamepad is **B**, bound once by the panel's router for every
 * sub-page (`src/index.tsx`) rather than here — so the narrow pages get it too,
 * and Main, which has nowhere to go back to, keeps Decky's own B. What the frame
 * has to do is get out of the way: Steam's tabbed page binds the content pane's
 * `onCancelButton` to "focus the tab row" unless `cancelSkipTabHeader` is
 * passed, so the first B inside a tab would be swallowed there. The chip stays
 * as the discoverable half and as the mouse path.
 *
 * The page's own content is `children`, or — when the page has tabs — the
 * `content` of each tab, which Steam's `Tabs` renders itself. Either way the
 * frame opens the page with focus inside that body rather than on the Back row.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`.
 */

import { useEffect, useLayoutEffect, useRef, useState, type FC, type ReactNode } from "react";
import { DialogButton, Focusable } from "@decky/ui";
import { ControllerGlyph, GLYPH_BUTTON_B, Tabs } from "../../utils/deckyUiInternals";
import { ENTRY_FOCUS_DELAY_MS, pageEntryStop, placeEntryFocus } from "../../utils/entryFocus";
import { WIDE_ROOT_CLASS, useWideQamPanel } from "../../utils/qamExpansion";
import { offsetWithinScroller } from "../../utils/scrollHelpers";
import { ScrollRegion } from "./ScrollRegion";

export interface WidePageTab {
  id: string;
  title: string;
  content: ReactNode;
}

/**
 * A page has all three tab props or none of them, and `children` belongs to the
 * second case alone: a tabbed page's body is the active tab's `content`, so
 * children passed beside `tabs` would render nowhere.
 *
 * `ownRegions` belongs to that second case too, for the same reason the frame
 * gives a tabbed body no region: it answers whose job the scrolling is, and a
 * tabbed page has already handed that to Steam's tabbed page.
 */
type TabProps =
  | {
      tabs: WidePageTab[];
      activeTab: string;
      onShowTab: (tabId: string) => void;
      children?: undefined;
      ownRegions?: undefined;
    }
  | {
      tabs?: undefined;
      activeTab?: undefined;
      onShowTab?: undefined;
      children?: ReactNode;
      /**
       * The page builds its own scrolling regions, so the frame wraps the body
       * in none. For a page whose content is side-by-side regions — a list and
       * a detail, a table beside a controls column — where a region from the
       * frame would nest a scroller around both of them.
       */
      ownRegions?: boolean;
    };

export type WidePageProps = {
  title: string;
  onBack: () => void;
} & TabProps;

/**
 * Marks a page that places its own entry focus, which the panel's router reads
 * to keep its first-button focus away. Every wide page sets it, because the
 * frame places that focus: `autoFocusContents` where Steam's tabbed page is
 * there to consume it, and this frame's own timer everywhere else. Either way
 * focus lands inside the body, never on the Back row above it.
 */
export const OWNS_ENTRY_FOCUS_ATTR = "data-romm-owns-entry-focus";

// Floor under the measured body, so a viewport read taken before Steam has laid
// the panel out cannot collapse the page to nothing.
const MIN_BODY_HEIGHT = 240;

/**
 * The two halves of one measurement: how tall the body may be, and how far the
 * root has to be pulled up for that height to fit.
 *
 * **They are a pair because each half alone is useless, and measurably so.**
 * `height` counts the space the frame's own ancestors hang into, so paying it
 * out without the pull-up overflows the scroller by exactly `overhang` and the
 * panel scrolls — which takes the Back row off the top. The pull-up without the
 * height moves nothing at all: the page still ends on the same line and the
 * band under it is unchanged, because the room the ancestors stop claiming goes
 * to nobody. So they travel as one value and are applied in one render.
 *
 * Both are taken at runtime and re-taken together on every `measure()`, so a
 * different display, UI scale or panel size changes them and nothing here.
 */
interface BodyFit {
  height: number;
  /**
   * What the root's negative bottom margin cancels — the measured overhang,
   * never a constant, for the reason `ancestorOverhang` states. A margin
   * changes what the box claims after itself, not where it paints, so the
   * ancestors stop where the scroller does and nothing on the page moves.
   *
   * Zero is the safe reading and needs no special case: the margin is then
   * `0px`, the height is what it always was, and the page behaves exactly as it
   * did before this pair existed. So a chain this frame has never seen cannot
   * come out worse than the version that gave the overhang away.
   */
  overhang: number;
}

/**
 * What the Back chip reads: the button's own glyph and the word.
 *
 * The glyph is Steam's, drawn for whatever controller is connected, so the chip
 * names the same button the footer legend does rather than a letter this plugin
 * picked. Where the probe misses, the chevron the chip carried before it takes
 * over — a chip that says Back and no glyph is still true, and a hand-drawn B
 * would be wrong for a PlayStation pad.
 */
const BackChipLabel: FC = () =>
  ControllerGlyph ? (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}>
      <ControllerGlyph button={GLYPH_BUTTON_B} bKnockout style={{ width: "13px", height: "13px" }} />
      Back
    </span>
  ) : (
    <>‹ Back</>
  );

/**
 * The nearest ancestor that scrolls `body`, or `null` when nothing does.
 *
 * The QAM's own tab panel is one (`#quickaccess_content_999` computes
 * `overflow-y: auto`), and it is the element whose bottom edge bounds the page.
 */
function scrollingAncestor(body: HTMLElement, view: Window): HTMLElement | null {
  for (let el = body.parentElement; el; el = el.parentElement) {
    const overflowY = view.getComputedStyle(el).overflowY;
    if (overflowY === "auto" || overflowY === "scroll") return el;
  }
  return null;
}

/**
 * How far `body`'s own ancestors hang below their parents, up to `scroller`.
 *
 * Decky wraps a plugin's content in a box that overhangs: it sits below the
 * panel top by the height of Decky's own plugin title and takes `height: 100%`
 * of a parent it is already inset within, so its bottom lands that far past
 * that parent's. Nothing of ours is painted in those pixels — but the panel
 * scrolls by them, and that is enough to take the Back row off the top.
 *
 * **This is what the root's negative bottom margin cancels, not what the body
 * gives up.** Subtracting it from the height instead is what left a band of the
 * panel empty across every wide page: the wrapper then ended just short of the
 * scroller's box and our own content an overhang above that, with content that
 * would have fitted clipped out of the difference. Cancelling it costs the page nothing,
 * because the pixels were never ours to paint in.
 *
 * **Measured, never a constant.** The overhang comes from someone else's markup,
 * and a constant would pin every wide page to today's value of it. That it has
 * so far read the same 50 px in every environment tried — it is Decky's inset
 * and padding in CSS pixels, so it does not scale with the viewport, and a
 * 1.5-scale panel three fifths the height (440 against 750) reports it
 * unchanged — is evidence for the
 * arithmetic, **not a value to hardcode**: the smaller the panel, the larger the
 * same 50 px looms in it.
 *
 * **What the arithmetic assumes is a SHAPE, not a value.** Every value here is
 * re-measured, so another display, scale or Decky release that merely overhangs
 * by a different amount is already handled. What is assumed is that the boxes
 * between the body and the scroller FOLLOW our content — the wrapper's bottom
 * tracks ours plus its own inset, at every body height — so pulling the root's
 * margin box up by the overhang moves those bottoms up with it. It holds
 * because the overhang is the wrapper's own padding rather than anything
 * derived from what we put inside, and it was checked at several body heights
 * in two panel geometries.
 *
 * **If that ever stops holding, this is what it looks like.** A wrapper pinned
 * to a height of its own — a future Decky or Steam nesting the plugin
 * differently — would not follow the body up: growing the body would then
 * overflow the wrapper instead of the wrapper's parent, the negative margin
 * would cancel nothing that was in the way, and the panel would scroll again,
 * which the reader meets as the Back row leaving the top. The check is one
 * reading: the lowest ancestor bottom in this chain should equal the body's own
 * once the margin is applied, and it should sit an overhang below it without.
 */
function ancestorOverhang(body: HTMLElement, scroller: HTMLElement): number {
  let total = 0;
  for (let el: HTMLElement | null = body; el && el !== scroller; el = el.parentElement) {
    const parent = el.parentElement;
    if (!parent || parent === scroller) break;
    total += Math.max(0, el.getBoundingClientRect().bottom - parent.getBoundingClientRect().bottom);
  }
  return total;
}

/**
 * The space left below `body` inside whatever scrolls it — the height a wide
 * page gets to work with, because Steam's tabbed page fills its parent rather
 * than growing and nothing in the QAM chain hands the plugin's panel a height —
 * paired with the pull-up that makes room for it.
 *
 * The height is the whole of the scroller below the body's own top, and no
 * allowance is taken off it. The ancestors' overhang is not taken out of it
 * either: it rides along as the second half of the pair, and the caller spends
 * it as a negative bottom margin on the root. That is the difference between a
 * page ending an overhang above the panel's box and one ending on it, with the
 * scroller unscrollable either way — the body carries `overflow: hidden`, so
 * landing its bottom exactly on the scroller's cannot make the panel scroll.
 *
 * **Holding a few pixels back for breathing room is the obvious thing to do
 * here, and it is wrong.** A constant subtracted at this point is not breathing
 * room a reader perceives as such: it is a band of dead panel under the page,
 * and the page has no way to end anywhere else. It also reads unevenly — a
 * tabbed page keeps a bottom padding of Steam's own inside our body on top of
 * it, which `qamExpansion` cancels — so the same constant showed as two
 * different gaps depending on the page. Steam does not do it either: a QAM
 * panel of its own, measured in this document, runs its content to its box with
 * a gap of 0. Room under a page belongs to that page's own layout, where it can
 * be seen and adjusted, not to a number the frame takes off every page's height.
 *
 * **Three runtime readings and one constant, and the readings are re-taken
 * whenever the panel or the document resizes** (the layout effect's observers
 * below). The readings are the scroller's `clientHeight`, the body's offset
 * within its content, and the ancestors' overhang; the constant is the floor.
 * Nothing here is a remembered pixel count, which is why a different display,
 * UI scale or panel geometry needs no case of its own.
 *
 * The floor is the one place a reading is overruled, and it is unchanged: a
 * measurement taken before Steam has laid the panel out can come back tiny or
 * negative, and `MIN_BODY_HEIGHT` keeps the page from collapsing until the
 * observers re-measure. The pull-up is not floored with it and does not need to
 * be — a margin that cancels an overhang can only ever reduce what the chain
 * claims, so it cannot turn a floored measurement into an overflow.
 *
 * **The quantity has to be free of the scroller's own offset, and only a
 * layout-relative one is.** Every viewport-relative form grows as the panel
 * scrolls, which makes the measurement feed itself: a body measured part-way
 * down comes out that much too tall, the panel then has that much more to
 * scroll, and nothing re-measures. It can be measured part-way down because
 * `QAMPanel` resets the panel's scroll inside a `requestAnimationFrame`, a
 * frame after this page's layout effect has already run.
 *
 * Measured live in the QAM over the mounted page, at panel offsets 0 / 200 /
 * 500 / 634 px: `innerHeight - top` answers 648 / 848 / 1148 / 1283, and so
 * does the scroller's own `bottom - top` — the panel's rect bottom is 764.3
 * against an `innerHeight` of 764, so bounding to the panel rather than the
 * window changes no number at any offset. This form answers 648 at all four.
 *
 * Both branches assume the scroller is an element other than the viewport's own.
 * The document element would break the first — it IS the viewport's scroller, so
 * its rect moves under its own scroll and the difference is already offset-free,
 * making `+ scrollTop` a double count. And the `null` branch is reached when no
 * ancestor computes `overflow-y: auto|scroll`, which is not the same as nothing
 * scrolling: a document scrolling at the viewport level is exactly that case,
 * and there the fallback is the self-amplifying form again. Neither is reachable
 * from the QAM, where the panel is an ordinary element and Steam's own document
 * does not scroll.
 */
function measureBodyFit(body: HTMLElement, view: Window): BodyFit {
  const scroller = scrollingAncestor(body, view);
  const remaining = scroller
    ? scroller.clientHeight - offsetWithinScroller(body, scroller)
    : view.innerHeight - body.getBoundingClientRect().top;
  return {
    height: Math.max(MIN_BODY_HEIGHT, remaining),
    // Nothing to cancel where the walk has no scroller to stop at — the
    // fallback's height is viewport-relative and counts no ancestor box.
    overhang: scroller ? ancestorOverhang(body, scroller) : 0,
  };
}

export const WidePage: FC<WidePageProps> = ({ title, onBack, tabs, activeTab, onShowTab, children, ownRegions }) => {
  const rootRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState<BodyFit | null>(null);

  useWideQamPanel(rootRef);

  useLayoutEffect(() => {
    const body = bodyRef.current;
    const view = body?.ownerDocument.defaultView;
    if (!body || !view) return;

    // A re-measure that answers the same pair keeps the previous object, the
    // bail-out an equal number used to get for free: the observers below fire
    // on layout, and a fresh object every time would re-render on each one.
    //
    // The layout is read HERE and only compared inside the updater. React
    // requires an updater to be pure and calls it when it likes — StrictMode
    // twice, a discarded concurrent render speculatively — so a rect read in
    // there would be taken during render, at a moment nothing here chose.
    const measure = () => {
      const next = measureBodyFit(body, view);
      setFit((previous) => (previous?.height === next.height && previous.overhang === next.overhang ? previous : next));
    };
    measure();
    // The view's own constructor, not the module's: plugin code runs in the
    // SharedJSContext window and these nodes are the QAM's.
    const observer = new view.ResizeObserver(measure);
    observer.observe(body.ownerDocument.documentElement);
    // And the panel itself, which is what actually changes size here.
    //
    // **The first measurement provably runs before the panel is widened**, and
    // that follows from the effect kinds rather than from timing: this is a
    // LAYOUT effect, while `useWideQamPanel` widens the panel from a passive
    // one, and React runs every layout effect of a commit before any passive
    // effect of it. So on every mount the page is measured against the panel
    // Steam has not expanded yet. The device figure is the other half — a body
    // of 1245 px where the settled geometry answers 694 implies a mount-time
    // `clientHeight` near 1257, not 750 — so the panel's box really does differ
    // between mount and settle, and nothing else would have corrected it: the
    // document element never resizes, because the viewport does not change.
    //
    // Only the TRIGGER is pinned to the mount-time scroller; the answer
    // re-resolves it on every `measure()`.
    const scroller = scrollingAncestor(body, view);
    if (scroller) observer.observe(scroller);
    return () => observer.disconnect();
  }, []);

  // `autoFocusContents` lands entry focus in the active tab's content, so where
  // Steam's tabbed page renders there is nothing for the frame to do. It is also
  // what makes Steam draw the L1/R1 glyphs: its tab row shows them only while
  // gamepad focus is within the tabbed page, and the Back row above the tabs is
  // outside it (`chunk~2dcc5aaf7.js`, the tab row's `showGlyphs`).
  const steamPlacesFocus = Boolean(tabs && Tabs);

  useEffect(() => {
    const body = bodyRef.current;
    if (steamPlacesFocus || !body) return;
    // The same delay the panel's router uses for the pages it still covers:
    // Steam's navigation resolves its retained focus pointer after the mount,
    // and a focus placed before that is taken back.
    //
    // And the same rule as the router's: the area the body declared, or its
    // first stop where it declared none. A page opened on something other than
    // its first row has to be able to say so, because on a list-and-detail page
    // focus is what selects — landing on the first row would select it and
    // discard the section the reader was sent to.
    const timer = setTimeout(() => placeEntryFocus(body, pageEntryStop), ENTRY_FOCUS_DELAY_MS);
    return () => clearTimeout(timer);
  }, [steamPlacesFocus]);

  // A `min-height` is not enough here — Steam's tabbed page fills its parent and
  // clips instead of growing, so the body needs a definite height. Its pull-up
  // is read off the same value in the same render, which is the whole guard
  // against the two drifting apart: there is no second thing to forget to set.
  const bodyStyle = { height: fit === null ? undefined : `${fit.height}px`, overflow: "hidden" };
  const rootStyle = fit === null ? undefined : { marginBottom: `${-fit.overhang}px` };

  // The frame's region is for a body of rows in one column, and nothing else
  // gets one. Steam's tabbed page already wraps each tab's content in this same
  // plain scroll panel (`ScrollingTab<id>`, `scrollDirection: "y"`, in
  // `chunk~2dcc5aaf7.js`), so a region from the frame would nest a second
  // scroller inside it; and a page whose content is side-by-side regions —
  // `ownRegions` — would get one wrapped around both of them. Either way any
  // region the body needs is the page's to build with `ScrollRegion`.
  let body: ReactNode = ownRegions ? children : <ScrollRegion>{children}</ScrollRegion>;
  if (tabs) {
    body = Tabs ? (
      // `cancelSkipTabHeader` leaves the content pane's cancel handler unset, so
      // B reaches the router's binding instead of being spent moving focus to
      // the tab row.
      <Tabs tabs={tabs} activeTab={activeTab} onShowTab={onShowTab} autoFocusContents cancelSkipTabHeader />
    ) : (
      tabs.find((tab) => tab.id === activeTab)?.content
    );
  }

  return (
    <div className={WIDE_ROOT_CLASS} ref={rootRef} style={rootStyle} {...{ [OWNS_ENTRY_FOCUS_ATTR]: "" }}>
      {/* One line, not three: the full-width Back row and the title on its own
          line cost two of the four rows the Deck's body has to spend. */}
      <Focusable style={{ display: "flex", alignItems: "center", gap: "10px", padding: "4px 16px 6px" }}>
        <DialogButton
          style={{ flex: "0 0 auto", minWidth: 0, width: "auto", padding: "4px 10px", fontSize: "13px" }}
          onClick={onBack}
        >
          <BackChipLabel />
        </DialogButton>
        <span style={{ fontSize: "16px", fontWeight: 600, color: "#dcdedf" }}>{title}</span>
      </Focusable>
      <div ref={bodyRef} style={bodyStyle} data-testid="wide-page-body">
        {body}
      </div>
    </div>
  );
};
