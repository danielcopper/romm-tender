/**
 * The `ScrollRegion` an element sits in, found by the wheel containment every
 * region carries on both of its branches: nothing else under `frontend/src`
 * sets it, and it lands on whichever element Steam's scroll-panel probe left
 * the region on.
 */

export function regionAround(el: Element): HTMLElement {
  for (let node = el.parentElement; node !== null; node = node.parentElement) {
    if (node.style.overscrollBehavior === "contain") return node;
  }
  throw new Error("the element sits in no ScrollRegion");
}
