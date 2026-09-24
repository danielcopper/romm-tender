/**
 * What the panel used to get from `@decky/api`, from Tender's own host instead.
 *
 * Five names, so no call site changes meaning: `callable`, `addEventListener`,
 * `removeEventListener`, `toaster` and `definePlugin`. Three of them are the
 * wire — they go over the WebSocket in `hostSocket.ts`. **Two of them reach no
 * socket at all**, and they live here anyway because this module replaces one
 * import specifier with another: making the reader distinguish would put two
 * imports at every call site for a distinction the call sites do not have.
 *
 * ## The two that are not the wire
 *
 * `definePlugin` answers with the factory unchanged and calls nothing, so it
 * opens no socket; it sits beside the three because a call site importing it
 * asks for the same thing the others answer.
 *
 * `toaster` was Decky Loader's own — `@decky/api` only forwarded it
 * (`api.toaster`). There is no host answer for it, so it gets a replacement of
 * Tender's own rather than a backend route: it pushes through Steam's own
 * notification store (`utils/steamToaster.tsx`).
 *
 * **It does not reach Decky Loader's API when one is running.** Why, once:
 * `docs/architecture/frontend-bundles.md`.
 *
 * ## The types
 *
 * Written from what this project's call sites actually require, not copied from
 * upstream's declarations. A copied declaration would carry upstream's licence
 * for no benefit, and a derived one describes what we use rather than what they
 * offer — so when a call site needs a field that is not here, the compiler says
 * so, which is a better conversation than inheriting fields nobody reads.
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
 * `subtext` by the cleanup summary, `duration` by the launch prompts. Upstream
 * declares fourteen.
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

/** Raises toasts under the plugin's name. */
export interface Toaster {
  toast(toast: ToastData): ToastNotification;
}

/** What `definePlugin`'s factory answers with — the panel, and its teardown. */
export interface Plugin {
  /** The entry's title, which Steam files the panel under. */
  name: string;
  /** Drawn in the Quick Access tab strip. */
  icon: ReactNode;
  /** Drawn in the panel below the strip. */
  content?: ReactNode;
  /**
   * Decky Loader's word for "render this panel even while another tab is
   * active". **Nothing reads it since the panel stopped being a Decky plugin**:
   * behind Tender's own entry, whether an unselected tab's panel stays mounted
   * is Steam's tab group's decision and there is no flag to ask it with. It
   * stays because it records what a page may still rely on — `qamExpansion.ts`
   * is written against a panel that can render while its tab is not active —
   * and deleting it would delete the question with it.
   */
  alwaysRender?: boolean;
  /**
   * Decky Loader's teardown hook. **Nothing calls it for the same reason**: a
   * JS-context rebuild is what ends this panel, and it takes the whole context
   * rather than unloading anything. The panel's own suite calls it to exercise
   * the teardown paths it registers.
   */
  onDismount?(): void;
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
 * to agree with every backend callable's parameter names, and those are an
 * implementation detail on that side.
 */
export const callable =
  <Args extends unknown[] = [], Return = void>(route: string) =>
  (...args: Args): Promise<Return> =>
    socket().call(route, args) as Promise<Return>;

/**
 * Subscribe to a backend event, and answer with the listener unchanged.
 *
 * Returning it is what lets a caller hand the same reference straight to
 * `removeEventListener`, which is how every teardown in `index.tsx` is written.
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
 * name is upstream's contract and the declaration is all of it that belongs
 * here.
 */
export const definePlugin = (fn: () => Plugin): (() => Plugin) => fn;

// -- the one that reaches Steam instead ---------------------------------------

/**
 * Raises toasts through Steam's own notification store — the popup window, the
 * queue behind it, the sound and the Quick Access entry are all Steam's.
 *
 * Everything about how that is done, including what happens when the lookups
 * behind it come back empty, lives in `utils/steamToaster.tsx`.
 */
export const toaster: Toaster = steamToaster;
