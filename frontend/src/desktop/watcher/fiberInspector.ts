/**
 * Read-only React Fiber inspection for DOM elements of Steam's desktop client
 * (docs/architecture/desktop-dom-architecture.md, "Fiber, read-only").
 */

export interface MinimalFiber {
  readonly type?: unknown;
  readonly memoizedProps?: Readonly<Record<string, unknown>> | null;
  readonly return?: MinimalFiber | null;
  readonly child?: MinimalFiber | null;
  readonly sibling?: MinimalFiber | null;
}

/**
 * Locate the React Fiber node attached to a DOM element.
 */
export function getFiberFromDom(el: Element | null | undefined): MinimalFiber | null {
  if (!el || typeof el !== "object") return null;
  const key = Object.keys(el).find((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$"));
  return key ? ((el as unknown as Record<string, unknown>)[key] as MinimalFiber) : null;
}

/**
 * Resolve display name or constructor name from a fiber's `type` field.
 */
export function resolveFiberTypeName(type: unknown): string | null {
  if (!type) return null;
  if (typeof type === "string") return type;
  if (typeof type === "function") return (type as { displayName?: string }).displayName || type.name || null;
  if (typeof type === "object") {
    const obj = type as { displayName?: string; name?: string; type?: unknown };
    if (obj.displayName) return obj.displayName;
    if (obj.name) return obj.name;
    if (obj.type) return resolveFiberTypeName(obj.type);
  }
  return null;
}

/**
 * Walk up the fiber chain from a starting fiber to locate the nearest React component name.
 * Skips lowercase HTML tag names like 'div', 'span', 'button'.
 */
export function getFiberComponentName(fiber: MinimalFiber | null, maxDepth = 15): string | null {
  let curr = fiber;
  let depth = 0;
  while (curr && depth < maxDepth) {
    const name = resolveFiberTypeName(curr.type);
    if (name && typeof name === "string" && !/^[a-z0-9-]+$/.test(name)) {
      return name;
    }
    curr = curr.return ?? null;
    depth++;
  }
  return null;
}

/**
 * Resolve the nearest React component name from a DOM element.
 */
export function getFiberDisplayName(el: Element | null | undefined): string | null {
  const fiber = getFiberFromDom(el);
  return getFiberComponentName(fiber);
}

/**
 * Walk up a fiber tree to find an ancestor matching a predicate.
 */
export function findAncestorFiber(
  fiber: MinimalFiber | null,
  predicate: (f: MinimalFiber) => boolean,
  maxDepth = 25,
): MinimalFiber | null {
  let curr = fiber;
  let depth = 0;
  while (curr && depth < maxDepth) {
    if (predicate(curr)) return curr;
    curr = curr.return ?? null;
    depth++;
  }
  return null;
}

/**
 * Extract a Steam appId from an element's fiber tree, for when the route yields none.
 * Inspects `overview.appid`, `appId`, or `details.appid` on ancestor props.
 */
export function getAppIdFromFiber(el: Element | null | undefined): number | null {
  const fiber = getFiberFromDom(el);
  if (!fiber) return null;

  const found = findAncestorFiber(fiber, (f) => {
    const props = f.memoizedProps;
    if (!props) return false;
    if (typeof props.appId === "number") return true;
    const ov = props.overview;
    if (ov && typeof ov === "object" && typeof (ov as { appid?: unknown }).appid === "number") return true;
    const det = props.details;
    if (det && typeof det === "object" && typeof (det as { appid?: unknown }).appid === "number") return true;
    return false;
  });

  if (!found?.memoizedProps) return null;
  const p = found.memoizedProps;
  if (typeof p.appId === "number") return p.appId;
  if (typeof p.appid === "number") return p.appid;
  const ov = p.overview;
  if (ov && typeof ov === "object") {
    const ovAppId = (ov as { appid?: unknown }).appid;
    if (typeof ovAppId === "number") return ovAppId;
  }
  const det = p.details;
  if (det && typeof det === "object") {
    const detAppId = (det as { appid?: unknown }).appid;
    if (typeof detAppId === "number") return detAppId;
  }

  return null;
}
