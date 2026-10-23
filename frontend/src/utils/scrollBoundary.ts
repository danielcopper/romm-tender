/**
 * The class of an element `findScrollParent` (`scrollHelpers.ts`) does not
 * walk past, so the helpers that scroll the nearest scroller leave everything
 * around it alone. The game page's tabs carry it: `docs/architecture/qam-panel.md`,
 * "The game page's tabs".
 */
export const SCROLL_BOUNDARY_CLASS = "romm-scroll-boundary";
