/**
 * The wide pages' wrapper on Steam's QAM host — the half that is decidable
 * without Steam: which fiber is the host, what one render of it is widened to,
 * and what the console is told when that does not happen. Reaching Steam — Big
 * Picture's document, the live fibers, the patcher — is
 * `installQamWidthPatch.ts`, which no test can drive.
 *
 * The mechanism is `docs/architecture/qam-panel.md`, "Two widths".
 */

import { cloneElement, isValidElement, type ReactElement } from "react";

/** One node of React's fiber tree around Steam's QAM, reduced to what the install reads and writes. */
export interface QamFiber {
  type?: unknown;
  readonly elementType?: unknown;
  readonly alternate?: QamFiber | null;
  readonly memoizedProps?: unknown;
}

/**
 * A `React.memo` with neither a comparator nor default props, which React
 * mounts as a simple one: the fiber carries the memo's own render function as
 * its `type`, where any other memo leaves the memo object there.
 */
export interface SimpleMemo {
  type: (...args: unknown[]) => unknown;
}

/** What one install answers: the host wrapped, or why nothing was. */
export type QamWidthInstall =
  | "wrapped"
  | "no-big-picture-window"
  | "class-map-incomplete"
  | "no-friends-ui-store"
  | "no-placeholder"
  | "no-host"
  | "host-not-simple-memo";

/** Is *fiber* Steam's QAM host? Its props carry both `active` and `ModalManager`. */
export function isQamHost(fiber: QamFiber): boolean {
  const props = fiber.memoizedProps;
  return typeof props === "object" && props !== null && "active" in props && "ModalManager" in props;
}

/** Is *memo* the memo *host* is mounted from as a simple one? */
function isSimpleMemoOf(memo: unknown, host: QamFiber): memo is SimpleMemo {
  if (typeof memo !== "object" || memo === null) return false;
  const render = (memo as { type?: unknown }).type;
  return typeof render === "function" && host.type === render;
}

/** The memos already wrapped. */
const wrappedHosts = new WeakSet<object>();

/**
 * Wrap the first of *hosts* that is mounted as a simple memo — one entry per
 * placeholder, `undefined` where no host stands above it — through *patch*,
 * once per memo, and make the mounted fiber and its alternate render through
 * what *patch* put on the memo.
 *
 * The adoption is there because React carries the memo's resolved render
 * function on the fiber, so a patch on the memo alone reaches the next mount
 * and nothing already on screen; `qam/installEntry.tsx` adopts its menu for the
 * same reason.
 */
export function wrapFirstQamHost(
  hosts: readonly (QamFiber | undefined)[],
  patch: (memo: SimpleMemo) => void,
): QamWidthInstall {
  if (hosts.length === 0) return "no-placeholder";
  let refusal: QamWidthInstall = "no-host";
  for (const host of hosts) {
    if (!host) continue;
    const memo = host.elementType;
    if (typeof memo === "object" && memo !== null && wrappedHosts.has(memo)) return "wrapped";
    if (!isSimpleMemoOf(memo, host)) {
      refusal = "host-not-simple-memo";
      continue;
    }
    patch(memo);
    wrappedHosts.add(memo);
    host.type = memo.type;
    if (host.alternate) host.alternate.type = memo.type;
    return "wrapped";
  }
  return refusal;
}

/** What the console has been told this load. */
type Note = "install" | "no-view" | "threw";

const noted = new Set<Note>();

function noteOnce(note: Note, line: string): void {
  if (noted.has(note)) return;
  noted.add(note);
  console.warn(`[Tender] the wide pages stay at Steam's narrow width: ${line}`);
}

/** The child the QAM's view is laid over: it is handed the view itself, whose bounds follow it. */
const carriesTheView = (child: unknown): child is ReactElement<{ className: string }> =>
  isValidElement(child) &&
  typeof (child.props as { browser?: { SetBounds?: unknown } }).browser?.SetBounds === "function" &&
  typeof (child.props as { className?: unknown }).className === "string";

/**
 * *rendered* with *expandedClass* on the element the QAM's view is laid over,
 * or *rendered* itself where that element already carries it — or `undefined`
 * where *rendered* carries no such element.
 */
function widened(rendered: unknown, expandedClass: string): ReactElement | undefined {
  if (!isValidElement(rendered)) return undefined;
  const children = (rendered.props as { children?: unknown }).children;
  if (!Array.isArray(children)) return undefined;
  const index = children.findIndex(carriesTheView);
  if (index === -1) return undefined;
  const view = children[index] as ReactElement<{ className: string }>;
  if (view.props.className.split(/\s+/).includes(expandedClass)) return rendered;
  const next = [...children];
  next[index] = cloneElement(view, { className: `${view.props.className} ${expandedClass}` });
  return cloneElement(rendered as ReactElement<{ children?: unknown }>, { children: next });
}

/**
 * The handler `afterPatch` runs after each render of the QAM host: Steam's
 * result widened while *isExpanded* answers true at that render, and Steam's
 * result itself otherwise.
 *
 * It calls no hooks: it joins a host that is already mounted, and a hook it
 * added would change how many hooks that host calls from one render to the
 * next, which React refuses. And it answers with Steam's result where its own
 * work throws, because `afterPatch` does not catch a throwing handler and
 * Steam's render of the QAM would end with it.
 */
export const widenWhileExpanded =
  (isExpanded: () => boolean, expandedClass: string) =>
  (_args: unknown[], rendered: unknown): unknown => {
    try {
      if (!isExpanded()) return rendered;
      const result = widened(rendered, expandedClass);
      if (result !== undefined) return result;
      noteOnce("no-view", "the QAM host rendered no element its view is laid over");
      return rendered;
    } catch (error) {
      noteOnce("threw", `the wrapper on the QAM host threw: ${String(error)}`);
      return rendered;
    }
  };

/**
 * Run *install*, and tell the console once per load where it wrapped nothing,
 * a throw included — but not where Big Picture's window was not found, which
 * is an ordinary state rather than a fault. A page that widens again tries the
 * install again, so the line is once and the attempt is not.
 */
export function tryQamWidthInstall(install: () => QamWidthInstall): void {
  let answer: string;
  try {
    answer = install();
  } catch (error) {
    answer = `the install threw: ${String(error)}`;
  }
  if (answer !== "wrapped" && answer !== "no-big-picture-window") {
    noteOnce("install", `the width patch wrapped nothing (${answer})`);
  }
}
