import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { act, useEffect, useState } from "react";
import * as reactDomClient from "react-dom/client";
import type { Root } from "react-dom/client";
import {
  appIdOf,
  startDesktopNavigationWatcher,
  stopDesktopNavigationWatcher,
  type DesktopGamePage,
} from "./navigationWatcher";
import * as elementSelectors from "./watcher/elementSelectors";
import { TENDER_PLAY_BUTTON_ID, TENDER_SUBSTITUTE_ID } from "./watcher/elementSelectors";
import {
  GLASS_PLAY_BAR_BG,
  GLASS_PLAY_BAR_GRADIENT,
  PINNED_PLAY_BAR_SHADOW,
  SOLID_PLAY_BAR_BG,
} from "./gameview/styles";
import * as rommAppIds from "../utils/rommAppIds";
import * as desktopWin from "./desktopWindow";
import * as backend from "../api/backend";

const Blank = () => null;
const PAGE: DesktopGamePage = { gameView: Blank, playButton: Blank };

interface Manager {
  MainWindowBrowserManager?: unknown;
}

function navigateTo(path: string): void {
  (window as unknown as Manager).MainWindowBrowserManager = { m_lastLocation: { pathname: path } };
}

/** React's real client, standing in for the one in Steam's module registry, keeping every root it creates. */
function reactClient() {
  const roots: Root[] = [];
  const client = {
    createRoot: vi.fn((host: Element | DocumentFragment, options?: { onUncaughtError?: (error: unknown) => void }) => {
      const root = (reactDomClient as unknown as desktopWin.ReactClientModule).createRoot(host, options);
      vi.spyOn(root, "unmount");
      roots.push(root);
      return root;
    }),
  };
  const findClient = vi.spyOn(desktopWin, "findReactClient").mockReturnValue(client);
  return { client, roots, findClient };
}

/**
 * A stand-in for the desktop client's window. Its frames and observer run only
 * when the test says, React's work runs inside each frame, and its timers never
 * run: the watcher sets none.
 */
function desktopClient(doc: Document, extra: Record<string, unknown> = {}) {
  const frames = new Map<number, FrameRequestCallback>();
  let nextFrame = 1;
  let observed: (() => void) | undefined;
  class TestMutationObserver {
    constructor(cb: () => void) {
      observed = cb;
    }
    observe() {}
    disconnect() {
      observed = undefined;
    }
  }
  const win = {
    document: doc,
    location: { pathname: "/" },
    requestAnimationFrame: vi.fn((cb: FrameRequestCallback) => {
      const id = nextFrame++;
      frames.set(id, cb);
      return id;
    }),
    cancelAnimationFrame: vi.fn((id: number) => {
      frames.delete(id);
    }),
    setInterval: vi.fn(),
    clearInterval: vi.fn(),
    setTimeout: vi.fn(),
    MutationObserver: TestMutationObserver,
    ...extra,
  } as unknown as Window;

  /**
   * Run every frame due, and the frames those ask for in turn. Inside `act`,
   * React rethrows a render error rather than handing it to `onUncaughtError`,
   * so a test of that handler runs its frames outside it.
   */
  const flush = (insideAct = true) => {
    for (let i = 0; i < 20 && frames.size > 0; i++) {
      const due = [...frames.values()];
      frames.clear();
      const run = () => {
        for (const cb of due) cb(0);
      };
      if (insideAct) act(run);
      else run();
    }
  };
  return {
    win,
    frames,
    flush,
    /** Steam changed the page's children. */
    mutate: () => {
      observed?.();
      flush();
    },
    observing: () => observed !== undefined,
    fireObserver: () => observed?.(),
  };
}

type DesktopClient = ReturnType<typeof desktopClient>;

/** Start the watcher on `client`'s window and run its first pass; the stop it answers runs inside `act`. */
function start(page: DesktopGamePage, client: DesktopClient): () => void {
  const stop = startDesktopNavigationWatcher(page, client.win);
  client.flush();
  return () => {
    act(() => {
      stop();
    });
  };
}

function el<K extends keyof HTMLElementTagNameMap>(doc: Document, tag: K, className = ""): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  node.className = className;
  return node;
}

/**
 * A RomM game page: the hero, then a play bar holding the Play button, its
 * badges and the right-hand controls, then a content section; Steam's
 * duplicate sticky header sits outside the panel.
 */
function gamePage() {
  const doc = document.implementation.createHTMLDocument("Steam Desktop");
  const panel = el(doc, "div", "AppDetailsOverviewPanel");
  const hero = el(doc, "div", "HeroHeaderWrapper");
  const playBar = el(doc, "div", "PlayBar");
  const playButton = el(doc, "button", "PlayButton");
  const badges = el(doc, "div", "StatusAndStats");
  const lastPlayed = el(doc, "div", "GameStat LastPlayed");
  const controls = el(doc, "div", "RightControls");
  const content = el(doc, "div", "AppDetailSectionList");
  const duplicateHeader = el(doc, "div", "StickyHeaderBar");
  badges.appendChild(lastPlayed);
  playBar.append(playButton, badges, controls);
  panel.append(hero, playBar, content);
  doc.body.append(duplicateHeader, panel);
  return { doc, panel, hero, playBar, playButton, badges, lastPlayed, controls, content, duplicateHeader };
}

describe("navigationWatcher", () => {
  const originalManager = (window as unknown as Manager).MainWindowBrowserManager;

  beforeAll(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  beforeEach(() => {
    stopDesktopNavigationWatcher();
  });

  afterEach(() => {
    act(() => {
      stopDesktopNavigationWatcher();
    });
    (window as unknown as Manager).MainWindowBrowserManager = originalManager;
    vi.restoreAllMocks();
  });

  describe("appIdOf", () => {
    it("returns null for empty or invalid paths", () => {
      expect(appIdOf(null)).toBeNull();
      expect(appIdOf(undefined)).toBeNull();
      expect(appIdOf("")).toBeNull();
      expect(appIdOf("/store")).toBeNull();
      expect(appIdOf("/library/home")).toBeNull();
    });

    it("extracts appId correctly from library routes", () => {
      expect(appIdOf("/library/app/12345")).toBe(12345);
      expect(appIdOf("/library/app/98765/achievements")).toBe(98765);
    });
  });

  describe("lifecycle and mounting", () => {
    it("no-ops on a window that cannot run it", () => {
      const emptyWin = {} as Window;
      const stop = startDesktopNavigationWatcher(PAGE, emptyWin);
      expect(typeof stop).toBe("function");
      stop();
    });

    it("mounts on RomM shortcut preserving play bar and unmounts on non-RomM navigation", () => {
      const react = reactClient();
      const doc = document.implementation.createHTMLDocument("Steam Desktop");
      const parent = el(doc, "div");
      const steamPanel = el(doc, "div", "AppDetailsOverviewPanel");
      const playSection = el(doc, "div", "PlaySection");
      const contentSection = el(doc, "div", "AppDetailSectionList");
      steamPanel.append(playSection, contentSection);
      parent.appendChild(steamPanel);
      doc.body.appendChild(parent);
      const client = desktopClient(doc);
      navigateTo("/library/app/50000");
      vi.spyOn(rommAppIds, "isRomMAppId").mockImplementation((id) => id === 50000);

      const stop = start(PAGE, client);

      expect(steamPanel.style.display).not.toBe("none");
      expect(playSection.style.display).not.toBe("none");
      expect(contentSection.style.display).toBe("none");
      const host = doc.getElementById(TENDER_SUBSTITUTE_ID);
      expect(host?.dataset.appid).toBe("50000");
      expect(playSection.nextElementSibling).toBe(host);
      expect(react.client.createRoot).toHaveBeenCalledTimes(1);

      navigateTo("/library/app/60000");
      client.mutate();

      expect(react.roots[0]?.unmount).toHaveBeenCalledTimes(1);
      expect(doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
      expect(contentSection.style.display).toBe("");
      expect(playSection.style.display).toBe("");
      expect(steamPanel.style.display).toBe("");

      stop();
      expect(client.observing()).toBe(false);
    });

    it("gives back the panel it hid as content, with the display it had, once the panel holds the play section", () => {
      reactClient();
      const doc = document.implementation.createHTMLDocument("Steam Desktop");
      const steamPanel = el(doc, "div", "AppDetailsOverviewPanel");
      steamPanel.style.display = "flex";
      const playSection = el(doc, "div", "PlaySection");
      doc.body.append(steamPanel, playSection);
      const client = desktopClient(doc);
      navigateTo("/library/app/50000");
      const isRomM = vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);
      expect(steamPanel.style.display).toBe("none");

      steamPanel.appendChild(playSection);
      client.mutate();
      expect(steamPanel.style.display).toBe("flex");

      isRomM.mockReturnValue(false);
      client.mutate();
      expect(steamPanel.style.display).toBe("flex");
      stop();
    });

    it("leaves a panel Steam hid as Steam set it", () => {
      reactClient();
      const doc = document.implementation.createHTMLDocument("Steam Desktop");
      const steamPanel = el(doc, "div", "AppDetailsOverviewPanel");
      steamPanel.style.display = "none";
      const playSection = el(doc, "div", "PlaySection");
      steamPanel.appendChild(playSection);
      doc.body.appendChild(steamPanel);
      const client = desktopClient(doc);
      navigateTo("/library/app/50000");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);
      expect(doc.getElementById(TENDER_SUBSTITUTE_ID)).not.toBeNull();
      expect(steamPanel.style.display).toBe("none");
      stop();
      expect(steamPanel.style.display).toBe("none");
    });

    it("defers mounting when overview panel or play section is initially absent and mounts when they appear", () => {
      const react = reactClient();
      const doc = document.implementation.createHTMLDocument("Steam Desktop");
      const parent = el(doc, "div");
      doc.body.appendChild(parent);
      const client = desktopClient(doc);
      navigateTo("/library/app/555");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);
      expect(doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
      expect(react.client.createRoot).not.toHaveBeenCalled();

      const steamPanel = el(doc, "div", "AppDetailsOverviewPanel");
      const playSection = el(doc, "div", "PlaySection");
      const contentSection = el(doc, "div", "AppDetailSectionList");
      steamPanel.append(playSection, contentSection);
      parent.appendChild(steamPanel);
      client.mutate();

      expect(steamPanel.style.display).not.toBe("none");
      expect(playSection.style.display).not.toBe("none");
      expect(contentSection.style.display).toBe("none");
      const host = doc.getElementById(TENDER_SUBSTITUTE_ID);
      expect(host?.dataset.appid).toBe("555");
      expect(host?.parentElement).toBe(steamPanel);
      expect(react.client.createRoot).toHaveBeenCalledTimes(1);
      stop();
    });

    it("re-hides content section if Steam re-renders it after our mount", () => {
      const react = reactClient();
      const page = gamePage();
      const client = desktopClient(page.doc);
      navigateTo("/library/app/888");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);
      expect(page.content.style.display).toBe("none");

      page.content.style.display = "";
      client.mutate();

      expect(page.content.style.display).toBe("none");
      expect(react.client.createRoot).toHaveBeenCalledTimes(2);
      stop();
    });

    it("switches to new RomM game cleanly", () => {
      const react = reactClient();
      const page = gamePage();
      const client = desktopClient(page.doc);
      navigateTo("/library/app/111");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start({ gameView: Blank }, client);
      expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)?.dataset.appid).toBe("111");

      navigateTo("/library/app/222");
      client.mutate();

      expect(react.roots[0]?.unmount).toHaveBeenCalledTimes(1);
      expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)?.dataset.appid).toBe("222");

      stop();
      expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
    });

    it("mounts directly after playBar inside innerContainer, hiding shortcut notice and notes/recordings panel", () => {
      reactClient();
      const doc = document.implementation.createHTMLDocument("Steam Desktop");
      const parent = el(doc, "div");
      const overviewPanel = el(doc, "div", "AppDetailsOverviewPanel");
      const innerContainer = el(doc, "div", "Container Glassy");
      const playBar = el(doc, "div", "PlayBar");
      playBar.appendChild(el(doc, "button"));
      const shortcutNotice = el(doc, "div", "hashed-shortcut-notice");
      const columnContainer = el(doc, "div", "hashed-column-container");
      innerContainer.append(playBar, shortcutNotice, columnContainer);
      overviewPanel.appendChild(innerContainer);
      parent.appendChild(overviewPanel);
      doc.body.appendChild(parent);
      const client = desktopClient(doc);
      navigateTo("/library/app/70000");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);

      expect(playBar.style.display).not.toBe("none");
      expect(shortcutNotice.style.display).toBe("none");
      expect(columnContainer.style.display).toBe("none");
      const host = doc.getElementById(TENDER_SUBSTITUTE_ID);
      expect(host?.parentElement).toBe(innerContainer);
      expect(playBar.nextElementSibling).toBe(host);

      stop();
      expect(shortcutNotice.style.display).toBe("");
      expect(columnContainer.style.display).toBe("");
      expect(doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
    });

    it("mounts after inPageContainer when playBar is nested inside in-page wrapper", () => {
      reactClient();
      const doc = document.implementation.createHTMLDocument("Steam Desktop");
      const parent = el(doc, "div");
      const overviewPanel = el(doc, "div", "AppDetailsOverviewPanel");
      const innerContainer = el(doc, "div", "_27RcNu8aXKBpYkHcNNrt-X");
      const inPageContainer = el(doc, "div", "_3Yf8b2v5oOD8Wqsxu04ar _1U7LKpx70kEsz3jJwAFOi- InPage");
      const playBar = el(doc, "div", "_3fLo166MlaNqP8r8tTyRz PlayBar");
      playBar.appendChild(el(doc, "button", "PlayButton"));
      inPageContainer.appendChild(playBar);
      const columnContainer = el(doc, "div", "OhSdLYuggDtBcWjYP0j_9");
      innerContainer.append(inPageContainer, columnContainer);
      overviewPanel.appendChild(innerContainer);
      parent.appendChild(overviewPanel);
      doc.body.appendChild(parent);
      const client = desktopClient(doc);
      navigateTo("/library/app/70001");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);

      expect(inPageContainer.style.display).not.toBe("none");
      expect(columnContainer.style.display).toBe("none");
      const host = doc.getElementById(TENDER_SUBSTITUTE_ID);
      expect(host?.parentElement).toBe(innerContainer);
      expect(inPageContainer.nextElementSibling).toBe(host);
      expect(inPageContainer.contains(host)).toBe(false);

      stop();
      expect(columnContainer.style.display).toBe("");
      expect(doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
    });

    it("replaces native play button with Tender play button and restores on unmount", () => {
      const react = reactClient();
      const page = gamePage();
      const client = desktopClient(page.doc);
      navigateTo("/library/app/99999");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);

      expect(page.playButton.style.display).toBe("none");
      const playBtnHost = page.doc.getElementById(TENDER_PLAY_BUTTON_ID);
      expect(playBtnHost?.dataset.appid).toBe("99999");
      expect(playBtnHost?.style.paddingBottom).toBe("2px");
      expect(playBtnHost?.nextElementSibling).toBe(page.playButton);

      stop();

      expect(page.playButton.style.display).toBe("");
      expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)).toBeNull();
      for (const root of react.roots) expect(root.unmount).toHaveBeenCalled();
    });

    it("hides Steam default play bar badges alongside native play button and restores on unmount", () => {
      reactClient();
      const page = gamePage();
      const client = desktopClient(page.doc);
      navigateTo("/library/app/88888");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);

      expect(page.playButton.style.display).toBe("none");
      expect(page.badges.style.display).toBe("none");
      expect(page.lastPlayed.style.display).toBe("none");
      expect(page.controls.style.display).not.toBe("none");
      expect(page.controls.style.marginLeft).toBe("auto");

      stop();

      expect(page.playButton.style.display).toBe("");
      expect(page.badges.style.display).toBe("");
      expect(page.lastPlayed.style.display).toBe("");
      expect(page.controls.style.display).toBe("");
      expect(page.controls.style.marginLeft).toBe("");
    });

    it("isolates play bar container as sticky, hides duplicate sticky header, contains hero overflow, and restores on unmount", () => {
      reactClient();
      const page = gamePage();
      const client = desktopClient(page.doc);
      navigateTo("/library/app/55555");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);

      expect(page.playBar.style.position).toBe("sticky");
      expect(page.playBar.style.top).toBe("0px");
      expect(page.playBar.style.zIndex).toBe("10");
      expect(page.playBar.style.backgroundColor).toBe("rgb(39, 44, 53)");
      expect(page.playBar.style.paddingBottom).toBe("2px");
      expect(page.hero.style.overflow).toBe("visible");
      expect(page.duplicateHeader.style.display).toBe("none");
      expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)?.className).toContain("tender-desktop-cards-container");

      stop();

      expect(page.playBar.style.position).toBe("");
      expect(page.playBar.style.top).toBe("");
      expect(page.playBar.style.backgroundColor).toBe("");
      expect(page.playBar.style.paddingBottom).toBe("");
      expect(page.hero.style.overflow).toBe("");
      expect(page.duplicateHeader.style.display).toBe("");
      expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
    });

    describe("the hero wrapper", () => {
      /** scroller > panel > hero wrapper + content panel > overview panel > play bar + a content section. */
      function nestedHeroPage(heroScrollHeight: number) {
        const doc = document.implementation.createHTMLDocument("Steam Desktop");
        const scrollContainer = el(doc, "div");
        const panel = el(doc, "div");
        const heroWrapper = el(doc, "div", "HeroBanner");
        Object.defineProperty(heroWrapper, "scrollHeight", { value: heroScrollHeight, configurable: true });
        Object.defineProperty(heroWrapper, "offsetHeight", { value: 307, configurable: true });
        const contentPanel = el(doc, "div");
        const overviewPanel = el(doc, "div", "AppDetailsOverviewPanel");
        const inPagePlayBar = el(doc, "div", "InPagePlayBarContainer InPage");
        inPagePlayBar.appendChild(el(doc, "button", "PlayButton"));
        overviewPanel.append(inPagePlayBar, el(doc, "div", "ColumnContainer"));
        contentPanel.appendChild(overviewPanel);
        panel.append(heroWrapper, contentPanel);
        scrollContainer.appendChild(panel);
        doc.body.appendChild(scrollContainer);
        return { doc, heroWrapper };
      }

      it("keeps it visible when the panel's first child wraps the play bar", () => {
        reactClient();
        const page = nestedHeroPage(1978);
        const client = desktopClient(page.doc);
        navigateTo("/library/app/77777");
        vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

        const stop = start(PAGE, client);
        expect(page.heroWrapper.style.overflow).toBe("visible");

        stop();
        expect(page.heroWrapper.style.overflow).toBe("");
      });

      it("keeps it visible once it inflates after the GameView has mounted", () => {
        reactClient();
        const page = nestedHeroPage(307);
        const client = desktopClient(page.doc);
        navigateTo("/library/app/77778");
        vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

        const stop = start(PAGE, client);
        expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).not.toBeNull();
        expect(page.heroWrapper.style.overflow).toBe("");

        page.heroWrapper.appendChild(el(page.doc, "canvas"));
        Object.defineProperty(page.heroWrapper, "scrollHeight", { value: 1978, configurable: true });
        client.mutate();

        expect(page.heroWrapper.style.overflow).toBe("visible");
        stop();
        expect(page.heroWrapper.style.overflow).toBe("");
      });
    });

    it("hides native duplicate sticky header when situated outside overviewPanel under main window split and restores on unmount", () => {
      reactClient();
      const doc = document.implementation.createHTMLDocument("Steam Desktop");
      const mainWindowSplit = el(doc, "div", "MainWindowSplit");
      const duplicateStickyPlayBar = el(doc, "div", "PlayBar");
      const scrollContainer = el(doc, "div", "ScrollContainer");
      const overviewPanel = el(doc, "div", "AppDetailsOverviewPanel");
      const inPagePlayBar = el(doc, "div", "InPagePlayBarContainer InPage");
      inPagePlayBar.appendChild(el(doc, "button", "PlayButton"));
      overviewPanel.appendChild(inPagePlayBar);
      scrollContainer.appendChild(overviewPanel);
      mainWindowSplit.append(duplicateStickyPlayBar, scrollContainer);
      doc.body.appendChild(mainWindowSplit);
      const client = desktopClient(doc);
      navigateTo("/library/app/55556");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);
      expect(duplicateStickyPlayBar.style.display).toBe("none");

      stop();
      expect(duplicateStickyPlayBar.style.display).toBe("");
    });

    describe("dynamic play bar glass and solid pinning", () => {
      it("applies grey glass styling when unpinned and transitions to solid grey when pinned", () => {
        reactClient();
        const doc = document.implementation.createHTMLDocument("Steam Desktop Dynamic Pinned");
        const scroller = el(doc, "div");
        scroller.style.overflowY = "scroll";
        const overviewPanel = el(doc, "div", "AppDetailsOverviewPanel");
        const inPagePlayBar = el(doc, "div", "InPagePlayBarContainer InPage");
        const playSection = el(doc, "div", "PlaySection");
        playSection.appendChild(el(doc, "button", "PlayButton"));
        inPagePlayBar.appendChild(playSection);
        overviewPanel.appendChild(inPagePlayBar);
        scroller.appendChild(overviewPanel);
        doc.body.appendChild(scroller);

        let pbTop = 300;
        const rect = (top: number, height: number) =>
          ({ top, bottom: top + height, left: 0, right: 1000, width: 1000, height }) as DOMRect;
        vi.spyOn(inPagePlayBar, "getBoundingClientRect").mockImplementation(() => rect(pbTop, 50));
        vi.spyOn(scroller, "getBoundingClientRect").mockImplementation(() => rect(50, 600));

        const client = desktopClient(doc);
        navigateTo("/library/app/55557");
        vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

        const stop = start(PAGE, client);

        expect(inPagePlayBar.style.position).toBe("sticky");
        expect(inPagePlayBar.style.top).toBe("0px");
        expect(inPagePlayBar.style.backgroundImage).toBe(GLASS_PLAY_BAR_GRADIENT);
        expect(inPagePlayBar.style.backgroundColor).toBe(GLASS_PLAY_BAR_BG);
        expect(inPagePlayBar.style.backdropFilter).toBe("blur(12px)");
        expect(inPagePlayBar.style.boxShadow).toBe("none");
        expect(playSection.style.backgroundColor).toBe("transparent");

        pbTop = 50;
        scroller.dispatchEvent(new Event("scroll"));

        expect(inPagePlayBar.style.backgroundImage).toBe("none");
        expect(inPagePlayBar.style.backgroundColor).toBe(SOLID_PLAY_BAR_BG);
        expect(inPagePlayBar.style.backdropFilter).toBe("none");
        expect(inPagePlayBar.style.boxShadow).toBe(PINNED_PLAY_BAR_SHADOW);
        expect(playSection.style.backgroundColor).toBe(SOLID_PLAY_BAR_BG);

        pbTop = 300;
        scroller.dispatchEvent(new Event("scroll"));

        expect(inPagePlayBar.style.backgroundImage).toBe(GLASS_PLAY_BAR_GRADIENT);
        expect(inPagePlayBar.style.backgroundColor).toBe(GLASS_PLAY_BAR_BG);
        expect(inPagePlayBar.style.backdropFilter).toBe("blur(12px)");
        expect(inPagePlayBar.style.boxShadow).toBe("none");
        expect(playSection.style.backgroundColor).toBe("transparent");

        stop();
        expect(inPagePlayBar.style.position).toBe("");
        expect(inPagePlayBar.style.top).toBe("");
        expect(inPagePlayBar.style.backgroundImage).toBe("");
        expect(inPagePlayBar.style.backgroundColor).toBe("");
        expect(inPagePlayBar.style.backdropFilter).toBe("");
        expect(inPagePlayBar.style.boxShadow).toBe("");
        expect(playSection.style.backgroundColor).toBe("");
      });

      it("recreates sticky controller when playBarTop is replaced during Steam re-render", () => {
        reactClient();
        const doc = document.implementation.createHTMLDocument("Steam Desktop");
        const overviewPanel = el(doc, "div", "AppDetailsOverviewPanel");
        const playBar1 = el(doc, "div", "PlayBar");
        playBar1.appendChild(el(doc, "button", "PlayButton"));
        overviewPanel.appendChild(playBar1);
        doc.body.appendChild(overviewPanel);
        const client = desktopClient(doc);
        navigateTo("/library/app/55558");
        vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

        const stop = start(PAGE, client);
        expect(playBar1.style.zIndex).toBe("10");

        const playBar2 = el(doc, "div", "PlayBar");
        playBar2.appendChild(el(doc, "button", "PlayButton"));
        overviewPanel.replaceChild(playBar2, playBar1);
        client.mutate();

        expect(playBar2.style.zIndex).toBe("10");
        expect(playBar2.style.position).toBe("sticky");
        stop();
      });
    });
  });

  describe("switching games, stopping, and the cost of a pass", () => {
    function startOn(appId: number, page: DesktopGamePage = PAGE) {
      const react = reactClient();
      const steam = gamePage();
      const client = desktopClient(steam.doc);
      navigateTo(`/library/app/${appId}`);
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);
      const stop = start(page, client);
      return { ...steam, react, desktop: client, findClient: react.findClient, stop };
    }

    it("gives Steam's Play button back on a switch to a RomM game whose Play button cannot be drawn", () => {
      vi.spyOn(backend, "logError").mockImplementation(() => {});
      const page = startOn(99999, { playButton: Blank });
      expect(page.playButton.style.display).toBe("none");
      page.react.client.createRoot.mockImplementationOnce(() => {
        throw new Error("no root for this game");
      });

      navigateTo("/library/app/88888");
      page.desktop.mutate();
      expect(page.playButton.style.display).toBe("");
      expect(page.badges.style.display).toBe("");
      expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)).toBeNull();

      navigateTo("/library/app/99999");
      page.desktop.mutate();
      expect(page.playButton.style.display).toBe("none");

      // Not drawn again for the game it failed on, and Steam's button stays.
      navigateTo("/library/app/88888");
      page.desktop.mutate();
      expect(page.playButton.style.display).toBe("");
      expect(page.badges.style.display).toBe("");
      expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)).toBeNull();
      expect(page.react.client.createRoot).toHaveBeenCalledTimes(3);
      page.stop();
    });

    it("keeps Steam's play button hidden through a switch to another RomM game", () => {
      const page = startOn(99999);

      navigateTo("/library/app/88888");
      page.desktop.mutate();

      expect(page.playButton.style.display).toBe("none");
      expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)?.dataset.appid).toBe("88888");
      expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)?.dataset.appid).toBe("88888");
      page.stop();
    });

    it("mounts nothing from a frame asked for just before the watcher stopped", () => {
      const page = startOn(99999);
      const rootsBeforeStop = page.react.client.createRoot.mock.calls.length;

      page.desktop.fireObserver();
      page.stop();
      page.desktop.flush();

      expect(page.react.client.createRoot).toHaveBeenCalledTimes(rootsBeforeStop);
      expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
      expect(page.playButton.style.display).toBe("");
    });

    it("looks createRoot up once, not on every pass", () => {
      const page = startOn(99999);

      page.desktop.mutate();
      page.desktop.mutate();

      expect(page.findClient).toHaveBeenCalledTimes(1);
      page.stop();
    });

    it("looks a missing createRoot up once too, and says so once", () => {
      const logWarn = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const steam = gamePage();
      const findClient = vi.spyOn(desktopWin, "findReactClient").mockReturnValue(undefined);
      const client = desktopClient(steam.doc);
      navigateTo("/library/app/99999");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);
      client.mutate();
      client.mutate();

      expect(findClient).toHaveBeenCalledTimes(1);
      expect(logWarn).toHaveBeenCalledTimes(1);
      expect(steam.playButton.style.display).toBe("");
      expect(steam.playBar.style.position).toBe("sticky");
      stop();
    });

    it("mounts the play button and the GameView once, not on every pass", () => {
      const page = startOn(99999);

      page.desktop.mutate();
      page.desktop.mutate();

      expect(page.react.client.createRoot).toHaveBeenCalledTimes(2);
      page.stop();
    });

    it("runs one pass for every trigger that arrives before the next frame", () => {
      const page = startOn(99999);
      const framesSoFar = vi.mocked(page.desktop.win.requestAnimationFrame).mock.calls.length;

      for (let i = 0; i < 10; i++) page.desktop.fireObserver();
      page.desktop.mutate();

      expect(vi.mocked(page.desktop.win.requestAnimationFrame).mock.calls.length - framesSoFar).toBe(1);
      page.stop();
    });

    it("hides none of Steam's page behind a view that has not rendered", () => {
      const steam = gamePage();
      const createRoot = vi.fn(() => ({ render: vi.fn(), unmount: vi.fn() }) as unknown as Root);
      vi.spyOn(desktopWin, "findReactClient").mockReturnValue({ createRoot });
      const client = desktopClient(steam.doc);
      navigateTo("/library/app/99999");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);

      const stop = start(PAGE, client);
      for (let i = 0; i < 3; i++) client.mutate();

      expect(steam.doc.getElementById(TENDER_PLAY_BUTTON_ID)).not.toBeNull();
      expect(steam.doc.getElementById(TENDER_SUBSTITUTE_ID)).not.toBeNull();
      expect(steam.playButton.style.display).toBe("");
      expect(steam.badges.style.display).toBe("");
      expect(steam.content.style.display).toBe("");
      stop();
    });

    describe("a page with nothing to mount", () => {
      it("leaves Steam's content and Play button in place and creates no React root", () => {
        const page = startOn(99999, {});

        expect(page.playButton.style.display).toBe("");
        expect(page.content.style.display).toBe("");
        expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)).toBeNull();
        expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
        expect(page.findClient).not.toHaveBeenCalled();
        page.stop();
      });

      it("adapts the play bar and gives it back on leaving the page", () => {
        const page = startOn(99999, {});
        expect(page.playBar.style.position).toBe("sticky");

        vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(false);
        navigateTo("/library/app/77777");
        page.desktop.mutate();

        expect(page.playBar.style.position).toBe("");
        page.stop();
      });

      it("runs on no timer of its own: every pass is one something asked for", () => {
        const page = startOn(99999, {});
        navigateTo("/library/app/88888");
        page.desktop.mutate();

        expect(page.desktop.win.setInterval).not.toHaveBeenCalled();
        expect(page.desktop.win.setTimeout).not.toHaveBeenCalled();
        page.stop();
      });
    });

    describe("a page handed one part", () => {
      it("with only a GameView, draws it and leaves Steam's Play button and badges", () => {
        const page = startOn(99999, { gameView: Blank });

        expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)?.dataset.appid).toBe("99999");
        expect(page.content.style.display).toBe("none");
        expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)).toBeNull();
        expect(page.playButton.style.display).toBe("");
        expect(page.badges.style.display).toBe("");
        page.stop();
      });

      it("with only a Play button, draws it and leaves Steam's content", () => {
        const page = startOn(99999, { playButton: Blank });

        expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)?.dataset.appid).toBe("99999");
        expect(page.playButton.style.display).toBe("none");
        expect(page.badges.style.display).toBe("none");
        expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
        expect(page.content.style.display).toBe("");
        page.stop();
      });
    });
  });

  describe("every change it makes is given back", () => {
    it.each([
      ["with both parts handed", PAGE],
      ["with nothing handed", {}],
    ])("leaves every element's style attribute as it found it, %s", async (_name, handed: DesktopGamePage) => {
      reactClient();
      const page = gamePage();
      const scroller = el(page.doc, "div", "ScrollPanel");
      scroller.style.overflowY = "auto";
      page.panel.replaceWith(scroller);
      scroller.appendChild(page.panel);
      page.panel.style.display = "flex";
      page.playBar.style.zIndex = "3";
      page.lastPlayed.style.display = "inline-block";
      const before = new Map(
        Array.from(page.doc.querySelectorAll<HTMLElement>("*"), (node) => [node, node.getAttribute("style") ?? ""]),
      );
      const client = desktopClient(page.doc);
      navigateTo("/library/app/50000");
      vi.spyOn(rommAppIds, "isRomMAppId").mockImplementation((id) => id === 50000);
      const otherShortcut = 50001;

      try {
        const stop = start(handed, client);
        client.mutate();
        client.mutate();
        expect(page.playBar.style.backgroundColor).toBe(SOLID_PLAY_BAR_BG);

        // A scroll and a resize re-pin the play bar; each must reach the listener it is for.
        vi.spyOn(page.playBar, "getBoundingClientRect").mockReturnValue({ top: 100 } as DOMRect);
        scroller.dispatchEvent(new Event("scroll"));
        expect(page.playBar.style.backgroundColor).toBe(GLASS_PLAY_BAR_BG);
        vi.spyOn(page.playBar, "getBoundingClientRect").mockReturnValue({ top: 0 } as DOMRect);
        window.dispatchEvent(new Event("resize"));
        expect(page.playBar.style.backgroundColor).toBe(SOLID_PLAY_BAR_BG);

        const framesSoFar = vi.mocked(client.win.requestAnimationFrame).mock.calls.length;
        rommAppIds.registerRomMAppId(otherShortcut);
        await Promise.resolve();
        expect(vi.mocked(client.win.requestAnimationFrame).mock.calls).toHaveLength(framesSoFar + 1);
        client.flush();

        navigateTo("/library/home");
        client.mutate();

        for (const [node, style] of before) expect(node.getAttribute("style") ?? "", node.className).toBe(style);
        expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)).toBeNull();
        expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
        stop();
      } finally {
        rommAppIds.unregisterRomMAppId(otherShortcut);
      }
    });
  });

  describe("what makes it look again", () => {
    it("adapts a page whose shortcut is registered after the page opened", async () => {
      const appId = 31337;
      const page = gamePage();
      const client = desktopClient(page.doc);
      navigateTo(`/library/app/${appId}`);
      try {
        const stop = start({}, client);
        expect(page.playBar.style.position).toBe("");

        rommAppIds.registerRomMAppId(appId);
        await Promise.resolve();
        client.flush();

        expect(page.playBar.style.position).toBe("sticky");
        stop();
      } finally {
        rommAppIds.unregisterRomMAppId(appId);
      }
    });

    it("adapts what Steam adds to a page it has already adapted", () => {
      const page = gamePage();
      page.duplicateHeader.remove();
      const client = desktopClient(page.doc);
      navigateTo("/library/app/50000");
      vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);
      const stop = start({}, client);
      client.mutate();

      page.doc.body.prepend(page.duplicateHeader);
      client.mutate();

      expect(page.duplicateHeader.style.display).toBe("none");
      stop();
    });
  });

  describe("when something fails", () => {
    function failingPage(appId = 50000) {
      const page = gamePage();
      const client = desktopClient(page.doc);
      navigateTo(`/library/app/${appId}`);
      vi.spyOn(rommAppIds, "isRomMAppId").mockImplementation((id) => id === appId);
      const logError = vi.spyOn(backend, "logError").mockImplementation(() => {});
      return { ...page, desktop: client, logError };
    }

    it("gives back what a pass changed before it threw, and says so once", () => {
      const page = failingPage();
      vi.spyOn(elementSelectors, "findSteamRightControls").mockImplementation(() => {
        throw new Error("a finder broke");
      });

      const stop = start({}, page.desktop);
      page.desktop.mutate();
      page.desktop.mutate();
      expect(page.playBar.style.position).toBe("sticky");

      navigateTo("/library/home");
      page.desktop.mutate();

      expect(page.playBar.style.position).toBe("");
      expect(page.playBar.style.zIndex).toBe("");
      expect(page.logError).toHaveBeenCalledTimes(1);
      expect(page.logError).toHaveBeenCalledWith(expect.stringContaining("a finder broke"));
      stop();
    });

    it("leaves Steam's Play button when createRoot throws, and does not try again", () => {
      const page = failingPage();
      const createRoot = vi.fn(() => {
        throw new Error("no root");
      });
      vi.spyOn(desktopWin, "findReactClient").mockReturnValue({ createRoot });

      const stop = start({ playButton: Blank }, page.desktop);
      for (let i = 0; i < 5; i++) page.desktop.mutate();
      page.desktop.mutate();

      expect(page.playButton.style.display).toBe("");
      expect(page.badges.style.display).toBe("");
      expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)).toBeNull();
      expect(createRoot).toHaveBeenCalledTimes(1);
      expect(page.logError).toHaveBeenCalledTimes(1);
      expect(page.logError).toHaveBeenCalledWith(expect.stringContaining("no root"));
      stop();
    });

    it("gives Steam's Play button back when the view it drew throws, and does not try again", async () => {
      const page = failingPage();
      const react = reactClient();
      const view: { break?: () => void } = {};
      const Flaky = () => {
        const [broken, setBroken] = useState(false);
        useEffect(() => {
          view.break = () => setBroken(true);
        }, []);
        if (broken) throw new Error("the view broke");
        return null;
      };
      const stop = startDesktopNavigationWatcher({ playButton: Flaky }, page.desktop.win);
      page.desktop.flush();
      expect(page.playButton.style.display).toBe("none");

      const actEnvironment = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
      actEnvironment.IS_REACT_ACT_ENVIRONMENT = false;
      try {
        view.break?.();
        await vi.waitFor(() => expect(page.logError).toHaveBeenCalledWith(expect.stringContaining("the view broke")));
        page.desktop.flush(false);

        expect(page.playButton.style.display).toBe("");
        expect(page.badges.style.display).toBe("");
        expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)).toBeNull();
        expect(react.client.createRoot).toHaveBeenCalledTimes(1);
        stop();
      } finally {
        actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;
      }
    });

    it("leaves Steam's content when createRoot throws for the GameView, and does not try again", () => {
      const page = failingPage();
      const createRoot = vi.fn(() => {
        throw new Error("no root");
      });
      vi.spyOn(desktopWin, "findReactClient").mockReturnValue({ createRoot });

      const stop = start({ gameView: Blank }, page.desktop);
      for (let i = 0; i < 5; i++) page.desktop.mutate();

      expect(page.content.style.display).toBe("");
      expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
      expect(createRoot).toHaveBeenCalledTimes(1);
      expect(page.logError).toHaveBeenCalledWith(expect.stringContaining("no root"));
      stop();
    });

    it("gives Steam's content back when the GameView it drew throws, and does not try again", async () => {
      const page = failingPage();
      const react = reactClient();
      const view: { break?: () => void } = {};
      const Flaky = () => {
        const [broken, setBroken] = useState(false);
        useEffect(() => {
          view.break = () => setBroken(true);
        }, []);
        if (broken) throw new Error("the view broke");
        return null;
      };
      const stop = startDesktopNavigationWatcher({ gameView: Flaky }, page.desktop.win);
      page.desktop.flush();
      expect(page.content.style.display).toBe("none");

      const actEnvironment = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
      actEnvironment.IS_REACT_ACT_ENVIRONMENT = false;
      try {
        view.break?.();
        await vi.waitFor(() => expect(page.logError).toHaveBeenCalledWith(expect.stringContaining("the view broke")));
        page.desktop.flush(false);

        expect(page.content.style.display).toBe("");
        expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
        expect(react.client.createRoot).toHaveBeenCalledTimes(1);
        stop();
      } finally {
        actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;
      }
    });

    it("gives Steam's Play button back when a Play button it replaces cannot be drawn", () => {
      const page = failingPage();
      const react = reactClient();
      const stop = start({ playButton: Blank }, page.desktop);
      expect(page.playButton.style.display).toBe("none");
      react.client.createRoot.mockImplementationOnce(() => {
        throw new Error("no root");
      });

      const otherSlot = el(page.doc, "div", "Elsewhere");
      page.playBar.appendChild(otherSlot);
      otherSlot.appendChild(page.playButton);
      page.desktop.mutate();

      expect(page.playButton.style.display).toBe("");
      expect(page.badges.style.display).toBe("");
      expect(page.doc.getElementById(TENDER_PLAY_BUTTON_ID)).toBeNull();
      stop();
    });

    it.each([
      ["overview panel", (page: ReturnType<typeof gamePage>) => page.panel.remove()],
      // The structural rung answers a panel's first child once it holds two.
      [
        "play section",
        (page: ReturnType<typeof gamePage>) => {
          page.playBar.remove();
          page.content.remove();
        },
      ],
    ])("says once, on leaving it, that it never recognised a RomM game page: no %s", (missing, breakPage) => {
      const page = failingPage();
      const logWarn = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const now = vi.spyOn(Date, "now").mockReturnValue(1_000);
      breakPage(page);

      const stop = start({}, page.desktop);
      now.mockReturnValue(5_000);
      page.desktop.mutate();
      page.desktop.mutate();
      expect(logWarn).not.toHaveBeenCalled();

      now.mockReturnValue(13_000);
      navigateTo("/library/home");
      page.desktop.mutate();
      page.desktop.mutate();

      expect(logWarn).toHaveBeenCalledTimes(1);
      expect(logWarn).toHaveBeenCalledWith(
        `desktop: left the game page of 50000 after 12000 ms without recognising it: found no ${missing}`,
      );
      stop();
    });

    it("says how soon it was left of a page left before Steam drew it", () => {
      const page = failingPage();
      const logWarn = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const now = vi.spyOn(Date, "now").mockReturnValue(1_000);
      page.panel.remove();

      const stop = start({}, page.desktop);
      now.mockReturnValue(1_040);
      navigateTo("/library/home");
      page.desktop.mutate();

      expect(logWarn).toHaveBeenCalledWith(
        "desktop: left the game page of 50000 after 40 ms without recognising it: found no overview panel",
      );
      stop();
    });

    it("says nothing of a page it recognised once Steam had drawn it", () => {
      const page = failingPage();
      const logWarn = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      page.panel.remove();

      const stop = start({}, page.desktop);
      page.doc.body.append(page.panel);
      page.desktop.mutate();
      expect(page.playBar.style.position).toBe("sticky");

      navigateTo("/library/home");
      page.desktop.mutate();

      expect(logWarn).not.toHaveBeenCalled();
      stop();
    });

    it("unmounts the root of a Play button it replaces", () => {
      const page = failingPage();
      const react = reactClient();
      const stop = start({ playButton: Blank }, page.desktop);

      const otherSlot = el(page.doc, "div", "Elsewhere");
      page.playBar.appendChild(otherSlot);
      otherSlot.appendChild(page.playButton);
      page.desktop.mutate();

      expect(react.roots).toHaveLength(2);
      expect(react.roots[0]?.unmount).toHaveBeenCalledTimes(1);
      expect(page.doc.querySelectorAll(`#${TENDER_PLAY_BUTTON_ID}`)).toHaveLength(1);
      stop();
    });
  });

  describe("finding the desktop client's window", () => {
    function popupManager() {
      const callbacks: Array<() => void> = [];
      const g = window as unknown as { g_PopupManager?: unknown };
      const original = g.g_PopupManager;
      g.g_PopupManager = {
        AddPopupCreatedCallback: (cb: () => void) => callbacks.push(cb),
        AddPopupDestroyedCallback: (cb: () => void) => callbacks.push(cb),
      };
      return {
        fire: () => {
          for (const cb of callbacks) cb();
        },
        callbacks,
        restore: () => {
          g.g_PopupManager = original;
        },
      };
    }

    it("attaches to the window it finds, and detaches and gives the page back once that window closes", () => {
      const popups = popupManager();
      try {
        const page = gamePage();
        const client = desktopClient(page.doc);
        navigateTo("/library/app/50000");
        vi.spyOn(rommAppIds, "isRomMAppId").mockReturnValue(true);
        const findWindow = vi.spyOn(desktopWin, "findDesktopWindow").mockReturnValue(client.win);

        const stop = startDesktopNavigationWatcher({});
        client.flush();
        expect(page.playBar.style.position).toBe("sticky");

        (client.win as { closed?: boolean }).closed = true;
        findWindow.mockReturnValue(undefined);
        popups.fire();

        expect(page.playBar.style.position).toBe("");
        expect(client.observing()).toBe(false);
        stop();
      } finally {
        popups.restore();
      }
    });

    it("leaves nothing running on a window it could not attach to, and does not try that window again", () => {
      const popups = popupManager();
      try {
        const logError = vi.spyOn(backend, "logError").mockImplementation(() => {});
        const page = gamePage();
        let built = 0;
        const disconnect = vi.fn();
        const client = desktopClient(page.doc, {
          MutationObserver: class {
            constructor() {
              built += 1;
            }
            observe() {
              throw new Error("no observer");
            }
            disconnect = disconnect;
          },
        });
        vi.spyOn(desktopWin, "findDesktopWindow").mockReturnValue(client.win);
        const unlisten = vi.fn();
        const listen = vi.spyOn(rommAppIds, "onRomMAppIdsChanged").mockReturnValue(unlisten);

        const stop = startDesktopNavigationWatcher({});
        popups.fire();

        expect(built).toBe(1);
        expect(disconnect).toHaveBeenCalledTimes(1);
        expect(listen).not.toHaveBeenCalled();
        expect(client.frames.size).toBe(0);
        expect(logError).toHaveBeenCalledTimes(1);
        expect(logError).toHaveBeenCalledWith(expect.stringContaining("no observer"));
        stop();
      } finally {
        popups.restore();
      }
    });

    it("attaches nothing from a popup callback that fires after the supervisor stopped", () => {
      const popups = popupManager();
      try {
        const page = gamePage();
        const client = desktopClient(page.doc);
        const findWindow = vi.spyOn(desktopWin, "findDesktopWindow").mockReturnValue(undefined);

        const stop = startDesktopNavigationWatcher(PAGE);
        stop();
        findWindow.mockReturnValue(client.win);
        popups.fire();

        expect(popups.callbacks).toHaveLength(2);
        expect(client.win.requestAnimationFrame).not.toHaveBeenCalled();
        expect(page.doc.getElementById(TENDER_SUBSTITUTE_ID)).toBeNull();
      } finally {
        popups.restore();
      }
    });
  });
});
