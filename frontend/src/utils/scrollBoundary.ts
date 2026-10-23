/**
 * The class of an element no focus helper of `scrollHelpers.ts` scrolls past:
 * what it holds scrolls inside it or not at all. The game page's tabs carry it,
 * because a pane whose rows all fit has no scroller of its own that overflows,
 * and the walk would otherwise reach Steam's game page around it and move the
 * page under the tab row.
 */
export const SCROLL_BOUNDARY_CLASS = "romm-scroll-boundary";
