/**
 * Element selectors for the DOM of Steam's desktop client. The finders resolve
 * through the ladder on `docs/architecture/desktop-dom-architecture.md` ("The
 * selector ladder"); the hero's parallax layer and mirrored canvas are found by
 * their computed transform.
 */

import {
  appActionButtonClasses,
  appDetailsClasses,
  basicAppDetailsSectionStylerClasses,
  playSectionClasses,
} from "../../utils/deckyUiInternals";
import { getFiberDisplayName } from "./fiberInspector";

export const TENDER_SUBSTITUTE_ID = "tender-desktop-substitute";
export const TENDER_PLAY_BUTTON_ID = "tender-desktop-play-button";

/**
 * A rung of the selector ladder (docs/architecture/desktop-dom-architecture.md,
 * "The selector ladder"), named for what it matches.
 */
export type Rung = "module class" | "class name" | "Fiber name" | "structure";

/** An element a finder found, and the rung that found it. */
export interface Located {
  element: HTMLElement;
  rung: Rung;
}

export function getDocument(root: HTMLElement | Document): Document {
  return "ownerDocument" in root && root.ownerDocument ? root.ownerDocument : (root as Document);
}

export function getDirectChild(parent: HTMLElement, descendant: HTMLElement): HTMLElement {
  let curr: HTMLElement = descendant;
  while (curr.parentElement && curr.parentElement !== parent) {
    curr = curr.parentElement;
  }
  return curr;
}

/**
 * Test whether an element belongs to Tender's own UI.
 */
export function isTenderElement(el: HTMLElement): boolean {
  if (el.id === TENDER_PLAY_BUTTON_ID || el.id === TENDER_SUBSTITUTE_ID) return true;
  if (el.closest(`#${TENDER_PLAY_BUTTON_ID}, #${TENDER_SUBSTITUTE_ID}`)) return true;
  if (typeof el.className === "string" && el.className.includes("tender-")) return true;
  return false;
}

/** The rung that recognises `el` as the play bar container, or null when none does. */
export function playBarRung(el: HTMLElement): Rung | null {
  const moduleClasses = [
    playSectionClasses?.PlayBar,
    playSectionClasses?.InPage,
    playSectionClasses?.Container,
    appDetailsClasses?.PlayBar,
    basicAppDetailsSectionStylerClasses?.PlaySection,
  ];
  if (moduleClasses.some((name) => name && el.classList.contains(name))) return "module class";

  if (
    typeof el.className === "string" &&
    /(?:^|[^a-z0-9])(PlayBar|PlaySection|InPage)(?:[^a-z0-9]|$)/i.test(el.className)
  ) {
    return "class name";
  }

  const fiberName = getFiberDisplayName(el);
  if (fiberName && /^(PlayBar|PlaySection|InPage|ActionButtonAndStatusPanel)$/i.test(fiberName)) {
    return "Fiber name";
  }

  return null;
}

export function isPlayBarElement(el: HTMLElement): boolean {
  return playBarRung(el) !== null;
}

/** Steam's overview panel, and the rung that found it. */
export function locateOverviewPanel(doc: Document): Located | null {
  const ovClass = appDetailsClasses?.AppDetailsOverviewPanel;
  if (ovClass) {
    const el = doc.querySelector<HTMLElement>(`.${ovClass}:not(#${TENDER_SUBSTITUTE_ID})`);
    if (el) return { element: el, rung: "module class" };
  }

  const byName = doc.querySelector<HTMLElement>(`[class*="AppDetailsOverviewPanel"]:not(#${TENDER_SUBSTITUTE_ID})`);
  if (byName) return { element: byName, rung: "class name" };

  for (const c of Array.from(doc.querySelectorAll<HTMLElement>('div[class*="Panel"], div[class*="Overview"]'))) {
    if (isTenderElement(c)) continue;
    const fiberName = getFiberDisplayName(c);
    if (fiberName && /AppDetailsOverviewPanel|DesktopAppOverview/i.test(fiberName)) {
      return { element: c, rung: "Fiber name" };
    }
  }

  const rightPanel = doc.querySelector<HTMLElement>(`[class*="RightPanel"]:not(#${TENDER_SUBSTITUTE_ID})`);
  if (rightPanel) {
    const firstChild = rightPanel.querySelector<HTMLElement>(":scope > div:first-child");
    return { element: firstChild ?? rightPanel, rung: "structure" };
  }

  return null;
}

export function findSteamOverviewPanel(doc: Document): HTMLElement | null {
  return locateOverviewPanel(doc)?.element ?? null;
}

/** `.<class>` for each class `@decky/ui` resolved, leaving out the ones it did not. */
function classSelectors(...names: (string | undefined)[]): string[] {
  return names.filter((name): name is string => Boolean(name)).map((name) => `.${name}`);
}

/** Climb from `el` through each parent `keepClimbing` accepts, never reaching `stop`. */
function climb(el: HTMLElement, stop: Node, keepClimbing: (parent: HTMLElement) => boolean): HTMLElement {
  let top = el;
  while (top.parentElement && top.parentElement !== stop && keepClimbing(top.parentElement)) {
    top = top.parentElement;
  }
  return top;
}

function findActionContainerFromButton(
  btn: HTMLElement,
  root: HTMLElement | Document,
  docBody: HTMLElement,
): HTMLElement {
  let curr: HTMLElement = btn;
  while (curr.parentElement && curr.parentElement !== root && curr.parentElement !== docBody) {
    if (curr.parentElement.children.length > 1 || /play|action/i.test(curr.parentElement.className)) {
      return curr.parentElement;
    }
    curr = curr.parentElement;
  }
  return btn;
}

/** The element's parent when `container` names the parent's class, else the element. */
function withContainer(el: HTMLElement, container: RegExp): HTMLElement {
  return el.parentElement && container.test(el.parentElement.className) ? el.parentElement : el;
}

function playButtonByModuleClass(root: HTMLElement | Document): HTMLElement | null {
  const pbcClass = appActionButtonClasses?.PlayButtonContainer;
  const container = pbcClass ? root.querySelector<HTMLElement>(`.${pbcClass}`) : null;
  if (container) return container;
  const pbClass = appActionButtonClasses?.PlayButton;
  const button = pbClass ? root.querySelector<HTMLElement>(`.${pbClass}`) : null;
  return button ? withContainer(button, /PlayButtonContainer|AppActionButton/i) : null;
}

function playButtonByClassName(root: HTMLElement | Document): HTMLElement | null {
  const btn = root.querySelector<HTMLElement>(
    '[class*="PlayButtonContainer"], [class*="playbuttoncontainer"], [class*="PlayButton"], [class*="AppActionButton"], button[class*="play" i]',
  );
  return btn ? withContainer(btn, /PlayButtonContainer/i) : null;
}

function playButtonByFiberName(root: HTMLElement | Document): HTMLElement | null {
  const searchRoot = "querySelectorAll" in root ? root : getDocument(root);
  for (const candidate of Array.from(searchRoot.querySelectorAll<HTMLElement>("div, button"))) {
    if (isTenderElement(candidate)) continue;
    const fiberName = getFiberDisplayName(candidate);
    if (fiberName && /PlayButton|AppActionButton/i.test(fiberName)) {
      return withContainer(candidate, /PlayButtonContainer/i);
    }
  }
  return null;
}

/** Steam's Play button or its container, and the rung that found it. */
export function locatePlayButton(root: HTMLElement | Document): Located | null {
  const ladder: [Rung, (root: HTMLElement | Document) => HTMLElement | null][] = [
    ["module class", playButtonByModuleClass],
    ["class name", playButtonByClassName],
    ["Fiber name", playButtonByFiberName],
  ];
  for (const [rung, find] of ladder) {
    const element = find(root);
    if (element) return { element, rung };
  }
  return null;
}

export function findSteamPlayButton(root: HTMLElement | Document): HTMLElement | null {
  return locatePlayButton(root)?.element ?? null;
}

const PLAY_SECTION_SUBSTRINGS =
  '[class*="PlaySection"], [class*="PlayBar"], [class*="playsection"], [class*="playbar"], [class*="ActionButtonAndStatusPanel"]';

/**
 * Locate Steam's play section. It asks the Play button's ladder before its own,
 * and climbs from the button it finds.
 */
export function findSteamPlaySection(root: HTMLElement | Document): HTMLElement | null {
  const docBody = getDocument(root).body;

  const playBtn = findSteamPlayButton(root);
  if (playBtn) {
    return (
      playBtn.closest<HTMLElement>(PLAY_SECTION_SUBSTRINGS) ?? findActionContainerFromButton(playBtn, root, docBody)
    );
  }

  for (const selector of classSelectors(
    basicAppDetailsSectionStylerClasses?.PlaySection,
    playSectionClasses?.PlayBar,
    appDetailsClasses?.PlayBar,
    playSectionClasses?.Container,
    playSectionClasses?.ActionSection,
  )) {
    const el = root.querySelector<HTMLElement>(selector);
    if (el) return el;
  }

  const byName = root.querySelector<HTMLElement>(PLAY_SECTION_SUBSTRINGS);
  if (byName) return byName;

  if ("children" in root && (root as HTMLElement).children.length >= 2) {
    return (root as HTMLElement).firstElementChild as HTMLElement;
  }

  return null;
}

const CONTENT_SECTION_SELECTORS = [
  '[class*="ColumnContainer"]',
  '[class*="RightColumn"]',
  '[class*="ShortcutContainer"]',
  '[class*="Shortcut"]',
  '[class*="AppDetailSectionList"]',
  '[class*="AppDetailsContent"]',
];

/**
 * Helper to check if an element or its descendants contain Steam's content sections.
 */
export function containsContentSections(el: HTMLElement | null | undefined): boolean {
  if (!el) return false;
  return CONTENT_SECTION_SELECTORS.some((sel) => el.matches(sel) || el.querySelector(sel) !== null);
}

/**
 * Locate the play bar element and the common content container, and the rung
 * that recognised the play bar.
 */
export function findPlayBarAndContainer(
  overviewPanel: HTMLElement,
  playSection: HTMLElement,
): { playBarTop: HTMLElement; container: HTMLElement; rung: Rung } {
  if (!overviewPanel.contains(playSection)) {
    return { playBarTop: playSection, container: overviewPanel.parentElement || overviewPanel, rung: "structure" };
  }

  let curr: HTMLElement | null = playSection;
  let recognizedPlayBar: HTMLElement | null = null;
  let rung: Rung = "structure";
  while (curr && curr !== overviewPanel && curr !== overviewPanel.parentElement) {
    const currRung = playBarRung(curr);
    if (currRung) {
      recognizedPlayBar = curr;
      rung = currRung;
    }
    curr = curr.parentElement;
  }

  while (
    recognizedPlayBar?.parentElement &&
    recognizedPlayBar.parentElement !== overviewPanel &&
    overviewPanel.contains(recognizedPlayBar.parentElement) &&
    (isPlayBarElement(recognizedPlayBar.parentElement) ||
      (!containsContentSections(recognizedPlayBar.parentElement) &&
        recognizedPlayBar.parentElement.parentElement &&
        overviewPanel.contains(recognizedPlayBar.parentElement.parentElement) &&
        containsContentSections(recognizedPlayBar.parentElement.parentElement)))
  ) {
    recognizedPlayBar = recognizedPlayBar.parentElement;
  }

  if (recognizedPlayBar?.parentElement && overviewPanel.contains(recognizedPlayBar.parentElement)) {
    return { playBarTop: recognizedPlayBar, container: recognizedPlayBar.parentElement, rung };
  }

  let container: HTMLElement = overviewPanel;
  let playBarTop: HTMLElement = getDirectChild(container, playSection);

  while (
    !playBarTop.nextElementSibling &&
    playBarTop !== playSection &&
    overviewPanel.contains(playBarTop) &&
    playBarTop.children.length > 0
  ) {
    const deeper = getDirectChild(playBarTop, playSection);
    if (deeper === playBarTop) break;
    container = playBarTop;
    playBarTop = deeper;
  }

  return { playBarTop, container, rung: "structure" };
}

const STICKY_OR_PLAYBAR = /sticky|playbar/i;

/**
 * Locate Steam's native duplicate sticky play bar / header that appears when scrolling down.
 */
export function findSteamStickyPlayBar(root: Document | HTMLElement, playBarTop: HTMLElement): HTMLElement | null {
  const doc = getDocument(root);
  const isCandidate = (el: HTMLElement): boolean =>
    el !== playBarTop && !playBarTop.contains(el) && !el.contains(playBarTop) && !isTenderElement(el);
  const firstCandidate = (selector: string): HTMLElement | undefined =>
    Array.from(doc.querySelectorAll<HTMLElement>(selector)).find(isCandidate);

  const appDetailsPb = appDetailsClasses?.PlayBar;
  const playBarMatch = appDetailsPb ? firstCandidate(`.${appDetailsPb}`) : undefined;
  if (playBarMatch) return playBarMatch;

  const stickyHeaderClass = playSectionClasses?.StickyHeader;
  const stickyHeader = stickyHeaderClass ? firstCandidate(`.${stickyHeaderClass}`) : undefined;
  if (stickyHeader) {
    return climb(
      stickyHeader,
      doc.body,
      (parent) =>
        isCandidate(parent) &&
        ((!!appDetailsPb && parent.classList.contains(appDetailsPb)) || STICKY_OR_PLAYBAR.test(parent.className)),
    );
  }

  const showPlayBarClass = appDetailsClasses?.ShowPlayBar;
  const shown = showPlayBarClass ? firstCandidate(`.${showPlayBarClass}`) : undefined;
  if (shown) return shown;

  const bySubstring = firstCandidate(
    'div[class*="PlayBar"], div[class*="playbar"], div[class*="StickyHeader"], div[class*="stickyheader"]',
  );
  return bySubstring
    ? climb(bySubstring, doc.body, (parent) => isCandidate(parent) && STICKY_OR_PLAYBAR.test(parent.className))
    : null;
}

/**
 * Locate content sections to hide (non-Steam placeholder, notes, screenshots).
 */
export function findSteamContentSections(overviewPanel: HTMLElement, playSection: HTMLElement | null): HTMLElement[] {
  if (!playSection) {
    return [];
  }

  const { playBarTop } = findPlayBarAndContainer(overviewPanel, playSection);
  const toHide = new Set<HTMLElement>();

  let next = playBarTop.nextElementSibling as HTMLElement | null;
  while (next) {
    if (next.id !== TENDER_SUBSTITUTE_ID && !isTenderElement(next)) {
      toHide.add(next);
    }
    next = next.nextElementSibling as HTMLElement | null;
  }

  for (const selector of CONTENT_SECTION_SELECTORS) {
    const matches = overviewPanel.querySelectorAll<HTMLElement>(selector);
    for (const el of Array.from(matches)) {
      if (
        el.id !== TENDER_SUBSTITUTE_ID &&
        !isTenderElement(el) &&
        !el.contains(playSection) &&
        !el.contains(playBarTop) &&
        !playBarTop.contains(el)
      ) {
        toHide.add(el);
      }
    }
  }

  if (toHide.size === 0 && !overviewPanel.contains(playSection)) {
    toHide.add(overviewPanel);
  }

  return Array.from(toHide);
}

/**
 * Test whether an element is, or contains, the right-side controls
 * {@link findSteamRightControls} finds.
 */
export function isRightControlsElement(el: HTMLElement): boolean {
  if (
    (playSectionClasses?.RightControls && el.classList.contains(playSectionClasses.RightControls)) ||
    (playSectionClasses?.AppButtonsContainer && el.classList.contains(playSectionClasses.AppButtonsContainer)) ||
    (basicAppDetailsSectionStylerClasses?.AppButtons &&
      el.classList.contains(basicAppDetailsSectionStylerClasses.AppButtons))
  ) {
    return true;
  }

  if (
    typeof el.className === "string" &&
    /\b(RightControls|AppButtons|AppButtonsContainer|ControllerConfig|FavoriteButton|GameInfoButton)\b/i.test(
      el.className,
    )
  ) {
    return true;
  }

  const fiberName = getFiberDisplayName(el);
  if (
    fiberName &&
    /^(RightControls|AppButtons|AppButtonsContainer|ControllerConfig|FavoriteButton)$/i.test(fiberName)
  ) {
    return true;
  }

  return (
    el.querySelector(
      '[class*="RightControls"], [class*="AppButtons"], [class*="ControllerConfig"], [class*="FavoriteButton"]',
    ) !== null
  );
}

/**
 * Locate Steam's native play bar badges (Last Played, Playtime, Cloud Status, etc.) to hide.
 * Like `findSteamContentSections`, it collects from every rung rather than stopping at the first.
 */
export function findSteamPlayBarBadges(root: HTMLElement, nativePlayBtn?: HTMLElement | null): HTMLElement[] {
  const badges = new Set<HTMLElement>();

  const isPlayBtn = (el: HTMLElement): boolean => {
    if (!nativePlayBtn) return false;
    return el === nativePlayBtn || el.contains(nativePlayBtn) || nativePlayBtn.contains(el);
  };

  const isExcluded = (el: HTMLElement): boolean => isTenderElement(el) || isRightControlsElement(el) || isPlayBtn(el);

  // A label's badge: the GameStat around it, else its parent, unless that is the root or excluded.
  const badgeFor = (el: HTMLElement): HTMLElement => {
    const parent = el.closest<HTMLElement>('[class*="GameStat"]') ?? el.parentElement;
    return parent && parent !== root && !isExcluded(parent) ? parent : el;
  };

  const addMatches = (selectors: string[], toBadge: (el: HTMLElement) => HTMLElement): void => {
    for (const sel of selectors) {
      for (const el of Array.from(root.querySelectorAll<HTMLElement>(sel))) {
        if (!isExcluded(el)) badges.add(toBadge(el));
      }
    }
  };
  const itself = (el: HTMLElement): HTMLElement => el;

  addMatches(
    [
      ...classSelectors(playSectionClasses?.StatusAndStats, playSectionClasses?.GameStatsSection),
      '[class*="StatusAndStats"]',
      '[class*="GameStatsSection"]',
    ],
    itself,
  );

  addMatches(
    [
      ...classSelectors(
        playSectionClasses?.GameStat,
        playSectionClasses?.LastPlayed,
        playSectionClasses?.LastPlayedInfo,
        playSectionClasses?.Playtime,
        playSectionClasses?.CloudStatusRow,
        playSectionClasses?.MiniAchievements,
        playSectionClasses?.AchievementProgressRow,
        playSectionClasses?.DetailsSectionStatus,
      ),
      '[class*="GameStat"]',
      '[class*="LastPlayed"]',
      '[class*="Playtime"]',
      '[class*="CloudStatus"]',
      '[class*="MiniAchievements"]',
      '[class*="AchievementProgress"]',
    ],
    itself,
  );

  addMatches(
    [
      ...classSelectors(playSectionClasses?.PlayBarDetailLabel, playSectionClasses?.PlayBarLabel),
      '[class*="PlayBarDetailLabel"]',
      '[class*="PlayBarLabel"]',
    ],
    badgeFor,
  );

  return Array.from(badges);
}

/**
 * Locate Steam's right-side control buttons container (Settings gear, Controller layout, Favorite).
 */
export function findSteamRightControls(root: HTMLElement): HTMLElement | null {
  const selectors = [
    ...classSelectors(
      playSectionClasses?.RightControls,
      playSectionClasses?.AppButtonsContainer,
      basicAppDetailsSectionStylerClasses?.AppButtons,
    ),
    '[class*="RightControls"]',
    '[class*="AppButtonsContainer"]',
    '[class*="AppButtons"]',
  ];
  const isOuterControls = (parent: HTMLElement): boolean => !isTenderElement(parent) && isRightControlsElement(parent);

  for (const sel of selectors) {
    const el = root.querySelector<HTMLElement>(sel);
    if (el && !isTenderElement(el)) {
      return climb(el, root, isOuterControls);
    }
  }

  const fallbackMatch = root.querySelector<HTMLElement>('[class*="ControllerConfig"], [class*="FavoriteButton"]');
  if (fallbackMatch && !isTenderElement(fallbackMatch)) {
    return climb(fallbackMatch, root, isOuterControls);
  }

  return null;
}

/**
 * The hero wrapper by its class name, else the panel's first child unless that
 * child holds the play bar: what stands for the hero wrapper until Steam's
 * canvases inflate the real one and `findInflatedHeroWrapper` can find it.
 */
export function findHeroWrapperFallback(steamPanel: HTMLElement, playBarTop: HTMLElement): HTMLElement | null {
  const heroQuery = steamPanel.querySelector<HTMLElement>(
    '[class*="Hero"], [class*="hero"], [class*="HeroHeader"], [class*="HeroBanner"]',
  );
  if (heroQuery && heroQuery !== playBarTop && !heroQuery.contains(playBarTop)) {
    return heroQuery;
  }
  const fc = steamPanel.firstElementChild as HTMLElement | null;
  return fc && fc !== playBarTop && !fc.contains(playBarTop) ? fc : null;
}

/**
 * Locate the hero banner's parallax layer: the element under the hero wrapper
 * that carries the `matrix3d` transform, and the last link of its 3D chain.
 */
export function findHeroParallaxLayer(heroWrapper: HTMLElement): HTMLElement | null {
  const win = heroWrapper.ownerDocument.defaultView;
  if (!win) return null;
  for (const el of heroWrapper.querySelectorAll<HTMLElement>("*")) {
    if (win.getComputedStyle(el).transform.startsWith("matrix3d")) return el;
  }
  return null;
}

/**
 * Locate the mirrored canvas inside the parallax layer: the canvas Steam flips
 * vertically to continue the artwork's blurred background below the image.
 */
export function findHeroMirrorCanvas(parallaxLayer: HTMLElement): HTMLElement | null {
  const win = parallaxLayer.ownerDocument.defaultView;
  if (!win) return null;
  for (const child of parallaxLayer.children as HTMLCollectionOf<HTMLElement>) {
    if (child.tagName === "CANVAS" && win.getComputedStyle(child).transform !== "none") return child;
  }
  return null;
}
