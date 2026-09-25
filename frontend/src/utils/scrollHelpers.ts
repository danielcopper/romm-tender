/**
 * Gamepad scroll helpers for injected game detail content.
 *
 * Steam's gamepad focus engine scrolls to focused elements automatically,
 * but its built-in handler doesn't center them reliably. These helpers
 * use a 50ms delayed scrollTo to override Steam's handler, ensuring
 * focused elements are centered (or scrolled to top) in the viewport.
 *
 * Only DialogButton works as a focusable element in this injection context —
 * Focusable wrappers around non-interactive content don't register with
 * Steam's gamepad engine when injected into the game page's own React tree.
 */

/**
 * How far `el` sits below the start of `scroller`'s content, in layout terms.
 *
 * The two rects are viewport-relative and the scroller's own `scrollTop` moves
 * only one of them: scrolling it slides `el` up by exactly that much and leaves
 * the scroller's own box where it is, so adding `scrollTop` back cancels it
 * exactly. Scrolling anything ABOVE the scroller moves both boxes together and
 * cancels in the subtraction. `clientTop` is the scroller's top border, which
 * its rect includes and `clientHeight` does not.
 *
 * `el.offsetTop` would be shorter and is not taken: `offsetTop` is measured
 * against `offsetParent`, which is the scroller only while the scroller stays
 * positioned — Steam's QAM panel computes `position: relative` today, and that
 * is Steam's CSS to change, not ours. Nothing above reads any element's
 * position.
 */
export function offsetWithinScroller(el: HTMLElement, scroller: HTMLElement): number {
  const elTop = el.getBoundingClientRect().top;
  const scrollerTop = scroller.getBoundingClientRect().top;
  return elTop - scrollerTop - scroller.clientTop + scroller.scrollTop;
}

/** Find the nearest ancestor that is actually scrollable (overflow:scroll|auto
 *  AND scrollHeight > clientHeight). */
export function findScrollParent(el: HTMLElement): HTMLElement | null {
  let parent: HTMLElement | null = el.parentElement;
  while (parent) {
    const ov = globalThis.getComputedStyle(parent).overflowY;
    if ((ov === "scroll" || ov === "auto") && parent.scrollHeight > parent.clientHeight) return parent;
    parent = parent.parentElement;
  }
  return null;
}

/** Find the outermost ancestor that is actually scrollable (overflow:scroll|auto
 *  AND scrollHeight > clientHeight). */
export function findOutermostScrollParent(el: HTMLElement): HTMLElement | null {
  let parent: HTMLElement | null = el.parentElement;
  let outermost: HTMLElement | null = null;
  while (parent) {
    const ov = globalThis.getComputedStyle(parent).overflowY;
    if ((ov === "scroll" || ov === "auto") && parent.scrollHeight > parent.clientHeight) outermost = parent;
    parent = parent.parentElement;
  }
  return outermost;
}

/** Minimal focus-event shape both DOM `FocusEvent` and React's `FocusEvent`
 *  satisfy — these helpers only ever read `currentTarget`. */
type FocusLike = { currentTarget: EventTarget | null };

/**
 * onFocus handler that scrolls the focused element to the center of the
 * scroll container. Use on DialogButton elements for gamepad navigation.
 */
export function scrollFocusedToCenter(e: FocusLike): void {
  const el = e.currentTarget as HTMLElement | null;
  setTimeout(() => {
    if (!el) return;
    const scrollParent = findScrollParent(el);
    if (scrollParent) {
      const elRect = el.getBoundingClientRect();
      const spRect = scrollParent.getBoundingClientRect();
      const targetScroll = scrollParent.scrollTop + (elRect.top - spRect.top) - spRect.height / 2 + elRect.height / 2;
      scrollParent.scrollTo({ top: targetScroll, behavior: "smooth" });
    }
  }, 50);
}

/**
 * onFocus handler that scrolls the NEAREST scroll container to its very top.
 *
 * For modal content, where the text the user has to read sits ABOVE the first
 * focusable element: Steam's focus engine only scrolls far enough to reveal the
 * focused element itself, so an intro above the first control stays permanently
 * off-screen on a controller. Put this on a wrapper around the topmost
 * selectable — React's onFocus is delivered via focusin, so it fires for a
 * focus landing on any descendant.
 *
 * Nearest, not outermost (`scrollToTop`): a modal's own scroll container is the
 * one that has to move, never the page scrolled behind it.
 */
export function scrollNearestToTop(e: FocusLike): void {
  const el = e.currentTarget as HTMLElement | null;
  setTimeout(() => {
    if (!el) return;
    findScrollParent(el)?.scrollTo({ top: 0, behavior: "smooth" });
  }, 50);
}

/**
 * onFocus handler that scrolls the NEAREST scroll container to its very end —
 * `scrollNearestToTop`'s twin, for text that sits BELOW the last focusable
 * element, where Steam's focus engine stops just as short. Put it on a wrapper
 * around the bottommost selectable.
 */
export function scrollNearestToBottom(e: FocusLike): void {
  const el = e.currentTarget as HTMLElement | null;
  setTimeout(() => {
    if (!el) return;
    const scroller = findScrollParent(el);
    scroller?.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  }, 50);
}

/**
 * onFocus handler that scrolls to the top of the scroll container.
 * Use on the Play button so navigating back up reveals the banner/hero.
 */
export function scrollToTop(e: FocusLike): void {
  const el = e.currentTarget as HTMLElement | null;
  setTimeout(() => {
    if (!el) return;
    // Use the outermost scroll parent so the banner/hero scrolls into view,
    // not just the nearest inner container.
    const scrollParent = findOutermostScrollParent(el);
    if (scrollParent) {
      scrollParent.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, 50);
}
