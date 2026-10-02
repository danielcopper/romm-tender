/**
 * Whether the Quick Access tab a wide page sits in is the menu's active one.
 *
 * `useWideQamPanel` works this out from inside the page's own tree and
 * publishes it here, for a reader above the page. `true` wherever no wide page
 * has answered — the default that hook takes where the question cannot be
 * asked, for the same reason.
 */

import { useSyncExternalStore } from "react";

let _active = true;
let _listeners: Array<() => void> = [];

export function setOwningQamTabActive(active: boolean): void {
  if (active === _active) return;
  _active = active;
  _listeners.forEach((fn) => fn());
}

export function getOwningQamTabActive(): boolean {
  return _active;
}

function onOwningQamTabChange(fn: () => void): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}

/** Subscribe to the answer from a component. */
export function useOwningQamTabActive(): boolean {
  return useSyncExternalStore(onOwningQamTabChange, getOwningQamTabActive);
}
