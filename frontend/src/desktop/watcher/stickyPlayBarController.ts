/**
 * The sticky play bar controller: pins Steam's play bar, keeps the hero wrapper
 * at `overflow: visible` and bounds the artwork's overflow
 * (docs/architecture/desktop-dom-architecture.md, "The hero's parallax").
 */

import { logWarn } from "../../api/backend";
import {
  GLASS_PLAY_BAR_BG,
  GLASS_PLAY_BAR_GRADIENT,
  PINNED_PLAY_BAR_SHADOW,
  SOLID_PLAY_BAR_BG,
} from "../gameview/styles";
import {
  findHeroMirrorCanvas,
  findHeroParallaxLayer,
  findHeroWrapperFallback,
  isTenderElement,
} from "./elementSelectors";
import type { DomRestorationLedger } from "./restorationLedger";

/** How far, in the parallax layer's own pixels, the artwork fades out before the clip edge. */
const HERO_FADE_PX = 160;

/** The vertical scale (m22) of a `matrix3d(...)` or `matrix(...)` transform, or null for anything else. */
function verticalScale(transform: string): number | null {
  const match = /^matrix(3d)?\(([^)]+)\)$/.exec(transform);
  if (!match) return null;
  const values = match[2]!.split(",").map(Number);
  const m22 = match[1] ? values[5] : values[3];
  return m22 !== undefined && Number.isFinite(m22) && m22 !== 0 ? m22 : null;
}

function setIfChanged(ledger: DomRestorationLedger, el: HTMLElement, property: string, value: string): void {
  if (el.style.getPropertyValue(property) !== value) ledger.style(el, property, value);
}

/**
 * Clip the parallax layer with an `overflow-clip-margin` so its scrollable
 * bottom, `layerTop + scale × (height + margin)`, lands on the content's bottom,
 * or on the scroller's height when the page is shorter; then fade the mirrored
 * canvas over its last {@link HERO_FADE_PX} pixels before that edge. Why:
 * docs/architecture/desktop-dom-architecture.md, "Bounding the artwork".
 */
export function boundHeroOverflow(
  heroWrapper: HTMLElement,
  scroller: HTMLElement,
  content: HTMLElement,
  ledger: DomRestorationLedger,
): void {
  const layer = findHeroParallaxLayer(heroWrapper);
  const win = layer?.ownerDocument.defaultView;
  if (!layer || !win) return;
  const scale = verticalScale(win.getComputedStyle(layer).transform);
  if (scale === null || scale < 0) return;

  const scrollerTop = scroller.getBoundingClientRect().top - scroller.scrollTop;
  const offsetParent = layer.offsetParent as HTMLElement | null;
  let layerTop: number;
  if (offsetParent === scroller) layerTop = layer.offsetTop;
  else if (offsetParent && scroller.contains(offsetParent))
    layerTop = offsetParent.getBoundingClientRect().top - scrollerTop + layer.offsetTop;
  else return;

  const contentBottom = content.getBoundingClientRect().bottom - scrollerTop;
  const target = Math.max(contentBottom, scroller.clientHeight);
  const margin = Math.max(0, Math.floor((target - layerTop) / scale - layer.offsetHeight));
  setIfChanged(ledger, layer, "overflow", "clip");
  setIfChanged(ledger, layer, "overflow-clip-margin", `${margin}px`);

  const mirror = findHeroMirrorCanvas(layer);
  if (mirror?.offsetParent !== layer) return;
  const visible = layer.offsetHeight + margin - mirror.offsetTop;
  if (visible <= 0) return;
  // The canvas is flipped, so its visual top is its own bottom edge.
  const flipped = (verticalScale(win.getComputedStyle(mirror).transform) ?? 1) < 0;
  const direction = flipped ? "to top" : "to bottom";
  const fadeFrom = Math.max(0, visible - HERO_FADE_PX);
  setIfChanged(
    ledger,
    mirror,
    "mask-image",
    `linear-gradient(${direction}, black ${fadeFrom}px, transparent ${visible}px)`,
  );
}

/**
 * Locate the scrollable container for an element (e.g. Steam's game overview scroller).
 * Returns the scrollable element if found, or the default window.
 */
export function findScrollContainer(el: HTMLElement): HTMLElement | Window {
  const win = el.ownerDocument.defaultView ?? window;
  let curr = el.parentElement;
  while (curr && curr !== el.ownerDocument.body && curr !== el.ownerDocument.documentElement) {
    const style = win.getComputedStyle(curr);
    const overflowY = style.overflowY || style.overflow;
    if (overflowY === "auto" || overflowY === "scroll") {
      return curr;
    }
    curr = curr.parentElement;
  }
  return win;
}

/**
 * Determine if the play bar has reached the top of its scroll container and is currently pinned.
 */
export function isPlayBarPinned(playBarTop: HTMLElement, scroller: HTMLElement | Window): boolean {
  const rect = playBarTop.getBoundingClientRect();
  if ("getBoundingClientRect" in scroller && typeof scroller.getBoundingClientRect === "function") {
    const scrollerRect = (scroller as HTMLElement).getBoundingClientRect();
    return rect.top <= scrollerRect.top + 2;
  }
  return rect.top <= 2;
}

/**
 * Whether `el` overflows the way the hero wrapper does: by more than 200px, or at
 * all when it holds a canvas (absolutely-positioned or inline canvas/img children
 * are what overflow it).
 */
function isInflated(el: HTMLElement): boolean {
  return (
    el.scrollHeight > el.offsetHeight + 200 ||
    (el.querySelector("canvas") !== null && el.scrollHeight > el.offsetHeight)
  );
}

/**
 * Locate any hero background wrapper whose canvas/content inflates scrollHeight.
 * Walks up from the container to the scroller, looking for a sibling that {@link isInflated}.
 */
export function findInflatedHeroWrapper(
  container: HTMLElement,
  playBarTop: HTMLElement,
  scroller: HTMLElement | Window,
): HTMLElement | null {
  const scrollerEl =
    "nodeType" in scroller && (scroller as HTMLElement).nodeType === 1 ? (scroller as HTMLElement) : null;
  let curr: HTMLElement = container;

  while (curr !== scrollerEl) {
    const parent: HTMLElement | null = curr.parentElement;
    if (!parent) break;

    const from = curr;
    const inflated = (Array.from(parent.children) as HTMLElement[]).find(
      (sibling) =>
        sibling !== from && !isTenderElement(sibling) && !sibling.contains(playBarTop) && isInflated(sibling),
    );
    if (inflated) return inflated;

    if (parent === scrollerEl) break;
    curr = parent;
  }

  return null;
}

export interface StickyPlayBarController {
  matches(playBarTop: HTMLElement, playSection: HTMLElement): boolean;
  updatePinning(): void;
  dispose(): void;
}

/**
 * Setup sticky play bar behavior and scroll monitoring using the provided ledger.
 */
export function createStickyPlayBarController(
  playBarTop: HTMLElement,
  playSection: HTMLElement,
  ledger: DomRestorationLedger,
  container?: HTMLElement,
  steamPanel?: HTMLElement,
): StickyPlayBarController {
  const applyBaselineStyles = () => {
    ledger.style(playBarTop, "position", "sticky");
    ledger.style(playBarTop, "top", "0px");
    ledger.style(playBarTop, "z-index", "10");
    ledger.style(playBarTop, "opacity", "1");
    ledger.style(playBarTop, "pointer-events", "auto");
    ledger.style(playBarTop, "padding-bottom", "2px");
    if (!playBarTop.style.transition) {
      ledger.style(playBarTop, "transition", "background-color 0.2s ease, box-shadow 0.2s ease");
    }

    if (playSection !== playBarTop) {
      if (!playSection.style.transition) {
        ledger.style(playSection, "transition", "background-color 0.2s ease");
      }
    }
  };

  applyBaselineStyles();

  const scroller = findScrollContainer(playBarTop);
  const win = playBarTop.ownerDocument.defaultView ?? window;

  const getHeroWrapper = (): HTMLElement | null => {
    if (!container || !steamPanel) return null;
    return findInflatedHeroWrapper(container, playBarTop, scroller) || findHeroWrapperFallback(steamPanel, playBarTop);
  };

  const ensureHeroVisible = (heroWrapper: HTMLElement | null) => {
    if (heroWrapper && heroWrapper.style.overflow !== "visible") {
      ledger.style(heroWrapper, "overflow", "visible");
    }
  };

  let isPinnedState: boolean | null = null;

  function applyPlayBarState(pinned: boolean) {
    if (isPinnedState === pinned) return;
    isPinnedState = pinned;

    if (pinned) {
      ledger.style(playBarTop, "background-image", "none");
      ledger.style(playBarTop, "background-color", SOLID_PLAY_BAR_BG);
      ledger.style(playBarTop, "backdrop-filter", "none");
      ledger.style(playBarTop, "-webkit-backdrop-filter", "none");
      ledger.style(playBarTop, "box-shadow", PINNED_PLAY_BAR_SHADOW);
      if (playSection !== playBarTop) {
        ledger.style(playSection, "background-color", SOLID_PLAY_BAR_BG);
      }
    } else {
      ledger.style(playBarTop, "background-image", GLASS_PLAY_BAR_GRADIENT);
      ledger.style(playBarTop, "background-color", GLASS_PLAY_BAR_BG);
      ledger.style(playBarTop, "backdrop-filter", "blur(12px)");
      ledger.style(playBarTop, "-webkit-backdrop-filter", "blur(12px)");
      ledger.style(playBarTop, "box-shadow", "none");
      if (playSection !== playBarTop) {
        ledger.style(playSection, "background-color", "transparent");
      }
    }
  }

  // Once the ledger has restored the page, nothing writes back onto it.
  let disposed = false;
  let ro: ResizeObserver | null = null;
  let observedLayer: HTMLElement | null = null;

  /**
   * The parallax layer's size feeds `boundHeroOverflow`'s margin as the
   * content's does, and changes without a child-list mutation, so the observer
   * follows whichever layer the hero holds now.
   */
  const observeLayer = (heroWrapper: HTMLElement | null) => {
    const layer = heroWrapper ? findHeroParallaxLayer(heroWrapper) : null;
    if (!ro || layer === observedLayer) return;
    if (observedLayer) ro.unobserve(observedLayer);
    if (layer) ro.observe(layer);
    observedLayer = layer;
  };

  const updatePinning = () => {
    if (disposed || !playBarTop.isConnected) return;
    applyBaselineStyles();
    const heroWrapper = getHeroWrapper();
    ensureHeroVisible(heroWrapper);
    if (heroWrapper && container && scroller !== win) {
      boundHeroOverflow(heroWrapper, scroller as HTMLElement, container, ledger);
    }
    observeLayer(heroWrapper);
    const pinned = isPlayBarPinned(playBarTop, scroller);
    applyPlayBarState(pinned);
  };

  const WinResizeObserver = (win as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver || ResizeObserver;
  if (typeof WinResizeObserver === "function") {
    try {
      ro = new WinResizeObserver(() => {
        updatePinning();
      });
      ro.observe(playBarTop);
      if ("nodeType" in scroller && (scroller as HTMLElement).nodeType === 1) {
        ro.observe(scroller as HTMLElement);
      }
      if (container) ro.observe(container);
    } catch (e) {
      logWarn(`desktop: no ResizeObserver on the play bar, so it re-pins only on scroll and resize: ${String(e)}`);
    }
  }

  ledger.addListener(scroller, "scroll", updatePinning, { passive: true });
  ledger.addListener(win, "resize", updatePinning, { passive: true });

  const controller: StickyPlayBarController = {
    matches: (top: HTMLElement, sec: HTMLElement) =>
      top === playBarTop && sec === playSection && playBarTop.isConnected,
    updatePinning,
    dispose: () => {
      disposed = true;
      if (ro) {
        ro.disconnect();
        ro = null;
      }
      ledger.removeListener(scroller, "scroll", updatePinning);
      ledger.removeListener(win, "resize", updatePinning);
      if (playSection !== playBarTop && playSection.isConnected) {
        ledger.style(playSection, "background-color", "transparent");
      }
    },
  };

  // The observer is already observing, and a caller handed a throw never gets
  // the controller whose dispose would disconnect it.
  try {
    updatePinning();
  } catch (e) {
    controller.dispose();
    throw e;
  }
  return controller;
}
