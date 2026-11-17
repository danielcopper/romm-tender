/**
 * The wide pages' wrapper on Steam's QAM host — the half that is decidable
 * without Steam: which fiber is the host, what one render of it is widened to,
 * what the console and the plugin log are told when that does not happen, and
 * what taking the wrapper back restores. Reaching Steam — Big Picture's
 * document, the live fibers, the patcher — is `installQamWidthPatch.ts`.
 *
 * The mechanism is `docs/architecture/qam-panel.md`, "Two widths".
 */

import { cloneElement, isValidElement, type ReactElement } from "react";

import { thrownText } from "./thrownText";
import { warnWithError } from "./warnWithError";

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

/** One memo wrapped: the fiber pointed at the wrapper, the wrapper itself, and the handle that takes it off the memo. */
interface QamHostWrapped {
  readonly host: QamFiber;
  readonly wrapper: SimpleMemo["type"];
  readonly unpatch: () => void;
}

/** The memos wrapped and not yet taken back. */
const wrappedHosts = new Map<SimpleMemo, QamHostWrapped>();

/** Set by the take-back; from then on the handler hands Steam's result back untouched. */
let takenBack = false;

/**
 * Wrap the first of *hosts* that is mounted as a simple memo — one entry per
 * placeholder, `undefined` where no host stands above it — through *patch*,
 * once per memo, and make the mounted fiber and its alternate render through
 * what *patch* put on the memo. *patch* answers the handle that takes its
 * wrapper off the memo again, which `takeBackQamWidthPatch` calls.
 */
export function wrapFirstQamHost(
  hosts: readonly (QamFiber | undefined)[],
  patch: (memo: SimpleMemo) => () => void,
): QamHostWrap {
  if (hosts.length === 0) return "no-placeholder";
  let refusal: QamHostWrap = "no-host";
  for (const host of hosts) {
    if (!host) continue;
    const memo = host.elementType;
    if (typeof memo === "object" && memo !== null && wrappedHosts.has(memo as SimpleMemo)) return "wrapped";
    if (!isSimpleMemoOf(memo, host)) {
      refusal = "host-not-simple-memo";
      continue;
    }
    const unpatch = patch(memo);
    wrappedHosts.set(memo, { host, wrapper: memo.type, unpatch });
    host.type = memo.type;
    if (host.alternate) host.alternate.type = memo.type;
    return "wrapped";
  }
  return refusal;
}

/**
 * Take every wrapper `wrapFirstQamHost` put on back off its memo and off the
 * fibers holding it, *findHosts* answering the hosts on screen now, and disarm
 * the handler for the rest of this load; how, and what that leaves, is "Two
 * widths". It does not throw: a handle that throws is written once and the
 * other wrappers still come off, and pointing the fibers back stops at its
 * first throw, which is written once. Each memo is forgotten either way, so it
 * is never taken back twice.
 */
export function takeBackQamWidthPatch(findHosts: () => readonly (QamFiber | undefined)[]): void {
  takenBack = true;
  if (wrappedHosts.size === 0) return;
  const records = [...wrappedHosts];
  wrappedHosts.clear();
  for (const [, { unpatch }] of records) {
    try {
      unpatch();
    } catch (error) {
      noteOnce(
        throwKey("take-off", error),
        "the wrapper on the QAM host could not be taken off; a host still rendering through it gets Steam's render " +
          "back untouched until Steam's JS context is rebuilt",
        error,
      );
    }
  }
  const pointBack = (hosts: readonly (QamFiber | undefined)[]): void => {
    for (const fiber of hosts.flatMap((host) => (host ? [host, host.alternate] : []))) {
      const record = records.find(([, { wrapper }]) => fiber?.type === wrapper);
      if (fiber && record) fiber.type = record[0].type;
    }
  };
  try {
    pointBack(records.map(([, { host }]) => host));
    pointBack(findHosts());
  } catch (error) {
    noteOnce(
      throwKey("point-back", error),
      "the QAM hosts could not all be pointed back at Steam's render; one still on the wrapper gets Steam's render " +
        "back untouched until it is mounted again",
      error,
    );
  }
}

/** Where the wrapper's, the install's or the take-back's own work threw. */
type ThrowSite = "wrapper" | "install" | "take-off" | "point-back";

/** Where a throw happened, and what was thrown. */
type ThrowKey = `threw:${ThrowSite}:${string}`;

/** One case a line is written about, with its reason. */
type NoteKey = `render:${RenderMiss}` | `install:${Exclude<QamWidthInstall, "wrapped">}` | ThrowKey;

/** The keys written this load. */
const noted = new Set<NoteKey>();

/** The start of every line about a wide page that was not widened. */
const NARROW = "the wide pages stay at Steam's narrow width";

/** Write *line* through `warnWithError` once per load for *key*. */
function noteOnce(key: NoteKey, line: string, ...error: [] | [unknown]): void {
  if (noted.has(key)) return;
  noted.add(key);
  warnWithError(line, ...error);
}

/** The key of a throw at *site*: a different error from the same place is a different key. */
const throwKey = (site: ThrowSite, thrown: unknown): ThrowKey => `threw:${site}:${thrownText(thrown)}`;

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
 * result itself otherwise, and always once the wrapper has been taken back.
 *
 * It calls no hooks ("Two widths" says why), and it catches its own throw,
 * because `afterPatch` does not catch a throwing handler and Steam's render of
 * the QAM would end with it.
 */
export const widenWhileExpanded =
  (isExpanded: () => boolean, expandedClass: string) =>
  (_args: unknown[], rendered: unknown): unknown => {
    if (takenBack) return rendered;
    try {
      if (!isExpanded()) return rendered;
      const result = widened(rendered, expandedClass);
      if (typeof result !== "string") return result;
      noteOnce(
        `render:${result}`,
        `${NARROW}: the QAM host rendered no element to widen while the QAM's Friends flag was set (${result})`,
      );
      return rendered;
    } catch (error) {
      noteOnce(throwKey("wrapper", error), `${NARROW}: the wrapper on the QAM host threw`, error);
      return rendered;
    }
  };

/**
 * Run *install*, catching its throw, and say where it wrapped nothing; which
 * lines, where to and how often is in "Two widths".
 */
export function tryQamWidthInstall(install: () => QamWidthInstall): void {
  let answer: QamWidthInstall;
  try {
    answer = install();
  } catch (error) {
    noteOnce(throwKey("install", error), `${NARROW}: the install of the wrapper on the QAM host threw`, error);
    return;
  }
  if (answer !== "wrapped") noteOnce(`install:${answer}`, `${NARROW}: the width patch wrapped nothing (${answer})`);
}
