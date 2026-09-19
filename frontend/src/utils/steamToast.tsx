/**
 * How one of Tender's toasts is drawn, in each of the three places Steam draws
 * a notification.
 *
 * Steam's own renderer switches on the notification's type and knows Valve's
 * typed notifications only, so an entry of ours reaches its `default` arm and
 * draws nothing. `utils/steamToaster.tsx` puts this component in front of it
 * for the groups that are ours; everything here is presentation.
 *
 * The class names are Steam's own (`utils/deckyUiInternals.ts`'s
 * `toastClasses`), so a toast of ours is laid out and animated by the same
 * stylesheet as the notifications beside it. A missing class leaves the element
 * unstyled rather than absent, which is why every read is allowed to be
 * `undefined` and nothing here guards on one.
 */

import { Focusable } from "@decky/ui";
import type { FC, ReactNode } from "react";

import type { ToastData } from "../api/host";
import type { ToastClasses } from "./deckyUiInternals";

/**
 * Where Steam is drawing the notification.
 *
 * Steam's own values, read out of its bundle: the renderer turns the prop into
 * a telemetry submethod name through a switch over `0 invalid`, `1 gamepad`,
 * `2 desktop`, `3 tray`, `4 all`, `5 push`, and the three call sites that reach
 * this component pass 1 from the Big Picture popup window, 2 from the
 * desktop-client popup and 3 from the Quick Access notifications tab.
 */
export const TOAST_LOCATION_BIG_PICTURE_POPUP = 1;
export const TOAST_LOCATION_DESKTOP_POPUP = 2;
export const TOAST_LOCATION_NOTIFICATION_TAB = 3;

export interface SteamToastProps {
  readonly toast: ToastData;
  /** Absent where Steam drew without one, which falls back to the tab layout. */
  readonly location: number | undefined;
  /** Unix milliseconds the notification was created at — the tab entry's time. */
  readonly createdMs: number;
  readonly newIndicator: boolean;
  readonly classes: ToastClasses;
}

const classNames = (...names: (string | undefined | false)[]): string => names.filter(Boolean).join(" ");

const shortTime = (createdMs: number): string =>
  new Date(createdMs).toLocaleTimeString(undefined, { timeStyle: "short" });

/**
 * The Big Picture popup, which is a native window of a fixed 321x81 px with
 * `overflow: hidden` — so a taller layout is not a layout that scrolls, it is
 * one whose lower half nobody can ever see. `TwoLine` is Steam's own answer to
 * that and the tallest thing that fits, which is why the subtext has no home
 * here.
 */
const BigPicturePopup: FC<SteamToastProps> = ({ toast, classes }) => (
  <div className={classNames(classes.ShortTemplate, classes.TwoLine)}>
    <div className={classes.Content}>
      <div className={classes.Header}>
        <div className={classes.Title}>{toast.title}</div>
      </div>
      <div className={classes.Body}>{toast.body}</div>
    </div>
  </div>
);

/**
 * The desktop client's popup. `Multiline` on the body is the difference that
 * matters: without it Steam clips a description to one line with an ellipsis,
 * and the desktop popup is the one place with room for the two the class buys.
 */
const DesktopPopup: FC<SteamToastProps> = ({ toast, classes }) => (
  <div className={classes.StandardTemplateContainer}>
    <div className={classNames(classes.DesktopToastTemplate, classes.StandardTemplateDesktop)}>
      <div className={classes.Content}>
        <div className={classes.Header}>
          <div className={classes.Title}>{toast.title}</div>
        </div>
        <div className={classNames(classes.StandardNotificationDescription, classes.Multiline)}>{toast.body}</div>
        {toast.subtext !== undefined && (
          <div className={classNames(classes.StandardNotificationSubText, classes.Multiline)}>{toast.subtext}</div>
        )}
      </div>
    </div>
  </div>
);

/**
 * The Quick Access notifications tab, where the entry stays until it is
 * dismissed. It is the one layout a reader navigates to, so it is a focus stop:
 * a region there scrolls only by moving focus, and a row nobody can focus is a
 * row nobody can scroll past. There is nothing to activate — a toast of ours
 * names no destination — so the handler is empty and buys the stop alone.
 */
const NotificationTabEntry: FC<SteamToastProps> = ({ toast, createdMs, newIndicator, classes }) => (
  <Focusable onActivate={() => {}} className={classes.StandardTemplateContainer}>
    <div className={classes.StandardTemplate}>
      <div className={classes.Content}>
        <div className={classes.Header}>
          <div className={classes.Title}>{toast.title}</div>
          <div className={classes.Timestamp}>{shortTime(createdMs)}</div>
        </div>
        <div className={classes.StandardNotificationDescription}>{toast.body}</div>
        {toast.subtext !== undefined && (
          <div className={classNames(classes.StandardNotificationSubText, classes.Multiline)}>{toast.subtext}</div>
        )}
      </div>
      {newIndicator && (
        <div className={classes.NewIndicator}>
          <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 50 50" aria-hidden="true">
            <circle fill="currentColor" cx="25" cy="25" r="25" />
          </svg>
        </div>
      )}
    </div>
  </Focusable>
);

/**
 * One of Tender's toasts, drawn for the place Steam is drawing it.
 *
 * An unrecognised location falls back to the tab layout, which is the only one
 * of the three that is right to render anywhere: it carries every field a toast
 * can have and imposes no size of its own.
 */
export const SteamToast: FC<SteamToastProps> = (props): ReactNode => {
  switch (props.location) {
    case TOAST_LOCATION_BIG_PICTURE_POPUP:
      return <BigPicturePopup {...props} />;
    case TOAST_LOCATION_DESKTOP_POPUP:
      return <DesktopPopup {...props} />;
    default:
      return <NotificationTabEntry {...props} />;
  }
};
