/**
 * The one module the panel imports for what it gets from its host: `endpoint`,
 * `addEventListener`, `removeEventListener`, `toaster` and `definePanel`.
 * Three of them are the wire — they go over the WebSocket in `hostSocket.ts`.
 * **Two of them reach no socket at all**, and they live here anyway: `index.tsx`
 * takes both kinds, and splitting the module would give it two imports for a
 * distinction it does not have.
 *
 * ## The two that are not the wire
 *
 * `definePanel` answers with the factory unchanged and calls nothing, so it
 * opens no socket; it sits beside the three because a call site importing it
 * asks for the same thing the others answer.
 *
 * `toaster` has no host answer, so it is Tender's own rather than a backend
 * route: it pushes through Steam's own notification store
 * (`utils/steamToaster.tsx`).
 *
 * **It does not reach Decky Loader's API when one is running.** Why, once:
 * `docs/architecture/frontend-bundles.md`.
 *
 * ## The types
 *
 * Written from what this project's call sites actually require, not copied from
 * another library's declarations. A copied declaration would carry that
 * library's licence for no benefit, and a derived one describes what we use
 * rather than what it offers — so when a call site needs a field that is not
 * here, the compiler says so, which is a better conversation than inheriting
 * fields nobody reads.
 */

import type { ReactNode } from "react";

import { steamToaster } from "../utils/steamToaster";
import { HostSocket, addressFromBundleUrl } from "./hostSocket";

export { HostTransportError } from "./hostSocket";

// -- the types the call sites name --------------------------------------------

/**
 * What a toast carries.
 *
 * Four fields, because four are passed: `title` and `body` by `showToast`,
 * `subtext` by a call site whose detail must stay readable, `duration` by one
 * that needs its own time on screen.
 */
export interface ToastData {
  title: ReactNode;
  body: ReactNode;
  subtext?: ReactNode;
  /** Milliseconds the popup stays up. */
  duration?: number;
}

/** A raised toast. No call site reads it; `showToast` declares it as its return. */
export interface ToastNotification {
  data: ToastData;
  dismiss: () => void;
}

/** Raises toasts under Tender's name. */
export interface Toaster {
  toast(toast: ToastData): ToastNotification;
}

/** What `definePanel`'s factory answers with — the panel's definition: its name, its glyph and the panel itself. */
export interface PanelDefinition {
  /** The entry's title, which Steam files the panel under. */
  name: string;
  /** Drawn in the Quick Access tab strip. */
  icon: ReactNode;
  /** Drawn in the panel below the strip. */
  content?: ReactNode;
}

// -- the connection -----------------------------------------------------------

let connection: HostSocket | null = null;

/**
 * The one socket, opened on first use.
 *
 * Lazy rather than opened at module scope so that importing this module has no
 * effect: the address is read off `import.meta.url`, and a module that read it
 * eagerly could not be imported anywhere that URL is not a real one.
 */
function socket(): HostSocket {
  connection ??= new HostSocket({
    address: () => addressFromBundleUrl(import.meta.url),
    open: (url) => new WebSocket(url),
    // One identity per bundle instance, travelling in the upgrade address. It is
    // the caller's own and not the backend's, which is what lets the host decide
    // at connection time whether a new connection is this panel reconnecting or
    // a leftover of an older one — a backend-assigned identity would be new per
    // connection and would measure nothing.
    sessionId: crypto.randomUUID(),
  });
  return connection;
}

// -- the three that are the wire, and the one that sits with them --------------

/**
 * Declare one backend method, and answer with a function that calls it.
 *
 * Arguments are positional, which is the wire's shape: a named form would have
 * to agree with every endpoint's parameter names, and those are an
 * implementation detail on that side.
 */
export const endpoint =
  <Args extends unknown[] = [], Return = void>(route: string) =>
  (...args: Args): Promise<Return> =>
    socket().call(route, args) as Promise<Return>;

/**
 * Subscribe to a backend event, and answer with the listener unchanged.
 *
 * Returning it is what lets a caller hand the same reference straight to
 * `removeEventListener`, which is how a view that subscribes while it is
 * mounted unsubscribes when it unmounts.
 */
export const addEventListener = <Payload = unknown>(
  event: string,
  listener: (payload: Payload) => unknown,
): ((payload: Payload) => unknown) => {
  socket().on(event, listener);
  return listener;
};

/** Drop a listener. Silent when it was never registered. */
export const removeEventListener = <Payload = unknown>(
  event: string,
  listener: (payload: Payload) => unknown,
): void => {
  socket().off(event, listener);
};

/**
 * Wrap the factory that builds the panel.
 *
 * It answers with the factory unchanged, and calling it is somebody else's job:
 * `index.tsx` hands it to `qam/installEntry.tsx`, which calls it exactly once
 * and mounts what it answers with behind Tender's own Quick Access entry. That
 * seam is where it is so this module stays the wire and reaches no view — the
 * declaration is all of it that belongs here.
 */
export const definePanel = (fn: () => PanelDefinition): (() => PanelDefinition) => fn;

// -- the one that reaches Steam instead ---------------------------------------

/**
 * Raises toasts through Steam's own notification store — the popup window, the
 * queue behind it, the sound and the Quick Access entry are all Steam's.
 *
 * Everything about how that is done, including what happens when the lookups
 * behind it come back empty, lives in `utils/steamToaster.tsx`.
 */
export const toaster: Toaster = steamToaster;
