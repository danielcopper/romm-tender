/**
 * The navigation watcher: adapts Steam's game page for a RomM shortcut in the
 * desktop client (docs/architecture/desktop-dom-architecture.md).
 */

import { createElement, useLayoutEffect, type ComponentType } from "react";
import { logError, logInfo, logWarn } from "../api/backend";
import { isRomMAppId, onRomMAppIdsChanged } from "../utils/rommAppIds";
import { findDesktopWindow, findReactClient, type ReactClientModule } from "./desktopWindow";
import { getAppIdFromFiber } from "./watcher/fiberInspector";
import { DomRestorationLedger } from "./watcher/restorationLedger";
import {
  TENDER_PLAY_BUTTON_ID,
  TENDER_SUBSTITUTE_ID,
  findPlayBarAndContainer,
  findSteamContentSections,
  findSteamPlayBarBadges,
  findSteamPlaySection,
  findSteamRightControls,
  findSteamStickyPlayBar,
  locateOverviewPanel,
  locatePlayButton,
  type Located,
} from "./watcher/elementSelectors";
import { createStickyPlayBarController, type StickyPlayBarController } from "./watcher/stickyPlayBarController";

interface ViewProps {
  appId: number;
}

/**
 * What the watcher draws on a RomM game page. A part left out leaves Steam's
 * own in its place: without `gameView` the page's content sections stay, and
 * without `playButton` Steam's Play button and the badges beside it stay.
 */
export interface DesktopGamePage {
  /** Drawn below the play bar, in place of the page's content sections. */
  gameView?: ComponentType<ViewProps>;
  /** Drawn in place of Steam's Play button and its badges. */
  playButton?: ComponentType<ViewProps>;
}

type Part = keyof DesktopGamePage;

const HOST_IDS: Record<Part, string> = { gameView: TENDER_SUBSTITUTE_ID, playButton: TENDER_PLAY_BUTTON_ID };

/** Steam's page as the finders found it, or the part of it they could not find. */
type FoundPage = { panel: Located; playSection: HTMLElement } | { missing: "overview panel" | "play section" };

/** The RomM game page the watcher is on, from the first pass on its appId until the appId changes. */
interface Visit {
  appId: number;
  startedAt: number;
  recognised: boolean;
  missing: string | null;
  announced: boolean;
}

interface MainWindowBrowserManagerStub {
  m_lastLocation?: {
    pathname?: string;
  };
}

interface WindowWithManager {
  MainWindowBrowserManager?: MainWindowBrowserManagerStub;
}

/**
 * Extract shortcut appId from a path string (e.g. `/library/app/12345`).
 */
export function appIdOf(path: string | undefined | null): number | null {
  if (!path) return null;
  const match = /\/library\/app\/(\d+)/.exec(path);
  return match ? Number(match[1]) : null;
}

/** Draws `view`, and calls `onRendered` once React has committed it. */
function Rendered({
  view,
  appId,
  onRendered,
}: {
  view: ComponentType<ViewProps>;
  appId: number;
  onRendered: () => void;
}) {
  useLayoutEffect(() => {
    onRendered();
  }, [onRendered]);
  return createElement(view, { appId });
}

let activeWatcherStop: (() => void) | null = null;
let supervisorInterval: number | null = null;

function attachToDesktopWindow(page: DesktopGamePage, deskWin: Window): () => void {
  const { gameView: GameView, playButton: PlayButton } = page;
  const d = deskWin.document;
  const ledger = new DomRestorationLedger();
  let stickyController: StickyPlayBarController | null = null;
  let visit: Visit | null = null;
  // `undefined` until looked up: the lookup walks Steam's module registry, and
  // its answer does not change within a JS context.
  let client: ReactClientModule | null | undefined;
  const failedMounts = new Set<string>();
  const reported = new Set<string>();
  let restoreRequested = false;
  let frame: number | null = null;
  // A view's render callback can still arrive after stop; it must not schedule another pass.
  let stopped = false;

  function once(key: string, log: () => void): void {
    if (reported.has(key)) return;
    reported.add(key);
    log();
  }

  function unmountCurrent() {
    if (stickyController) {
      stickyController.dispose();
      stickyController = null;
    }
    ledger.restoreAll();

    for (const id of Object.values(HOST_IDS)) d.getElementById(id)?.remove();
  }

  /** Resolve the current navigation path from the browser manager or window location. */
  function getCurrentPath(): string {
    const manager =
      (deskWin as unknown as WindowWithManager).MainWindowBrowserManager ||
      (window as unknown as WindowWithManager).MainWindowBrowserManager;
    return manager?.m_lastLocation?.pathname || deskWin.location.pathname;
  }

  /** The page's appId: the route's, or the overview panel's Fiber's when the route names none. */
  function currentAppId(): number | null {
    const fromRoute = appIdOf(getCurrentPath());
    if (fromRoute) return fromRoute;
    const overviewCandidate = locateOverviewPanel(d)?.element;
    return overviewCandidate ? getAppIdFromFiber(overviewCandidate) : null;
  }

  /**
   * Give Steam's page back. What decides it is whether the ledger holds
   * anything, not whether a page was entered, so a pass that threw partway is
   * given back too.
   */
  function leavePage(left: number | null): void {
    const hostLeft = Object.values(HOST_IDS).some((id) => d.getElementById(id));
    if (!hostLeft && ledger.isEmpty()) return;
    unmountCurrent();
    logInfo(`desktop: left the game page of ${left ?? "a RomM shortcut"}, and gave Steam's page back`);
  }

  /**
   * End the visit and give Steam's page back, reporting a page the finders
   * never recognised (docs/architecture/desktop-dom-architecture.md, "What it logs").
   */
  function leaveVisit(): void {
    const left = visit;
    visit = null;
    if (left && !left.recognised && left.missing) {
      logWarn(
        `desktop: left the game page of ${left.appId} after ${Date.now() - left.startedAt} ms ` +
          `without recognising it: found no ${left.missing}`,
      );
    }
    leavePage(left?.appId ?? null);
  }

  /** Steam's overview panel and play section, or which is missing; a detached panel counts as missing. */
  function findPage(): FoundPage {
    const panel = locateOverviewPanel(d);
    if (!panel?.element.parentElement) return { missing: "overview panel" };
    const playSection = findSteamPlaySection(panel.element) || findSteamPlaySection(d);
    return playSection ? { panel, playSection } : { missing: "play section" };
  }

  /** The React client to mount with; `undefined` when nothing was handed over to mount, or none was found. */
  function clientForThePage(): ReactClientModule | undefined {
    if (!GameView && !PlayButton) return undefined;
    if (client === undefined) {
      client = findReactClient() ?? null;
      if (!client) logWarn("desktop: Steam's modules hold no createRoot, so the watcher mounts nothing");
    }
    return client ?? undefined;
  }

  /** Whether `part`'s host for `appId` is on the page and React has committed into it. */
  function rendered(part: Part, appId: number): boolean {
    const host = d.getElementById(HOST_IDS[part]);
    return !!host?.isConnected && host.dataset.appid === String(appId) && host.dataset.rendered === "true";
  }

  /**
   * The GameView already in place for this game in `container`; null when there
   * is none, or once a stale one is torn down.
   */
  function currentGameView(appId: number, container: HTMLElement): HTMLElement | null {
    const substitute = d.getElementById(TENDER_SUBSTITUTE_ID);
    if (!substitute) return null;
    if (
      substitute.isConnected &&
      substitute.dataset.appid === String(appId) &&
      substitute.parentElement === container
    ) {
      return substitute;
    }
    unmountCurrent();
    return null;
  }

  /**
   * Render `view` into `host`. A mount that fails is remembered for its appId
   * and not tried again, and has the next pass give Steam's page back first, so
   * Steam's own part returns where an earlier mount had hidden it.
   */
  function mountView(
    part: Part,
    view: ComponentType<ViewProps>,
    roots: ReactClientModule,
    host: HTMLElement,
    appId: number,
  ): void {
    host.id = HOST_IDS[part];
    host.dataset.appid = String(appId);
    const key = `${part}:${appId}`;
    const failed = (e: unknown) => {
      failedMounts.add(key);
      logError(`desktop: could not draw the ${part} for ${appId}, so Steam's own stays: ${String(e)}`);
      restoreRequested = true;
      schedulePass();
    };
    try {
      const root = roots.createRoot(host, { onUncaughtError: failed });
      ledger.recordRoot(root, host);
      const onRendered = () => {
        host.dataset.rendered = "true";
        schedulePass();
      };
      root.render(createElement(Rendered, { view, appId, onRendered }));
    } catch (e) {
      failed(e);
      ledger.releaseRoot(host);
    }
  }

  function adaptPlayButton(
    view: ComponentType<ViewProps>,
    roots: ReactClientModule | undefined,
    nativePlayBtn: HTMLElement,
    appId: number,
  ): void {
    const current = d.getElementById(TENDER_PLAY_BUTTON_ID);
    const inPlace =
      current?.isConnected &&
      current.dataset.appid === String(appId) &&
      (nativePlayBtn.parentElement === null || current.parentElement === nativePlayBtn.parentElement);
    if (!inPlace) {
      if (current) ledger.releaseRoot(current);
      if (!roots || failedMounts.has(`playButton:${appId}`)) return;
      const host = d.createElement("div");
      host.style.overflow = "visible";
      host.style.position = "relative";
      host.style.zIndex = "20";
      host.style.paddingBottom = "2px";
      nativePlayBtn.before(host);
      mountView("playButton", view, roots, host, appId);
    }
    if (rendered("playButton", appId)) ledger.hide(nativePlayBtn);
  }

  function refreshStickyPlayBar(
    playBarTop: HTMLElement,
    playSection: HTMLElement,
    container: HTMLElement,
    steamPanel: HTMLElement,
  ): void {
    if (stickyController && !stickyController.matches(playBarTop, playSection)) {
      stickyController.dispose();
      stickyController = null;
    }
    if (stickyController) {
      stickyController.updatePinning();
    } else {
      stickyController = createStickyPlayBarController(playBarTop, playSection, ledger, container, steamPanel);
    }
  }

  function hideBadges(playBarTop: HTMLElement, playSection: HTMLElement, nativePlayBtn: HTMLElement | null): void {
    const badges = new Set(findSteamPlayBarBadges(playBarTop, nativePlayBtn));
    if (!playBarTop.contains(playSection)) {
      for (const badge of findSteamPlayBarBadges(playSection, nativePlayBtn)) badges.add(badge);
    }
    for (const badge of badges) ledger.hide(badge);
  }

  /** Every adaptation of Steam's page, in the order desktop-dom-architecture.md, "The pass order", gives. */
  function adaptPage(
    steamPanel: HTMLElement,
    playSection: HTMLElement,
    playBarTop: HTMLElement,
    container: HTMLElement,
    nativePlayBtn: HTMLElement | null,
    roots: ReactClientModule | undefined,
    appId: number,
  ): void {
    if (GameView && rendered("gameView", appId)) {
      for (const sec of findSteamContentSections(steamPanel, playSection)) {
        ledger.hide(sec);
      }
    }

    if (PlayButton && nativePlayBtn) {
      adaptPlayButton(PlayButton, roots, nativePlayBtn, appId);
    }

    refreshStickyPlayBar(playBarTop, playSection, container, steamPanel);

    ledger.hide(findSteamStickyPlayBar(d, playBarTop));

    if (PlayButton && rendered("playButton", appId)) {
      hideBadges(playBarTop, playSection, nativePlayBtn);
    }

    const rightControls = findSteamRightControls(playBarTop) ?? findSteamRightControls(playSection);
    if (rightControls) {
      ledger.style(rightControls, "margin-left", "auto");
    }
  }

  function mountGameView(
    view: ComponentType<ViewProps>,
    roots: ReactClientModule,
    playBarTop: HTMLElement,
    container: HTMLElement,
    appId: number,
  ): void {
    const host = d.createElement("div");
    host.className = "tender-desktop-cards-container";
    host.style.position = "relative";
    host.style.zIndex = "1";

    const insertBeforeRef = playBarTop.nextElementSibling;
    if (insertBeforeRef?.parentElement === container) {
      insertBeforeRef.before(host);
    } else {
      container.appendChild(host);
    }
    mountView("gameView", view, roots, host, appId);
  }

  function pass(): void {
    if (restoreRequested) {
      restoreRequested = false;
      unmountCurrent();
    }
    const id = currentAppId();
    const appId = id && isRomMAppId(id) ? id : null;
    // Another game's page is a page left, even where Steam keeps the same elements.
    if (visit?.appId !== appId) leaveVisit();
    if (appId === null) return;
    visit ??= { appId, startedAt: Date.now(), recognised: false, missing: null, announced: false };

    const page = findPage();
    if ("missing" in page) {
      visit.missing = page.missing;
      return;
    }
    visit.recognised = true;
    const roots = clientForThePage();
    const steamPanel = page.panel.element;
    const { playSection } = page;
    const playBar = findPlayBarAndContainer(steamPanel, playSection);
    const { playBarTop, container } = playBar;
    const nativePlayBtn = locatePlayButton(playSection);

    // A stale GameView is torn down before the adaptations
    // (docs/architecture/desktop-dom-architecture.md, "The pass order").
    const substitute = currentGameView(appId, container);

    adaptPage(steamPanel, playSection, playBarTop, container, nativePlayBtn?.element ?? null, roots, appId);
    if (!visit.announced) {
      visit.announced = true;
      logInfo(
        `desktop: adapted the game page of ${appId}; found the panel by ${page.panel.rung}, ` +
          `the play bar by ${playBar.rung} and the Play button by ${nativePlayBtn?.rung ?? "no rung"}`,
      );
    }

    // The adaptations stay above this guard: Steam adds the hero's canvases after
    // a GameView has mounted, and a pass that returned here first would miss them.
    if (!GameView || !roots || substitute || failedMounts.has(`gameView:${appId}`)) {
      return;
    }
    mountGameView(GameView, roots, playBarTop, container, appId);
  }

  function runFrame(): void {
    frame = null;
    if (stopped) return;
    const path = getCurrentPath();
    try {
      pass();
    } catch (e) {
      once(`pass:${path}:${String(e)}`, () =>
        logError(`desktop: a pass over ${path} threw, and is given back on leaving the page: ${String(e)}`),
      );
    }
  }

  /** Run a pass in the next animation frame; triggers before it share that one pass. */
  function schedulePass(): void {
    if (stopped || frame !== null) return;
    frame = deskWin.requestAnimationFrame(runFrame);
  }

  let mo: MutationObserver | undefined;
  let unlistenAppIds: (() => void) | undefined;

  const stop = () => {
    stopped = true;
    if (frame !== null) deskWin.cancelAnimationFrame(frame);
    mo?.disconnect();
    unlistenAppIds?.();
    unmountCurrent();
  };

  try {
    const WinObserver =
      (deskWin as { MutationObserver?: typeof MutationObserver }).MutationObserver || MutationObserver;
    mo = new WinObserver(schedulePass);
    mo.observe(d.body, { childList: true, subtree: true });

    unlistenAppIds = onRomMAppIdsChanged(schedulePass);

    schedulePass();
  } catch (e) {
    stop();
    throw e;
  }
  return stop;
}

export function startDesktopNavigationWatcher(page: DesktopGamePage, customWin?: Window): () => void {
  stopDesktopNavigationWatcher();

  if (customWin) {
    if (typeof customWin.requestAnimationFrame !== "function") {
      return () => {};
    }
    const stop = attachToDesktopWindow(page, customWin);
    activeWatcherStop = stop;
    return () => {
      stop();
      if (activeWatcherStop === stop) {
        activeWatcherStop = null;
      }
    };
  }

  let currentDeskWin: Window | null = null;
  let currentDetach: (() => void) | null = null;
  // A window the watcher could not attach to is not tried again every poll.
  const refused = new WeakSet<Window>();
  // Nothing here unregisters the popup callbacks below, so they outlive this
  // supervisor; once it has stopped they must do nothing.
  let supervisorStopped = false;

  const detachCurrent = () => {
    if (currentDetach) {
      try {
        currentDetach();
      } catch (e) {
        logError(`desktop: detaching from the desktop client's window threw: ${String(e)}`);
      }
      currentDetach = null;
      logInfo("desktop: detached from the desktop client's window");
    }
    currentDeskWin = null;
  };

  const pollDesktop = () => {
    if (supervisorStopped) return;
    const foundWin = findDesktopWindow();

    if (currentDeskWin && (currentDeskWin.closed || currentDeskWin !== foundWin)) {
      detachCurrent();
    }

    if (!foundWin || foundWin.closed || foundWin === currentDeskWin || refused.has(foundWin)) return;
    try {
      const detach = attachToDesktopWindow(page, foundWin);
      currentDeskWin = foundWin;
      currentDetach = detach;
      logInfo("desktop: attached to the desktop client's window");
    } catch (e) {
      refused.add(foundWin);
      logError(`desktop: could not attach to the desktop client's window, and will not try it again: ${String(e)}`);
    }
  };

  pollDesktop();

  const popupManager = (
    window as unknown as {
      g_PopupManager?: {
        AddPopupCreatedCallback?: (cb: () => void) => void;
        AddPopupDestroyedCallback?: (cb: () => void) => void;
      };
    }
  ).g_PopupManager;
  for (const register of ["AddPopupCreatedCallback", "AddPopupDestroyedCallback"] as const) {
    if (typeof popupManager?.[register] !== "function") continue;
    try {
      popupManager[register](pollDesktop);
    } catch (e) {
      logWarn(`desktop: ${register} threw, so only the 500 ms poll finds the desktop client's window: ${String(e)}`);
    }
  }

  if (typeof window.setInterval === "function") {
    supervisorInterval = window.setInterval(pollDesktop, 500);
  }

  const stop = () => {
    supervisorStopped = true;
    if (supervisorInterval !== null && typeof window.clearInterval === "function") {
      window.clearInterval(supervisorInterval);
      supervisorInterval = null;
    }
    detachCurrent();
    if (activeWatcherStop === stop) {
      activeWatcherStop = null;
    }
  };

  activeWatcherStop = stop;
  return stop;
}

export function stopDesktopNavigationWatcher(): void {
  if (activeWatcherStop) {
    activeWatcherStop();
    activeWatcherStop = null;
  }
  if (supervisorInterval !== null && typeof window.clearInterval === "function") {
    window.clearInterval(supervisorInterval);
    supervisorInterval = null;
  }
}
