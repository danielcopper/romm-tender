/**
 * DOM restoration ledger: records each change the desktop watcher makes to
 * Steam's page so that `restoreAll` can give it back, idempotently
 * (docs/architecture/desktop-dom-architecture.md, "The restoration ledger").
 */

import type { Root } from "react-dom/client";
import { logError } from "../../api/backend";

interface TrackedListener {
  target: EventTarget;
  type: string;
  listener: EventListenerOrEventListenerObject;
  options?: boolean | AddEventListenerOptions | undefined;
}

interface TrackedRoot {
  root: Root;
  host: HTMLElement;
}

/** Run `step`, logging what it was doing when it throws, so one failed restore does not stop the rest. */
function attempt(doing: string, step: () => void): void {
  try {
    step();
  } catch (e) {
    logError(`desktop: the restoration ledger could not ${doing}: ${String(e)}`);
  }
}

export class DomRestorationLedger {
  private readonly originalStyles = new Map<HTMLElement, Map<string, string>>();
  private readonly hiddenElements = new Map<HTMLElement, string>();
  private mountedRoots: TrackedRoot[] = [];
  private attachedListeners: TrackedListener[] = [];

  /**
   * Hide an element. The display recorded is the one it had before the first
   * hide; a later hide of the same element keeps it.
   */
  hide(el: HTMLElement | null | undefined): void {
    if (!el) return;
    if (!this.hiddenElements.has(el)) {
      this.hiddenElements.set(el, el.style.display);
    }
    if (el.style.display !== "none") {
      el.style.display = "none";
    }
  }

  /**
   * Set an inline style property, named in its CSS spelling (`margin-left`).
   * The value recorded is the one it had before the first change; a later
   * change of the same property keeps it.
   */
  style(el: HTMLElement | null | undefined, property: string, value: string): void {
    if (!el) return;
    let propMap = this.originalStyles.get(el);
    if (!propMap) {
      propMap = new Map<string, string>();
      this.originalStyles.set(el, propMap);
    }
    if (!propMap.has(property)) {
      propMap.set(property, el.style.getPropertyValue(property));
    }
    el.style.setProperty(property, value);
  }

  /** Record a mounted React root with the host element Tender created for it. */
  recordRoot(root: Root, host: HTMLElement): void {
    this.mountedRoots.push({ root, host });
  }

  /** Unmount the root recorded for `host`, if any, and remove `host`. */
  releaseRoot(host: HTMLElement): void {
    const tracked = this.mountedRoots.filter((r) => r.host === host);
    this.mountedRoots = this.mountedRoots.filter((r) => r.host !== host);
    for (const { root } of tracked) attempt("unmount a React root", () => root.unmount());
    if (host.isConnected) host.remove();
  }

  addListener(
    target: EventTarget | null | undefined,
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: boolean | AddEventListenerOptions,
  ): void {
    if (!target) return;
    target.addEventListener(type, listener, options);
    this.attachedListeners.push({ target, type, listener, options });
  }

  removeListener(
    target: EventTarget | null | undefined,
    type: string,
    listener: EventListenerOrEventListenerObject,
  ): void {
    if (!target) return;
    target.removeEventListener(type, listener);
    this.attachedListeners = this.attachedListeners.filter(
      (l) => !(l.target === target && l.type === type && l.listener === listener),
    );
  }

  /** Whether the ledger holds nothing for `restoreAll` to give back. */
  isEmpty(): boolean {
    return (
      this.originalStyles.size === 0 &&
      this.hiddenElements.size === 0 &&
      this.mountedRoots.length === 0 &&
      this.attachedListeners.length === 0
    );
  }

  /** Detach the listeners, unmount the roots and remove their hosts, then restore displays and styles. */
  restoreAll(): void {
    this.detachListeners();
    this.unmountRoots();
    this.restoreDisplays();
    this.restoreStyles();
  }

  private detachListeners(): void {
    for (const l of this.attachedListeners) {
      attempt(`remove a ${l.type} listener`, () => l.target.removeEventListener(l.type, l.listener, l.options));
    }
    this.attachedListeners = [];
  }

  private unmountRoots(): void {
    for (const { root, host } of this.mountedRoots) {
      attempt("unmount a React root", () => root.unmount());
      attempt("remove a React root's host", () => {
        if (host.isConnected) host.remove();
      });
    }
    this.mountedRoots = [];
  }

  private restoreDisplays(): void {
    for (const [el, originalDisplay] of this.hiddenElements) {
      if (!el.isConnected) continue;
      attempt("restore an element's display", () => {
        el.style.display = originalDisplay;
      });
    }
    this.hiddenElements.clear();
  }

  private restoreStyles(): void {
    for (const [el, propMap] of this.originalStyles) {
      if (!el.isConnected) continue;
      attempt("restore an element's styles", () => {
        for (const [prop, origVal] of propMap) {
          if (origVal) {
            el.style.setProperty(prop, origVal);
          } else {
            el.style.removeProperty(prop);
          }
        }
      });
    }
    this.originalStyles.clear();
  }
}
