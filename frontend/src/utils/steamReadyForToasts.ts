/**
 * Whether Steam can show a toast yet, and a wait until it can.
 *
 * A toast pushed before Steam's interface is up is lost: it is never shown, and
 * nothing says so. That is the ordinary state of a panel loaded right after
 * Steam restarts its JS context, because the backend loads the panel into the
 * new context before Steam's start is through. Measured on the device, in
 * windowed Big Picture beside Decky Loader, Steam's start goes through these in
 * order: the new context; `App.BFinishedInitBeforeLogin()`; Big Picture's main
 * window created with its document `hidden`; **Tender's panel loaded** (a toast
 * raised here was lost); that document `visible`; the login route; and last
 * `App.GetServicesInitialized()` true with the library route up, after which a
 * toast showed for its full duration.
 *
 * So a toast waits for three things, all read off Steam's own globals (the
 * spellings are in `steamui/chunk~*.js`):
 *
 * - **Steam's services are initialised** — `App.WaitForServicesInitialized()`,
 *   which resolves once `InitAfterLogin` finished, where it exists, and
 *   otherwise `App.GetServicesInitialized()` asked on every round.
 * - **The lock screen is not up** — `securitystore.IsLockScreenActive()`,
 *   which Decky Loader polls before its own toasts too
 *   (`routerHook.waitForUnlock()`).
 * - **Big Picture's main window is visible, where there is one** —
 *   `SteamUIStore.WindowStore.GamepadUIMainWindowInstance?.BrowserWindow`'s
 *   `document.visibilityState`. The desktop client has no such window — that
 *   expression is `null` there — and the condition is then met.
 *
 * Every read is defensive and none throws: a global that is absent, a shape
 * other than the one above, or a getter that throws counts as NOT yet met,
 * because a condition nothing could read is a condition nothing established.
 * The one exception is the Big Picture window's own absence under a window
 * store that answers, which is the desktop client's ordinary state.
 * That is also why the wait has a deadline: past it the caller raises the toast
 * anyway, since a toast that might be lost is better than one never raised.
 *
 * All three met in time still do not promise the toast is seen for its full
 * duration: in windowed Big Picture on the desktop it may show only
 * briefly, or not at all, after a JS-context reload, which is why the update
 * announcement also stands as a card (`docs/architecture/qam-panel.md`,
 * "Notices and homes").
 */

import { delay } from "./pacedOps";

/** How often the conditions are asked again while one is unmet. */
export const TOAST_READINESS_POLL_MS = 250;

/** How long a toast waits for Steam before it is raised whatever the conditions say. */
export const TOAST_READINESS_DEADLINE_MS = 30_000;

/** One of the three things a toast waits for. */
export type ToastCondition = "services_initialized" | "lock_screen_inactive" | "big_picture_window_visible";

/** How the wait ended. */
export interface ToastReadiness {
  /** Every condition was met before the deadline. */
  inTime: boolean;
  /** The conditions still unmet when the wait ended — empty exactly when `inTime`. */
  unmet: ToastCondition[];
}

function globalValue(name: string): unknown {
  return (globalThis as unknown as Record<string, unknown>)[name];
}

function isThenable(value: unknown): value is PromiseLike<unknown> {
  return (
    (typeof value === "object" || typeof value === "function") &&
    value !== null &&
    typeof (value as { then?: unknown }).then === "function"
  );
}

/** Call `<global>.<method>()` and give back its answer; `undefined` for anything that is not there or throws. */
function callOn(globalName: string, method: string): unknown {
  try {
    const target = globalValue(globalName);
    if (typeof target !== "object" || target === null) return undefined;
    const fn = (target as Record<string, unknown>)[method];
    return typeof fn === "function" ? (fn as () => unknown).call(target) : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Start asking whether Steam's services are initialised, and give back the
 * question to ask on every round.
 *
 * Where `App.WaitForServicesInitialized()` answers with a promise, its
 * resolution is the answer; where it is absent, throws, answers with anything
 * else, or rejects, `App.GetServicesInitialized()` is asked instead.
 */
function watchServicesInitialized(): () => boolean {
  let resolved = false;
  let askTheGetter = true;
  const waiting = callOn("App", "WaitForServicesInitialized");
  if (isThenable(waiting)) {
    askTheGetter = false;
    waiting.then(
      () => {
        resolved = true;
      },
      () => {
        askTheGetter = true;
      },
    );
  }
  return () => resolved || (askTheGetter && callOn("App", "GetServicesInitialized") === true);
}

function lockScreenInactive(): boolean {
  return callOn("securitystore", "IsLockScreenActive") === false;
}

function bigPictureWindowVisible(): boolean {
  try {
    const store = globalValue("SteamUIStore") as { WindowStore?: unknown } | null | undefined;
    const windowStore = store?.WindowStore as { GamepadUIMainWindowInstance?: unknown } | null | undefined;
    if (typeof windowStore !== "object" || windowStore === null) return false;
    const instance = windowStore.GamepadUIMainWindowInstance as { BrowserWindow?: unknown } | null | undefined;
    const browserWindow = instance?.BrowserWindow as { document?: { visibilityState?: unknown } } | null | undefined;
    if (browserWindow === null || browserWindow === undefined) return true;
    return browserWindow.document?.visibilityState === "visible";
  } catch {
    return false;
  }
}

/** Which conditions are unmet this round, in a fixed order. */
function unmetConditions(servicesInitialized: () => boolean): ToastCondition[] {
  const unmet: ToastCondition[] = [];
  if (!servicesInitialized()) unmet.push("services_initialized");
  if (!lockScreenInactive()) unmet.push("lock_screen_inactive");
  if (!bigPictureWindowVisible()) unmet.push("big_picture_window_visible");
  return unmet;
}

/**
 * Wait until Steam can show a toast, or until {@link TOAST_READINESS_DEADLINE_MS}
 * has passed. Never rejects; the answer says which of the two ended it.
 */
export async function waitUntilSteamCanShowToasts(): Promise<ToastReadiness> {
  const deadline = Date.now() + TOAST_READINESS_DEADLINE_MS;
  const servicesInitialized = watchServicesInitialized();
  for (;;) {
    const unmet = unmetConditions(servicesInitialized);
    if (unmet.length === 0) return { inTime: true, unmet };
    if (Date.now() >= deadline) return { inTime: false, unmet };
    await delay(TOAST_READINESS_POLL_MS);
  }
}
