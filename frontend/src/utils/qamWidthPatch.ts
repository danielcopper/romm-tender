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

/** What wrapping the first wrappable host answers: wrapped, or why nothing was. */
export type QamHostWrap = "wrapped" | "no-placeholder" | "no-host" | "host-not-simple-memo";

/** What one install answers: the host wrapped, or why nothing was. */
export type QamWidthInstall = QamHostWrap | "no-big-picture-window" | "class-map-incomplete" | "no-friends-ui-store";

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
 */
export function wrapFirstQamHost(
  hosts: readonly (QamFiber | undefined)[],
  patch: (memo: SimpleMemo) => void,
): QamHostWrap {
  if (hosts.length === 0) return "no-placeholder";
  let refusal: QamHostWrap = "no-host";
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

/** Where the wrapper's or the install's own work threw, and what was thrown. */
type ThrowKey = `threw:${"wrapper" | "install"}:${string}`;

/** One case the console is told about, with its reason. */
type NoteKey = `render:${RenderMiss}` | `install:${Exclude<QamWidthInstall, "wrapped">}` | ThrowKey;

/** The keys the console has been told this load. */
const noted = new Set<NoteKey>();

function noteOnce(key: NoteKey, line: string, ...error: [] | [unknown]): void {
  if (noted.has(key)) return;
  noted.add(key);
  console.warn(`[Tender] the wide pages stay at Steam's narrow width: ${line}`, ...error);
}

/**
 * The key of a throw at *site*: what was thrown, told apart by its name and
 * message, read so that the reading cannot throw out of the catch it runs in.
 * A value whose name or message cannot be read is told by that alone.
 */
function throwKey(site: "wrapper" | "install", thrown: unknown): ThrowKey {
  try {
    if ((typeof thrown !== "object" && typeof thrown !== "function") || thrown === null) {
      return `threw:${site}:${typeof thrown} ${String(thrown)}`;
    }
    const { name, message } = thrown as { name?: unknown; message?: unknown };
    const read = (part: unknown): string => (typeof part === "string" ? part : typeof part);
    return `threw:${site}:${read(name)}: ${read(message)}`;
  } catch {
    return `threw:${site}:unreadable`;
  }
}

/** Which part of one render of the QAM host was not as the wrapper expects it. */
type RenderMiss = "not-an-element" | "children-not-an-array" | "no-child-handed-the-view" | "view-without-a-class";

/** The child the QAM's view is laid over: it is handed the view itself, whose bounds follow it. */
const carriesTheView = (child: unknown): child is ReactElement<{ className?: unknown }> =>
  isValidElement(child) &&
  typeof (child.props as { browser?: { SetBounds?: unknown } }).browser?.SetBounds === "function";

/**
 * *rendered* with *expandedClass* on the element the QAM's view is laid over,
 * or *rendered* itself where that element already carries it — or which part
 * of *rendered* was not as expected.
 */
function widened(rendered: unknown, expandedClass: string): ReactElement | RenderMiss {
  if (!isValidElement(rendered)) return "not-an-element";
  const children = (rendered.props as { children?: unknown }).children;
  if (!Array.isArray(children)) return "children-not-an-array";
  const index = children.findIndex(carriesTheView);
  if (index === -1) return "no-child-handed-the-view";
  const view = children[index] as ReactElement<{ className?: unknown }>;
  const className = view.props.className;
  if (typeof className !== "string") return "view-without-a-class";
  if (className.split(/\s+/).includes(expandedClass)) return rendered;
  const next = [...children];
  next[index] = cloneElement(view, { className: `${className} ${expandedClass}` });
  return cloneElement(rendered as ReactElement<{ children?: unknown }>, { children: next });
}

/**
 * The handler `afterPatch` runs after each render of the QAM host: Steam's
 * result widened while *isExpanded* answers true at that render, and Steam's
 * result itself otherwise.
 *
 * It calls no hooks ("Two widths" says why), and it catches its own throw,
 * because `afterPatch` does not catch a throwing handler and Steam's render of
 * the QAM would end with it.
 */
export const widenWhileExpanded =
  (isExpanded: () => boolean, expandedClass: string) =>
  (_args: unknown[], rendered: unknown): unknown => {
    try {
      if (!isExpanded()) return rendered;
      const result = widened(rendered, expandedClass);
      if (typeof result !== "string") return result;
      noteOnce(`render:${result}`, `the QAM host rendered no element to widen (${result})`);
      return rendered;
    } catch (error) {
      noteOnce(throwKey("wrapper", error), "the wrapper on the QAM host threw", error);
      return rendered;
    }
  };

/**
 * Run *install*, catching its throw, and tell the console where it wrapped
 * nothing; which lines, and how often, is in "Two widths".
 */
export function tryQamWidthInstall(install: () => QamWidthInstall): void {
  let answer: QamWidthInstall;
  try {
    answer = install();
  } catch (error) {
    noteOnce(throwKey("install", error), "the install of the wrapper on the QAM host threw", error);
    return;
  }
  if (answer !== "wrapped") noteOnce(`install:${answer}`, `the width patch wrapped nothing (${answer})`);
}
