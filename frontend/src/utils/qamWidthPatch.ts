/**
 * Widening the Quick Access panel from inside Steam's own render of it.
 *
 * Steam's QAM host — the component in Big Picture's main window that renders
 * the placeholder the QAM's view is laid over — gives that placeholder its
 * `Expanded` class only while `qamFriendsExpanded` is set AND the active QAM
 * tab is Friends (`steamui/sp.js`, both QAM renderers). A wide page sets the
 * flag on Tender's tab, so the panel stays narrow. The wrapper installed here
 * adds the class whenever the flag is set, and Steam's own layout effect then
 * moves the view to match.
 *
 * What the mechanism rests on, and why it is a render wrapper rather than an
 * edit of the placeholder in the DOM, is `docs/architecture/qam-panel.md`,
 * "Two widths".
 */

import { cloneElement, isValidElement, type ReactElement } from "react";

import { FriendsUIStore, findSP, quickAccessMenuClasses } from "./deckyUiInternals";

/** One node of React's live fiber tree, reduced to what the install reads and writes. */
export interface HostFiber {
  type?: unknown;
  readonly elementType?: unknown;
  readonly alternate?: HostFiber | null;
  readonly return?: HostFiber | null;
  readonly memoizedProps?: unknown;
}

type Render = (...args: unknown[]) => unknown;

function fiberOf(el: Element): HostFiber | undefined {
  const key = Object.keys(el).find((k) => k.startsWith("__reactFiber$"));
  return key === undefined ? undefined : ((el as unknown as Record<string, HostFiber | undefined>)[key] ?? undefined);
}

const isHostProps = (props: unknown): boolean =>
  typeof props === "object" && props !== null && "active" in props && "ModalManager" in props;

/**
 * The QAM host above *placeholder*: the nearest fiber whose props carry both
 * `active` and `ModalManager`, which is the shape Steam's QAM host is handed.
 */
export function findQamHost(placeholder: Element): HostFiber | undefined {
  for (let fiber = fiberOf(placeholder); fiber; fiber = fiber.return ?? undefined) {
    if (isHostProps(fiber.memoizedProps)) return fiber;
  }
  return undefined;
}

/** The child the QAM's view is laid over: it is handed the view itself, whose bounds follow it. */
const carriesTheView = (child: unknown): child is ReactElement<{ className: string }> =>
  isValidElement(child) &&
  typeof (child.props as { browser?: { SetBounds?: unknown } }).browser?.SetBounds === "function" &&
  typeof (child.props as { className?: unknown }).className === "string";

/**
 * One render of the QAM host with *expandedClass* added to the element the
 * QAM's view is laid over — or *rendered* itself, untouched, where that
 * element is not found or already carries the class. On the Friends tab Steam
 * sets the class itself.
 */
export function widenQamHostRender(rendered: unknown, expandedClass: string): unknown {
  if (!isValidElement(rendered)) return rendered;
  const children = (rendered.props as { children?: unknown }).children;
  if (!Array.isArray(children)) return rendered;
  const index = children.findIndex(carriesTheView);
  if (index === -1) return rendered;
  const view = children[index] as ReactElement<{ className: string }>;
  if (view.props.className.split(/\s+/).includes(expandedClass)) return rendered;
  const widened = [...children];
  widened[index] = cloneElement(view, { className: `${view.props.className} ${expandedClass}` });
  return cloneElement(rendered as ReactElement<{ children?: unknown }>, { children: widened });
}

/** The hosts already wrapped, by the `memo` they are mounted from. */
const wrappedHosts = new WeakSet<object>();

/**
 * Wrap the render of the host *fiber* is mounted from, once, and answer
 * whether it is wrapped.
 *
 * The `memo`'s own `type` is replaced, which every later mount of the host
 * renders through, and so are the mounted fiber's and its alternate's, which
 * React carries the resolved function on and would otherwise go on rendering
 * the original with.
 *
 * The wrapper calls no hooks: it is installed under a host that is already
 * mounted, and a hook it added would change how many hooks that host calls
 * from one render to the next, which React refuses. And where its own work
 * throws it answers with Steam's result unchanged: it runs inside Steam's
 * render of the QAM, which a throw of ours would end.
 */
export function wrapQamHost(fiber: HostFiber, isExpanded: () => boolean, expandedClass: string): boolean {
  const memo = fiber.elementType;
  if (typeof memo !== "object" || memo === null) return false;
  if (wrappedHosts.has(memo)) return true;
  const original = (memo as { type?: unknown }).type;
  if (typeof original !== "function" || typeof fiber.type !== "function") return false;
  const render = original as Render;
  const wrapped: Render = (...args) => {
    const rendered = render(...args);
    try {
      return isExpanded() ? widenQamHostRender(rendered, expandedClass) : rendered;
    } catch {
      return rendered;
    }
  };
  (memo as { type: unknown }).type = wrapped;
  fiber.type = wrapped;
  if (fiber.alternate) fiber.alternate.type = wrapped;
  wrappedHosts.add(memo);
  return true;
}

/**
 * Install the wrapper on the QAM host in Big Picture's main window, where it
 * is mounted. Answers whether the host is wrapped; `false` leaves a wide page
 * at Steam's narrow width.
 *
 * Nothing takes it back: the host lives until the JS context is rebuilt, which
 * takes this module with it.
 */
export function installQamWidthPatch(): boolean {
  const placeholderClass = quickAccessMenuClasses?.ViewPlaceholder;
  const expandedClass = quickAccessMenuClasses?.Expanded;
  if (!placeholderClass || !expandedClass) return false;
  const store = FriendsUIStore;
  if (!store) return false;
  const doc = findSP()?.document;
  if (!doc) return false;
  for (const placeholder of doc.getElementsByClassName(placeholderClass)) {
    const host = findQamHost(placeholder);
    if (host) return wrapQamHost(host, () => store.qamFriendsExpanded === true, expandedClass);
  }
  return false;
}
