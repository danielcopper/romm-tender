import { describe, it, expect, vi, beforeEach } from "vitest";
import { DomRestorationLedger } from "./restorationLedger";
import type { Root } from "react-dom/client";
import * as backend from "../../api/backend";

describe("DomRestorationLedger", () => {
  let ledger: DomRestorationLedger;

  beforeEach(() => {
    ledger = new DomRestorationLedger();
  });

  describe("hide and restore", () => {
    it("hides element and restores original display", () => {
      const el = document.createElement("div");
      el.style.display = "flex";
      document.body.appendChild(el);

      ledger.hide(el);
      expect(el.style.display).toBe("none");
      expect(ledger.isEmpty()).toBe(false);

      ledger.restoreAll();
      expect(el.style.display).toBe("flex");
      expect(ledger.isEmpty()).toBe(true);

      el.remove();
    });

    it("handles elements with no initial display style", () => {
      const el = document.createElement("div");
      document.body.appendChild(el);

      ledger.hide(el);
      expect(el.style.display).toBe("none");

      ledger.restoreAll();
      expect(el.style.display).toBe("");

      el.remove();
    });

    it("keeps the display from before the first hide through a later one", () => {
      const el = document.createElement("div");
      el.style.display = "flex";
      document.body.appendChild(el);

      ledger.hide(el);
      ledger.hide(el);
      ledger.restoreAll();

      expect(el.style.display).toBe("flex");
      el.remove();
    });
  });

  describe("style mutations and restore", () => {
    it("records original styles and restores them accurately", () => {
      const el = document.createElement("div");
      el.style.position = "relative";
      el.style.backgroundColor = "rgb(0, 0, 0)";
      document.body.appendChild(el);

      ledger.style(el, "position", "sticky");
      ledger.style(el, "top", "0px");
      ledger.style(el, "background-color", "rgb(255, 255, 255)");

      expect(el.style.position).toBe("sticky");
      expect(el.style.top).toBe("0px");
      expect(el.style.backgroundColor).toBe("rgb(255, 255, 255)");

      ledger.restoreAll();

      expect(el.style.position).toBe("relative");
      expect(el.style.top).toBe("");
      expect(el.style.backgroundColor).toBe("rgb(0, 0, 0)");

      el.remove();
    });

    it("logs an element whose styles throw on restore, and still restores the next one", () => {
      const logError = vi.spyOn(backend, "logError").mockImplementation(() => {});
      const broken = document.createElement("div");
      const next = document.createElement("div");
      document.body.append(broken, next);
      ledger.style(broken, "z-index", "10");
      ledger.style(next, "z-index", "10");
      vi.spyOn(broken.style, "removeProperty").mockImplementation(() => {
        throw new Error("style refused");
      });

      expect(() => ledger.restoreAll()).not.toThrow();

      expect(next.style.zIndex).toBe("");
      expect(logError).toHaveBeenCalledWith(expect.stringContaining("style refused"));
      broken.remove();
      next.remove();
    });

    it("preserves earliest recorded value across multiple style calls", () => {
      const el = document.createElement("div");
      el.style.zIndex = "5";
      document.body.appendChild(el);

      ledger.style(el, "z-index", "10");
      ledger.style(el, "z-index", "20");
      expect(el.style.zIndex).toBe("20");

      ledger.restoreAll();
      expect(el.style.zIndex).toBe("5");

      el.remove();
    });
  });

  describe("React roots lifecycle", () => {
    it("unmounts root and removes host container on restoreAll", () => {
      const host = document.createElement("div");
      document.body.appendChild(host);

      const unmountMock = vi.fn();
      const mockRoot = { unmount: unmountMock } as unknown as Root;

      ledger.recordRoot(mockRoot, host);
      expect(host.isConnected).toBe(true);

      ledger.restoreAll();

      expect(unmountMock).toHaveBeenCalled();
      expect(host.isConnected).toBe(false);
    });

    it("logs a root that throws on unmount and still removes its host", () => {
      const logError = vi.spyOn(backend, "logError").mockImplementation(() => {});
      const host = document.createElement("div");
      document.body.appendChild(host);

      const mockRoot = {
        unmount: vi.fn(() => {
          throw new Error("Unmount error");
        }),
      } as unknown as Root;

      ledger.recordRoot(mockRoot, host);
      expect(() => ledger.restoreAll()).not.toThrow();
      expect(host.isConnected).toBe(false);
      expect(logError).toHaveBeenCalledWith(expect.stringContaining("Unmount error"));
    });

    it("releases one root by its host and keeps the others", () => {
      const released = document.createElement("div");
      const kept = document.createElement("div");
      document.body.append(released, kept);
      const releasedRoot = { unmount: vi.fn() } as unknown as Root;
      const keptRoot = { unmount: vi.fn() } as unknown as Root;
      ledger.recordRoot(releasedRoot, released);
      ledger.recordRoot(keptRoot, kept);

      ledger.releaseRoot(released);

      expect(releasedRoot.unmount).toHaveBeenCalledTimes(1);
      expect(released.isConnected).toBe(false);
      expect(keptRoot.unmount).not.toHaveBeenCalled();
      expect(kept.isConnected).toBe(true);

      ledger.restoreAll();
      expect(releasedRoot.unmount).toHaveBeenCalledTimes(1);
      expect(kept.isConnected).toBe(false);
    });
  });

  describe("event listeners lifecycle", () => {
    it("attaches listener and removes it on restoreAll", () => {
      const target = document.createElement("div");
      const listener = vi.fn();

      ledger.addListener(target, "scroll", listener);
      target.dispatchEvent(new Event("scroll"));
      expect(listener).toHaveBeenCalledTimes(1);

      ledger.restoreAll();
      target.dispatchEvent(new Event("scroll"));
      expect(listener).toHaveBeenCalledTimes(1);
    });

    it("allows removing listener explicitly", () => {
      const target = document.createElement("div");
      const listener = vi.fn();

      ledger.addListener(target, "scroll", listener);
      ledger.removeListener(target, "scroll", listener);

      target.dispatchEvent(new Event("scroll"));
      expect(listener).not.toHaveBeenCalled();
    });
  });
});
