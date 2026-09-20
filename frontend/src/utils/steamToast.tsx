/**
 * How one of Tender's toasts is drawn, in each of the three places Steam draws
 * a notification. `utils/steamToaster.tsx` puts this component in front of
 * Steam's own renderer for the groups that are ours; everything here is
 * presentation.
 *
 * The class names are Steam's own (`utils/deckyUiInternals.ts`'s
 * `toastClasses`), so a toast of ours is laid out and animated by the same
 * stylesheet as the notifications beside it. A missing class leaves the element
 * unstyled rather than absent, which is why every read is allowed to be
 * `undefined` and nothing here guards on one.
 */

import { Focusable } from "@decky/ui";
import type { CSSProperties, FC, ReactNode } from "react";

import type { ToastData } from "../api/host";
import type { ToastClasses } from "./deckyUiInternals";

/**
 * Where Steam is drawing the notification. Source: `library.js`, function `Bn`
 * (reached as `ey3`), in the Steam client build on disk.
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

/**
 * Steam's subtext rule allows one line, or two with `Multiline`, and ends the
 * rest in an ellipsis (`css/chunk~2dcc5aaf7.css`, `StandardNotificationSubText`).
 * The tab entry is where a reason has to be readable in full, so its subtext
 * takes neither and wraps as far as it runs.
 */
const SUBTEXT_UNCLIPPED: CSSProperties = { whiteSpace: "normal", overflow: "visible", textOverflow: "clip" };

/**
 * `StandardTemplate` is 50 px tall in the same stylesheet, so an entry whose
 * subtext wraps would run out of its box over the next row: the height follows
 * the content, from Steam's own as the floor.
 */
const ENTRY_GROWS: CSSProperties = { height: "auto", minHeight: "50px" };

const shortTime = (createdMs: number): string =>
  new Date(createdMs).toLocaleTimeString(undefined, { timeStyle: "short" });

/**
 * The Big Picture popup, which is a native window of a fixed size that clips —
 * so a taller layout is not one that scrolls, it is one whose lower half nobody
 * can ever see, and the subtext has no home here. `TwoLine` is Steam's own
 * two-line variant of the same template.
 */
type PopupProps = Pick<SteamToastProps, "toast" | "classes">;

const BigPicturePopup: FC<PopupProps> = ({ toast, classes }) => (
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
 * The desktop client's popup. `Multiline` on the body is what keeps a long one
 * off a single clipped line — without the class Steam ellipsises a description
 * after one.
 */
const DesktopPopup: FC<PopupProps> = ({ toast, classes }) => (
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
const NotificationTabEntry: FC<Omit<SteamToastProps, "location">> = ({ toast, createdMs, newIndicator, classes }) => (
  <Focusable onActivate={() => {}} className={classes.StandardTemplateContainer}>
    <div className={classes.StandardTemplate} style={ENTRY_GROWS}>
      <div className={classes.Content}>
        <div className={classes.Header}>
          <div className={classes.Title}>{toast.title}</div>
          <div className={classes.Timestamp}>{shortTime(createdMs)}</div>
        </div>
        <div className={classes.StandardNotificationDescription}>{toast.body}</div>
        {toast.subtext !== undefined && (
          <div className={classes.StandardNotificationSubText} style={SUBTEXT_UNCLIPPED}>
            {toast.subtext}
          </div>
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
export const SteamToast: FC<SteamToastProps> = ({ location, ...entry }): ReactNode => {
  switch (location) {
    case TOAST_LOCATION_BIG_PICTURE_POPUP:
      return <BigPicturePopup toast={entry.toast} classes={entry.classes} />;
    case TOAST_LOCATION_DESKTOP_POPUP:
      return <DesktopPopup toast={entry.toast} classes={entry.classes} />;
    default:
      return <NotificationTabEntry {...entry} />;
  }
};
