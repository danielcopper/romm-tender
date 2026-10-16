import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  findScrollContainer,
  isPlayBarPinned,
  findInflatedHeroWrapper,
  createStickyPlayBarController,
  boundHeroOverflow,
} from "./stickyPlayBarController";
import { DomRestorationLedger } from "./restorationLedger";
import { GLASS_PLAY_BAR_BG, PINNED_PLAY_BAR_SHADOW, SOLID_PLAY_BAR_BG } from "../gameview/styles";

/**
 * Puts a ResizeObserver on `window` that keeps what it observes and reports a
 * resize only when the test says, even after it was disconnected.
 */
function mockResizeObserver() {
  const observed = new Set<Element>();
  let callback: (() => void) | undefined;
  class MockResizeObserver {
    constructor(cb: () => void) {
      callback = cb;
    }
    observe(target: Element) {
      observed.add(target);
    }
    unobserve(target: Element) {
      observed.delete(target);
    }
    disconnect() {
      observed.clear();
    }
  }
  const original = window.ResizeObserver;
  window.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;
  return {
    observed,
    report: () => callback?.(),
    restore: () => {
      window.ResizeObserver = original;
    },
  };
}

describe("stickyPlayBarController", () => {
  let ledger: DomRestorationLedger;

  beforeEach(() => {
    ledger = new DomRestorationLedger();
  });

  afterEach(() => {
    ledger.restoreAll();
  });

  describe("findScrollContainer", () => {
    it("returns element with overflowY auto or scroll", () => {
      const outer = document.createElement("div");
      outer.style.overflowY = "auto";

      const inner = document.createElement("div");
      outer.appendChild(inner);
      document.body.appendChild(outer);

      expect(findScrollContainer(inner)).toBe(outer);
      outer.remove();
    });

    it("skips non-scrollable overflow:visible elements even if scrollHeight exceeds clientHeight", () => {
      const scroller = document.createElement("div");
      scroller.style.overflowY = "scroll";

      const wrapper = document.createElement("div");
      wrapper.style.overflowY = "visible";
      Object.defineProperty(wrapper, "scrollHeight", { value: 1000, configurable: true });
      Object.defineProperty(wrapper, "clientHeight", { value: 300, configurable: true });

      const inner = document.createElement("div");
      wrapper.appendChild(inner);
      scroller.appendChild(wrapper);
      document.body.appendChild(scroller);

      expect(findScrollContainer(inner)).toBe(scroller);
      scroller.remove();
    });

    it("falls back to window if no scrollable container", () => {
      const el = document.createElement("div");
      document.body.appendChild(el);

      expect(findScrollContainer(el)).toBe(el.ownerDocument.defaultView);
      el.remove();
    });
  });

  describe("isPlayBarPinned", () => {
    it("returns true when top <= scrollerTop + 2", () => {
      const scroller = document.createElement("div");
      const playBar = document.createElement("div");

      scroller.getBoundingClientRect = () => ({ top: 100 }) as DOMRect;
      playBar.getBoundingClientRect = () => ({ top: 101 }) as DOMRect;

      expect(isPlayBarPinned(playBar, scroller)).toBe(true);
    });

    it("returns false when top > scrollerTop + 2", () => {
      const scroller = document.createElement("div");
      const playBar = document.createElement("div");

      scroller.getBoundingClientRect = () => ({ top: 100 }) as DOMRect;
      playBar.getBoundingClientRect = () => ({ top: 150 }) as DOMRect;

      expect(isPlayBarPinned(playBar, scroller)).toBe(false);
    });

    it("handles window as scroller", () => {
      const playBar = document.createElement("div");
      vi.spyOn(playBar, "getBoundingClientRect").mockReturnValue({
        top: 0,
        bottom: 50,
        left: 0,
        right: 100,
        width: 100,
        height: 50,
      } as DOMRect);

      expect(isPlayBarPinned(playBar, window)).toBe(true);
    });
  });

  describe("createStickyPlayBarController", () => {
    it("applies glass styles when unpinned and solid styles when pinned", () => {
      const scroller = document.createElement("div");
      scroller.style.overflowY = "scroll";
      document.body.appendChild(scroller);

      const playBar = document.createElement("div");
      scroller.appendChild(playBar);

      // Unpinned
      scroller.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
      playBar.getBoundingClientRect = () => ({ top: 100 }) as DOMRect;

      const controller = createStickyPlayBarController(playBar, playBar, ledger);

      expect(playBar.style.backgroundColor).toBe(GLASS_PLAY_BAR_BG);
      expect(playBar.style.boxShadow).toBe("none");

      // Pin
      playBar.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
      controller.updatePinning();

      expect(playBar.style.backgroundColor).toBe(SOLID_PLAY_BAR_BG);
      expect(playBar.style.boxShadow).toBe(PINNED_PLAY_BAR_SHADOW);
      // A pinned bar casts a shadow over the page scrolling under it.
      expect(PINNED_PLAY_BAR_SHADOW).toMatch(/rgba\([^)]*\) \d+px \d+px \d+px/);

      controller.dispose();
      ledger.restoreAll();
      scroller.remove();
    });

    it("writes nothing back from a resize reported after dispose and restore", () => {
      const resize = mockResizeObserver();
      try {
        const scroller = document.createElement("div");
        scroller.style.overflowY = "scroll";
        document.body.appendChild(scroller);
        const playBar = document.createElement("div");
        scroller.appendChild(playBar);
        scroller.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
        playBar.getBoundingClientRect = () => ({ top: 100 }) as DOMRect;

        const controller = createStickyPlayBarController(playBar, playBar, ledger);
        controller.dispose();
        ledger.restoreAll();
        resize.report();

        expect(playBar.style.position).toBe("");
        expect(playBar.style.backgroundColor).toBe("");
        scroller.remove();
      } finally {
        resize.restore();
      }
    });

    it("writes nothing back from a resize once its first pinning threw and the page was restored", () => {
      const resize = mockResizeObserver();
      try {
        const scroller = document.createElement("div");
        scroller.style.overflowY = "scroll";
        document.body.appendChild(scroller);
        const playBar = document.createElement("div");
        scroller.appendChild(playBar);
        scroller.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
        playBar.getBoundingClientRect = vi
          .fn<() => DOMRect>()
          .mockImplementationOnce(() => {
            throw new Error("a measurement broke");
          })
          .mockReturnValue({ top: 100 } as DOMRect);

        expect(() => createStickyPlayBarController(playBar, playBar, ledger)).toThrow("a measurement broke");
        ledger.restoreAll();
        resize.report();

        expect(playBar.style.position).toBe("");
        expect(playBar.style.backgroundColor).toBe("");
        expect(resize.observed.size).toBe(0);
        scroller.remove();
      } finally {
        resize.restore();
      }
    });

    it("sets no timer of its own", () => {
      const setTimeout = vi.spyOn(window, "setTimeout");
      const scroller = document.createElement("div");
      scroller.style.overflowY = "scroll";
      document.body.appendChild(scroller);
      const playBar = document.createElement("div");
      scroller.appendChild(playBar);

      const controller = createStickyPlayBarController(playBar, playBar, ledger);

      expect(setTimeout).not.toHaveBeenCalled();
      controller.dispose();
      scroller.remove();
    });

    it("resets playSection background to transparent on dispose if disposed while pinned", () => {
      const scroller = document.createElement("div");
      scroller.style.overflowY = "scroll";
      document.body.appendChild(scroller);

      const playBar = document.createElement("div");
      const playSection = document.createElement("div");
      scroller.appendChild(playBar);
      scroller.appendChild(playSection);

      scroller.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
      playBar.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;

      const controller = createStickyPlayBarController(playBar, playSection, ledger);
      expect(playSection.style.backgroundColor).toBe(SOLID_PLAY_BAR_BG);

      controller.dispose();
      expect(playSection.style.backgroundColor).toBe("transparent");

      ledger.restoreAll();
      scroller.remove();
    });

    it("observes playBar via ResizeObserver and updates pinning when layout changes", () => {
      let roCallback: (() => void) | null = null;
      class MockResizeObserver {
        constructor(cb: () => void) {
          roCallback = cb;
        }
        observe() {}
        unobserve() {}
        disconnect() {
          roCallback = null;
        }
      }
      const origRO = window.ResizeObserver;
      window.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;

      const scroller = document.createElement("div");
      scroller.style.overflowY = "scroll";
      document.body.appendChild(scroller);

      const playBar = document.createElement("div");
      scroller.appendChild(playBar);

      scroller.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
      playBar.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;

      const controller = createStickyPlayBarController(playBar, playBar, ledger);
      expect(playBar.style.backgroundColor).toBe(SOLID_PLAY_BAR_BG);

      // Layout changes (hero expands) so playBar moves down
      playBar.getBoundingClientRect = () => ({ top: 300 }) as DOMRect;
      if (typeof roCallback === "function") {
        (roCallback as () => void)();
      }

      expect(playBar.style.backgroundColor).toBe(GLASS_PLAY_BAR_BG);
      expect(playBar.style.boxShadow).toBe("none");

      controller.dispose();
      ledger.restoreAll();
      scroller.remove();
      window.ResizeObserver = origRO;
    });

    it("maintains hero wrapper overflow visible so parallax and refraction persist when unpinned and pinned", () => {
      const scroller = document.createElement("div");
      scroller.style.overflowY = "scroll";
      document.body.appendChild(scroller);

      const panel = document.createElement("div");
      scroller.appendChild(panel);

      const hero = document.createElement("div");
      hero.className = "HeroBanner";
      const canvas = document.createElement("canvas");
      hero.appendChild(canvas);
      Object.defineProperty(hero, "scrollHeight", { value: 1200, configurable: true });
      Object.defineProperty(hero, "offsetHeight", { value: 300, configurable: true });
      hero.style.overflow = "hidden"; // Simulate any pre-existing or native hidden overflow
      panel.appendChild(hero);

      const content = document.createElement("div");
      panel.appendChild(content);

      const playBar = document.createElement("div");
      content.appendChild(playBar);

      // Start unpinned: playBar top is below scroller top
      scroller.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
      playBar.getBoundingClientRect = () => ({ top: 150 }) as DOMRect;

      const controller = createStickyPlayBarController(playBar, playBar, ledger, content, panel);

      expect(hero.style.overflow).toBe("visible");

      // Scroll so playBar pins to the top; hero continues scrolling in background for remaining cards
      playBar.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
      controller.updatePinning();

      expect(hero.style.overflow).toBe("visible");

      // Scroll back up so playBar unpins
      playBar.getBoundingClientRect = () => ({ top: 150 }) as DOMRect;
      controller.updatePinning();

      expect(hero.style.overflow).toBe("visible");

      controller.dispose();
      ledger.restoreAll();
      expect(hero.style.overflow).toBe("hidden"); // Restored to pre-existing style
      scroller.remove();
    });

    it("re-applies baseline styles on updatePinning even if styles were wiped externally", () => {
      const scroller = document.createElement("div");
      scroller.style.overflowY = "scroll";
      document.body.appendChild(scroller);

      const playBar = document.createElement("div");
      scroller.appendChild(playBar);

      const controller = createStickyPlayBarController(playBar, playBar, ledger);
      expect(playBar.style.zIndex).toBe("10");
      expect(playBar.style.position).toBe("sticky");

      // Simulate external VDOM re-render wiping inline styles
      playBar.style.zIndex = "";
      playBar.style.position = "";

      controller.updatePinning();

      expect(playBar.style.zIndex).toBe("10");
      expect(playBar.style.position).toBe("sticky");

      controller.dispose();
      ledger.restoreAll();
      scroller.remove();
    });

    it("matches correctly identifies managed elements", () => {
      const playBar = document.createElement("div");
      const otherBar = document.createElement("div");
      document.body.appendChild(playBar);
      document.body.appendChild(otherBar);

      const controller = createStickyPlayBarController(playBar, playBar, ledger);

      expect(controller.matches(playBar, playBar)).toBe(true);
      expect(controller.matches(otherBar, playBar)).toBe(false);
      expect(controller.matches(playBar, otherBar)).toBe(false);

      playBar.remove();
      expect(controller.matches(playBar, playBar)).toBe(false);

      controller.dispose();
      ledger.restoreAll();
      otherBar.remove();
    });
  });

  describe("boundHeroOverflow", () => {
    // A fixture in the page's shape: a parallax layer at the top of the page,
    // scaled 2x by its matrix3d, holding the artwork and a flipped canvas that
    // runs far below it.
    function heroPage({ contentBottom, clientHeight = 617 }: { contentBottom: number; clientHeight?: number }) {
      const scroller = document.createElement("div");
      Object.defineProperty(scroller, "clientHeight", { value: clientHeight, configurable: true });
      scroller.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;

      const wrapper = document.createElement("div");
      wrapper.style.overflow = "visible";
      const header = document.createElement("div");
      header.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
      const layer = document.createElement("div");
      layer.style.transform = "matrix3d(2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1, 0, 0, 0, -1, 1)";
      Object.defineProperty(layer, "offsetParent", { value: header, configurable: true });
      Object.defineProperty(layer, "offsetTop", { value: 0, configurable: true });
      Object.defineProperty(layer, "offsetHeight", { value: 307, configurable: true });
      const mirror = document.createElement("canvas");
      mirror.style.transform = "matrix(1, 0, 0, -1, 0, 0)";
      Object.defineProperty(mirror, "offsetParent", { value: layer, configurable: true });
      Object.defineProperty(mirror, "offsetTop", { value: 369, configurable: true });
      layer.appendChild(mirror);
      header.appendChild(layer);
      wrapper.appendChild(header);

      const content = document.createElement("div");
      content.getBoundingClientRect = () => ({ bottom: contentBottom }) as DOMRect;
      scroller.appendChild(wrapper);
      scroller.appendChild(content);
      document.body.appendChild(scroller);
      return { scroller, wrapper, layer, mirror, content };
    }

    it("clips the parallax layer so its scaled overflow ends where the content does", () => {
      const page = heroPage({ contentBottom: 1405 });

      boundHeroOverflow(page.wrapper, page.scroller, page.content, ledger);

      // 2 × (307 + 395) = 1404, the last whole pixel inside 1405.
      expect(page.layer.style.overflow).toBe("clip");
      expect(page.layer.style.getPropertyValue("overflow-clip-margin")).toBe("395px");
      page.scroller.remove();
    });

    it("fades the flipped canvas out over the pixels before the clip edge", () => {
      const page = heroPage({ contentBottom: 1405 });

      boundHeroOverflow(page.wrapper, page.scroller, page.content, ledger);

      // The clip edge is 307 + 395 = 702 into the layer, 333 into the canvas.
      expect(page.mirror.style.getPropertyValue("mask-image")).toBe(
        "linear-gradient(to top, black 173px, transparent 333px)",
      );
      page.scroller.remove();
    });

    it("lets a page shorter than the window keep the artwork down to the window's bottom", () => {
      const page = heroPage({ contentBottom: 500, clientHeight: 900 });

      boundHeroOverflow(page.wrapper, page.scroller, page.content, ledger);

      expect(page.layer.style.getPropertyValue("overflow-clip-margin")).toBe("143px");
      page.scroller.remove();
    });

    it("never clips the hero wrapper, and hands every style back on restore", () => {
      const page = heroPage({ contentBottom: 1405 });

      boundHeroOverflow(page.wrapper, page.scroller, page.content, ledger);
      expect(page.wrapper.style.overflow).toBe("visible");

      ledger.restoreAll();
      expect(page.layer.style.overflow).toBe("");
      expect(page.layer.style.getPropertyValue("overflow-clip-margin")).toBe("");
      expect(page.mirror.style.getPropertyValue("mask-image")).toBe("");
      page.scroller.remove();
    });

    it("is applied by the sticky controller on every pinning update", () => {
      const page = heroPage({ contentBottom: 1405 });
      page.scroller.style.overflowY = "scroll";
      Object.defineProperty(page.wrapper, "scrollHeight", { value: 1978, configurable: true });
      Object.defineProperty(page.wrapper, "offsetHeight", { value: 307, configurable: true });
      const playBar = document.createElement("div");
      page.content.appendChild(playBar);

      const controller = createStickyPlayBarController(playBar, playBar, ledger, page.content, page.scroller);
      expect(page.layer.style.getPropertyValue("overflow-clip-margin")).toBe("395px");

      page.content.getBoundingClientRect = () => ({ bottom: 1805 }) as DOMRect;
      controller.updatePinning();
      expect(page.layer.style.getPropertyValue("overflow-clip-margin")).toBe("595px");

      controller.dispose();
      page.scroller.remove();
    });

    it("follows the content's and the parallax layer's size, which no child-list mutation reports", () => {
      const resize = mockResizeObserver();
      try {
        const page = heroPage({ contentBottom: 1405 });
        page.scroller.style.overflowY = "scroll";
        Object.defineProperty(page.wrapper, "scrollHeight", { value: 1978, configurable: true });
        Object.defineProperty(page.wrapper, "offsetHeight", { value: 307, configurable: true });
        const playBar = document.createElement("div");
        page.content.appendChild(playBar);

        const controller = createStickyPlayBarController(playBar, playBar, ledger, page.content, page.scroller);
        expect(resize.observed).toContain(page.content);
        expect(resize.observed).toContain(page.layer);

        page.content.getBoundingClientRect = () => ({ bottom: 1805 }) as DOMRect;
        resize.report();
        expect(page.layer.style.getPropertyValue("overflow-clip-margin")).toBe("595px");

        controller.dispose();
        page.scroller.remove();
      } finally {
        resize.restore();
      }
    });

    it("does nothing where the hero carries no matrix3d layer", () => {
      const page = heroPage({ contentBottom: 1405 });
      page.layer.style.transform = "none";

      boundHeroOverflow(page.wrapper, page.scroller, page.content, ledger);

      expect(page.layer.style.overflow).toBe("");
      expect(page.mirror.style.getPropertyValue("mask-image")).toBe("");
      page.scroller.remove();
    });
  });

  describe("findInflatedHeroWrapper", () => {
    it("identifies hero sibling when canvas child causes scrollHeight > offsetHeight", () => {
      const scroller = document.createElement("div");
      const parent = document.createElement("div");
      scroller.appendChild(parent);

      const hero = document.createElement("div");
      const canvas = document.createElement("canvas");
      hero.appendChild(canvas);
      Object.defineProperty(hero, "scrollHeight", { value: 400, configurable: true });
      Object.defineProperty(hero, "offsetHeight", { value: 300, configurable: true });
      parent.appendChild(hero);

      const content = document.createElement("div");
      const playBar = document.createElement("div");
      content.appendChild(playBar);
      parent.appendChild(content);

      document.body.appendChild(scroller);
      try {
        const found = findInflatedHeroWrapper(content, playBar, scroller);
        expect(found).toBe(hero);
      } finally {
        scroller.remove();
      }
    });

    it("skips siblings that contain the play bar", () => {
      const scroller = document.createElement("div");
      const wrapper = document.createElement("div");
      scroller.appendChild(wrapper);

      const playBar = document.createElement("div");
      playBar.className = "PlayBar";
      wrapper.appendChild(playBar);
      Object.defineProperty(wrapper, "scrollHeight", { value: 2000, configurable: true });
      Object.defineProperty(wrapper, "offsetHeight", { value: 300, configurable: true });

      const container = document.createElement("div");
      wrapper.appendChild(container);

      document.body.appendChild(scroller);
      try {
        const result = findInflatedHeroWrapper(container, playBar, scroller);
        expect(result).toBeNull();
      } finally {
        scroller.remove();
      }
    });

    it("ignores element when scrollHeight <= offsetHeight", () => {
      const scroller = document.createElement("div");
      const panel = document.createElement("div");
      scroller.appendChild(panel);

      const hero = document.createElement("div");
      hero.className = "HeroWrapper";
      Object.defineProperty(hero, "scrollHeight", { value: 300, configurable: true });
      Object.defineProperty(hero, "offsetHeight", { value: 300, configurable: true });
      panel.appendChild(hero);

      const content = document.createElement("div");
      const playBar = document.createElement("div");
      content.appendChild(playBar);
      panel.appendChild(content);

      document.body.appendChild(scroller);
      try {
        const result = findInflatedHeroWrapper(content, playBar, scroller);
        expect(result).toBeNull();
      } finally {
        scroller.remove();
      }
    });

    it("returns null when no matching sibling exists", () => {
      const scroller = document.createElement("div");
      const panel = document.createElement("div");
      scroller.appendChild(panel);

      const content = document.createElement("div");
      const playBar = document.createElement("div");
      content.appendChild(playBar);
      panel.appendChild(content);

      document.body.appendChild(scroller);
      try {
        const result = findInflatedHeroWrapper(content, playBar, scroller);
        expect(result).toBeNull();
      } finally {
        scroller.remove();
      }
    });

    it("handles window as scroller", () => {
      const panel = document.createElement("div");
      const hero = document.createElement("div");
      hero.className = "HeroWrapper";
      Object.defineProperty(hero, "scrollHeight", { value: 1200, configurable: true });
      Object.defineProperty(hero, "offsetHeight", { value: 300, configurable: true });
      panel.appendChild(hero);

      const content = document.createElement("div");
      const playBar = document.createElement("div");
      content.appendChild(playBar);
      panel.appendChild(content);

      document.body.appendChild(panel);
      try {
        const result = findInflatedHeroWrapper(content, playBar, window);
        expect(result).toBe(hero);
      } finally {
        panel.remove();
      }
    });
  });
});
