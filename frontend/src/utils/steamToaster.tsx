/**
 * Tender's toasts, raised through Steam's own notification store.
 *
 * A toast is one notification pushed into `NotificationStore`, which then owns
 * everything a toast does: the popup window and its animation, the queue behind
 * it, the sound, and the entry left in the Quick Access notifications tab. None
 * of that is reimplemented here and none of it can be — the popup is a native
 * window Steam opens.
 *
 * Why a missing drawing means no push, why the chain is re-checked before every
 * push rather than watched, and why none of this reaches Decky Loader's own
 * toaster: `docs/architecture/frontend-bundles.md`.
 *
 * The one thing the code below cannot state for itself is the ORDER: the check
 * at push time is enough only because a toast is drawn after it is pushed,
 * never before.
 */

import { injectFCTrampoline } from "@decky/ui";
import type { FC, PropsWithChildren, ReactNode } from "react";

import type { ToastData, ToastNotification, Toaster } from "../api/host";
import {
  ErrorBoundary,
  NotificationStore,
  ToastRenderer,
  toastClasses,
  type SteamNotification,
  type SteamNotificationGroup,
  type SteamNotificationInfo,
  type SteamNotificationStore,
  type SteamToastRenderFn,
  type SteamToastRenderProps,
  type SteamToastRenderer,
  type ToastClasses,
} from "./deckyUiInternals";
import { SteamToast } from "./steamToast";

/** How long a popup stays up when the call site names no duration. */
const DEFAULT_TOAST_DURATION_MS = 5000;

/**
 * Steam's "General" notification type.
 *
 * It has to be one Steam's own per-type table knows: the store reads
 * `Q[e.eType].displayToastAlone` unguarded while working its toast queue, so an
 * unknown type throws inside Steam's queue rather than in anything of ours.
 */
const STEAM_NOTIFICATION_TYPE_GENERAL = 31;

/** `k_Client` — the notification came from this machine, not from Steam's servers. */
const STEAM_NOTIFICATION_SOURCE_CLIENT = 1;

/** `ToastMisc`, the sound Steam plays for a notification that is nothing more specific. */
const STEAM_TOAST_SOUND_MISC = 6;

/** `ProcessNotification`'s "New" — as opposed to updating or removing one. */
const STEAM_TOAST_TYPE_NEW = 0;

/**
 * A notification of ours, told apart from Steam's by a mark of our own.
 *
 * The mark rides on the notification rather than on the group because Steam's
 * popup windows build a group per render out of the single notification they
 * are showing, so a mark left on a group is gone by the time the renderer sees
 * it. Why the name is not Decky Loader's: `docs/architecture/frontend-bundles.md`.
 */
interface TenderNotification extends SteamNotification {
  tender: true;
}

/** Our link in the renderer's `render` chain, once it is installed. */
interface InstalledDrawing {
  /** What was put on the prototype — the identity a later check compares against. */
  readonly render: SteamToastRenderFn;
  /** What it delegates to for everything that is not ours. */
  readonly previous: SteamToastRenderFn;
  /** Stop drawing, leaving the link in place as a pass-through. */
  retire(): void;
}

/** What this module reads off the machine, so a test can supply all of it. */
export interface SteamToasterSeams {
  readonly renderer: SteamToastRenderer | undefined;
  readonly store: SteamNotificationStore | undefined;
  readonly classes: ToastClasses | undefined;
  /** Steam's own, so a throw in our drawing stops here instead of in its tree. */
  readonly errorBoundary: FC<PropsWithChildren> | undefined;
  readonly installTrampoline: (renderer: SteamToastRenderer) => void;
  readonly log: (message: string) => void;
}

/** A toaster, plus the teardown the plugin's dismount owes the renderer. */
export interface SteamToaster extends Toaster {
  /** Take our drawing back out of the renderer's chain. */
  teardown(): void;
}

const describeToast = (toast: ToastData): string => `${String(toast.title)} — ${String(toast.body)}`;

export function createSteamToaster(seams: SteamToasterSeams): SteamToaster {
  const { renderer, store, classes, errorBoundary: Boundary, installTrampoline, log } = seams;
  let drawing: InstalledDrawing | null = null;

  /** The notification a toast of ours is being drawn for, or `null` for anyone else's. */
  const ourNotification = (props: SteamToastRenderProps | undefined): SteamNotification | null => {
    const notification = props?.group?.notifications[0];
    if (notification === undefined) return null;
    return (notification as Partial<TenderNotification>).tender === true ? notification : null;
  };

  const link = (previous: SteamToastRenderFn, Boundary: FC<PropsWithChildren>): InstalledDrawing => {
    let retired = false;
    const render: SteamToastRenderFn = function (
      this: { props: SteamToastRenderProps },
      ...args: unknown[]
    ): ReactNode {
      const mine = retired ? null : ourNotification(this.props);
      if (mine === null) return previous.apply(this, args);
      return (
        <Boundary>
          <SteamToast
            toast={mine.data}
            location={this.props.location}
            createdMs={mine.rtCreated}
            newIndicator={mine.bNewIndicator}
            classes={classes ?? {}}
          />
        </Boundary>
      );
    };
    return {
      render,
      previous,
      retire: () => {
        retired = true;
      },
    };
  };

  /**
   * Put our drawing on top of the renderer's chain, or confirm it is still
   * there. Answers whether a toast pushed now would be drawn by us.
   */
  const ensureDrawing = (): boolean => {
    if (renderer === undefined || Boundary === undefined) return false;
    try {
      const prototype = renderer.prototype;
      if (drawing !== null && prototype.render === drawing.render) return true;
      // An own `render` means a trampoline is already installed — any patcher's,
      // ours included. Applying a second one over it would orphan whoever is in
      // the chain.
      if (Object.getOwnPropertyDescriptor(prototype, "render") === undefined) installTrampoline(renderer);
      const previous = prototype.render;
      if (typeof previous !== "function") return false;
      // Whatever overwrote the link we are replacing may still delegate through
      // it, and it would go on drawing from there — so it is retired before the
      // new one goes on top.
      drawing?.retire();
      drawing = link(previous, Boundary);
      prototype.render = drawing.render;
      return true;
    } catch (e) {
      log(`[Tender] could not install the toast drawing into Steam's notification renderer: ${String(e)}`);
      return false;
    }
  };

  const toast = (data: ToastData): ToastNotification => {
    if (store === undefined || !ensureDrawing()) {
      log(
        `[Tender] toast not shown — what Tender raises toasts through is missing in this Steam: ${describeToast(data)}`,
      );
      return { data, dismiss: () => {} };
    }

    const durationMs = data.duration ?? DEFAULT_TOAST_DURATION_MS;
    const id = store.m_nNextTestNotificationID++;
    const notification: TenderNotification = {
      // Both spellings, one value: `RemoveGroupFromTray` matches a group on
      // `notifications[0].notificationID` and the tab list uses it as its React
      // key, while `nNotificationID` is the counter's own name.
      notificationID: id,
      nNotificationID: id,
      rtCreated: Date.now(),
      eType: STEAM_NOTIFICATION_TYPE_GENERAL,
      eSource: STEAM_NOTIFICATION_SOURCE_CLIENT,
      nToastDurationMS: durationMs,
      bNewIndicator: true,
      data,
      tender: true,
    };

    // A toast is kept in the notifications tab only when it carries subtext: the
    // popup is a fixed-size window that clips, so subtext is the call site
    // saying there is more to read than the popup showed — and an entry nobody
    // can learn anything new from is one more row for the reader to clear.
    let group: SteamNotificationGroup | null = null;
    const info: SteamNotificationInfo = {
      showToast: true,
      sound: STEAM_TOAST_SOUND_MISC,
      playSound: true,
      eFeature: 0,
      toastDurationMS: durationMs,
      fnTray:
        data.subtext === undefined
          ? null
          : (kept, tray) => {
              group = { eType: kept.eType, notifications: [kept] };
              tray.unshift(group);
            },
    };

    try {
      store.ProcessNotification(info, notification, STEAM_TOAST_TYPE_NEW);
    } catch (e) {
      log(`[Tender] Steam refused a toast: ${String(e)}`);
    }

    return {
      data,
      dismiss: () => {
        if (group === null) return;
        try {
          store.RemoveGroupFromTray(group);
        } catch (e) {
          log(`[Tender] Steam refused to drop a toast from its tray: ${String(e)}`);
        }
      },
    };
  };

  const teardown = (): void => {
    const installed = drawing;
    drawing = null;
    if (installed === null || renderer === undefined) return;
    if (renderer.prototype.render === installed.render) {
      renderer.prototype.render = installed.previous;
      return;
    }
    // Somebody wrapped over ours and delegates through it, so cutting it out
    // would take their drawing with it. Left in place and made transparent
    // instead.
    installed.retire();
  };

  return { toast, teardown };
}

/**
 * The toaster the panel raises its toasts through.
 *
 * The trampoline seam casts because `injectFCTrampoline` is typed for the
 * component it renders, where everything here wants the one property this code
 * reads and writes — see {@link SteamToastRenderer}.
 */
export const steamToaster: SteamToaster = createSteamToaster({
  renderer: ToastRenderer,
  store: NotificationStore,
  classes: toastClasses,
  errorBoundary: ErrorBoundary,
  installTrampoline: (renderer) => {
    injectFCTrampoline(renderer as unknown as FC);
  },
  log: (message) => console.warn(message),
});
