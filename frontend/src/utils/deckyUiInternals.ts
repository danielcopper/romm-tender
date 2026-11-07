/**
 * Honest-typed re-exports of @decky/ui internal lookups whose runtime presence
 * isn't guaranteed.
 *
 * @decky/ui populates its class-map consts via `findClassModule` / webpack
 * module probes that can return `undefined` at runtime, and answers `findSP`
 * from Steam's runtime state — `document.title`, else the focus controller's
 * navigation trees — which carry no Big Picture tree until Big Picture has been
 * opened; its own code even writes `findSP() || window`. Upstream still types
 * them as always-present (`declare const x: T`, `findSP(): Window`), so direct
 * consumers get a lying non-null type and their defensive `?.` guards read as
 * dead code. TS cannot re-type a `const`/function via `declare module`
 * augmentation, so this thin runtime re-export module re-declares each as
 * `T | undefined`, making the guards legitimate.
 *
 * Any future @decky/ui value sourced from a findClassModule-style probe, or from
 * Steam's runtime state, belongs here, typed honestly. A global Steam itself
 * installs belongs here too, for the same reason and with the same shape: it is
 * a name this code reads off a foreign program and cannot make appear, so the
 * type has to admit that it may not be there.
 */

import type { CSSProperties, FC, FocusEventHandler, PropsWithChildren, ReactNode } from "react";

import type { ToastData } from "../api/host";
import {
  findClassModule,
  findModule,
  findModuleExport,
  type ClassModule,
  ErrorBoundary as _ErrorBoundary,
  appActionButtonClasses as _appActionButtonClasses,
  basicAppDetailsSectionStylerClasses as _basicAppDetailsSectionStylerClasses,
  appDetailsClasses as _appDetailsClasses,
  playSectionClasses as _playSectionClasses,
  quickAccessMenuClasses as _quickAccessMenuClasses,
  ScrollPanel as _ScrollPanel,
  Tabs as _Tabs,
  findSP as _findSP,
  getGamepadNavigationTrees as _getGamepadNavigationTrees,
  type TabsProps,
} from "@decky/ui";

export const appActionButtonClasses: typeof _appActionButtonClasses | undefined = _appActionButtonClasses;
export const basicAppDetailsSectionStylerClasses: typeof _basicAppDetailsSectionStylerClasses | undefined =
  _basicAppDetailsSectionStylerClasses;
export const appDetailsClasses: typeof _appDetailsClasses | undefined = _appDetailsClasses;
export const playSectionClasses: typeof _playSectionClasses | undefined = _playSectionClasses;
export const quickAccessMenuClasses: typeof _quickAccessMenuClasses | undefined = _quickAccessMenuClasses;

/**
 * What the tabbed page accepts beyond the four props `@decky/ui` declares.
 * Upstream's `TabsProps` predates all of them; the component is typed `any`
 * there, so nothing would have caught a name being wrong either. What each does
 * in Steam's code: `docs/architecture/qam-panel.md`, "Pages" for
 * `cancelSkipTabHeader`, "The game page's tabs" for the other two.
 */
export interface WideTabsProps extends TabsProps {
  /** True leaves the content pane without its handler for B. */
  cancelSkipTabHeader?: boolean;
  /** The tabbed page's own handler for B, which B on the tab row reaches; `false` lets B go on. */
  onCancelFromTabHeader?: () => boolean | undefined;
  /** Gives the tabbed page its height: one screen less Steam's header. */
  canBeHeaderBackground?: "on-outer-scroll";
}

/**
 * Steam's L1/R1 tabbed page, found through a `findModuleByExport` probe on the
 * shape of its render function — so it is `undefined` whenever that probe misses.
 * Upstream types it `any`, which hides both the absence and the props; a
 * component type states what the frame actually passes it.
 */
export const Tabs: FC<WideTabsProps> | undefined = _Tabs;

/**
 * What the scroll panel accepts beyond its children. Upstream types it
 * `FC<{ children?: ReactNode }>`, which is narrower than it is: it destructures
 * `className` and `style`, merges the style with its own scroll padding, and
 * spreads the rest into the same base panel `Focusable` renders.
 */
export interface ScrollPanelProps {
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  /** Reaches the rendered element: the panel spreads what it does not
   *  destructure into the base panel `Focusable` renders, which is where the
   *  attribute lands. React delivers it through `focusin`, so it fires for a
   *  focus landing on any descendant. */
  onFocus?: FocusEventHandler<HTMLDivElement>;
}

/**
 * Steam's plain scroll container: an `overflow-y: auto` box that takes no focus
 * of its own, so the rows inside it take focus directly and Steam's navigation
 * scrolls the focused row into view. It is what the QAM's own tab panel is built
 * from — the element carrying `#quickaccess_content_999` IS one of these — and
 * what Steam's tabbed page wraps each tab's content in.
 *
 * Its sibling `ScrollPanelGroup` binds gamepad direction to scrolling, which is
 * what a region of content nobody can focus would need; it was tried on the
 * device and rejected, because it is focusable and its OK button focuses its
 * first visible child, making every region a stop the reader has to enter with A
 * before reaching a row. Reachability is bought the other way instead: every row
 * a reader must reach is a focusable row (`docs/architecture/qam-panel.md`).
 *
 * Reached through a `findModuleByExport` probe on its render function, so it is
 * `undefined` whenever that probe misses.
 */
export const ScrollPanel: FC<ScrollPanelProps> | undefined = _ScrollPanel;

export const findSP = (): Window | undefined => _findSP();

/**
 * One of Steam's gamepad navigation trees, reduced to what this code reads off
 * it: the id it is looked up by and the element its root is mounted on.
 *
 * `@decky/ui` reads these trees under two vocabularies — `findSP` reads `m_ID`,
 * `Root` and `Element` where its Quick Access lookup reads the names below — so
 * this is not the whole shape and is not a description of one.
 */
export interface GamepadNavigationTree {
  id?: string;
  m_Root?: { m_element?: Element };
}

/**
 * Steam's navigation trees for the focus controller's active (else last active)
 * context — `undefined` while that context carries none, which is the desktop
 * client until Big Picture has been opened.
 *
 * Upstream types it `any`, so every caller's guard reads as a guard over
 * something that is always there. The array's MEMBERS are optional because
 * upstream's own lookup guards them (`tree?.id`,
 * `dist/custom-hooks/useQuickAccessVisible.js`), which is a statement about the
 * array that its type has to carry.
 */
export const getGamepadNavigationTrees = (): (GamepadNavigationTree | undefined)[] | undefined =>
  _getGamepadNavigationTrees();

/**
 * What Steam's controller-glyph image takes. `button` is Steam's OWN button
 * enum and not `@decky/ui`'s `GamepadButton` — the two disagree on every value
 * ({@link GLYPH_BUTTON_B}).
 */
export interface ControllerGlyphProps {
  button: number;
  /** The monochrome silhouette, which is what Steam uses inside running text. */
  bKnockout?: boolean;
  className?: string;
  style?: CSSProperties;
}

/**
 * The value Steam's glyph component wants for **B**.
 *
 * It indexes Steam's own action-button enum — `A=0, B=1, X=2, Y=3, Left=4 … `
 * (`steamui/sp.js`, module 14181, exported as `g4`) — which is a different
 * enum from `@decky/ui`'s `GamepadButton` (`INVALID=0, OK=1, CANCEL=2 …`), where
 * 1 is the A button. Passing the wrong one draws the wrong glyph rather than
 * failing.
 */
export const GLYPH_BUTTON_B = 1;

/** The values Steam's glyph component wants for **L2** and **R2**, in the same enum:
 *  `LeftTrigger=14`, `RightTrigger=15`. */
export const GLYPH_TRIGGER_LEFT = 14;
export const GLYPH_TRIGGER_RIGHT = 15;

const isMemoComponent = (value: unknown): boolean =>
  typeof value === "object" && value !== null && (value as { $$typeof?: symbol }).$$typeof === Symbol.for("react.memo");

/**
 * Steam's controller-glyph image: one button drawn the way the controller in
 * the user's hands draws it — B on a Deck or an Xbox pad, ○ on a PlayStation
 * one, and the swapped face button under a Nintendo layout. It reads the active
 * controller itself, which is why neither a lookalike SVG nor a typed letter is
 * an alternative: both would be wrong for someone.
 *
 * `@decky/ui` does not re-export it, so it is reached by a module probe: the
 * module exporting both `WD` (this glyph, which is the answer) and `XH` (the
 * footer's glyph-plus-label legend), each a `React.memo` object — both are mobx
 * observers, which is what one is at runtime. How many exports that module has
 * is deliberately not asked. In `steamui/sp.js` of the Deck's stable client
 * (1792618285) and its public beta (1794079253) the pair stands on exactly one
 * module.
 *
 * The names are webpack 5's deterministic export mangling
 * (`MangleExportsPlugin`): each is a hash of Valve's ORIGINAL export name
 * reduced into a range set by how many exports the module has — one character
 * for two exports, two characters for 3 to 172, the first character the same
 * in both. So an export name holds from one Steam build to the next: short of
 * a hash collision or a change to Valve's webpack setup, it moves only when
 * Valve renames the original or the module's export count leaves its bracket —
 * back down to two would most likely make these `W` and `X` again.
 * This is the one home of that rule; the other probes that name it point here.
 *
 * `undefined` the day that happens, so every caller keeps a fallback rather
 * than a hole where the glyph was.
 */
export const ControllerGlyph: FC<ControllerGlyphProps> | undefined = findModule((m: unknown) => {
  try {
    if (typeof m !== "object" || m === null) return false;
    const exports = m as Record<string, unknown>;
    return isMemoComponent(exports.WD) && isMemoComponent(exports.XH);
  } catch {
    return false;
  }
})?.WD;

/** One notification as Steam's store carries it, with our own toast as its payload. */
export interface SteamNotification {
  /** What `RemoveGroupFromTray` matches a group on, and the tab list's React key. */
  notificationID: number;
  nNotificationID: number;
  rtCreated: number;
  eType: number;
  eSource: number;
  nToastDurationMS: number;
  bNewIndicator: boolean;
  data: ToastData;
}

/** What the store keeps in its tray: one or more notifications of one type. */
export interface SteamNotificationGroup {
  eType: number;
  notifications: SteamNotification[];
}

/** The second argument Steam hands `ProcessNotification`'s `fnTray` callback. */
export type SteamNotificationTray = SteamNotificationGroup[];

/** How one notification is to be delivered. */
export interface SteamNotificationInfo {
  showToast: boolean;
  sound: number;
  playSound: boolean;
  eFeature: number;
  toastDurationMS: number;
  /** Called with the notification and the live tray array when it is to be kept. */
  fnTray: ((notification: SteamNotification, tray: SteamNotificationTray) => void) | null;
}

/** The two methods this code calls on Steam's notification store, and the counter it reads. */
export interface SteamNotificationStore {
  m_nNextTestNotificationID: number;
  ProcessNotification(info: SteamNotificationInfo, notification: SteamNotification, eToastType: number): void;
  RemoveGroupFromTray(group: SteamNotificationGroup): void;
}

/** What Steam's toast renderer is handed for one notification group. */
export interface SteamToastRenderProps {
  group?: SteamNotificationGroup;
  /** Which surface is drawing — see `utils/steamToast.tsx` for the values. */
  location?: number;
  className?: string;
}

/** A `prototype.render` installed on the toast renderer by an FC trampoline. */
export type SteamToastRenderFn = (this: { props: SteamToastRenderProps }, ...args: unknown[]) => ReactNode;

/**
 * Steam's toast renderer, reduced to the one property this code reads and
 * writes: `prototype.render`.
 *
 * Calling it is left out on purpose — nothing here renders it, and a call
 * signature would invite one.
 */
export interface SteamToastRenderer {
  prototype: { render?: SteamToastRenderFn };
}

/**
 * Steam's toast renderer, the component every notification is drawn through.
 *
 * Found by the string its own body contains — it opens its result with
 * `controller:"notification",method:` — because the module exports it under a
 * minified name, which moves whenever Valve renames it or the module's export
 * count leaves its bracket ({@link ControllerGlyph} has the rule).
 *
 * What it would draw for a notification of ours, and why `utils/steamToaster.tsx`
 * puts a drawing in front of it, is on the docs page
 * (`docs/architecture/frontend-bundles.md`).
 */
export const ToastRenderer: SteamToastRenderer | undefined = findModuleExport((e: unknown) => {
  // The predicate is run against every export in Steam's registry, so one
  // whose `toString` is not a function, or throws, must answer `false` rather
  // than end the search.
  try {
    const source = (e as { toString?: () => unknown } | undefined)?.toString?.();
    return typeof source === "string" && source.includes('controller:"notification",method:');
  } catch {
    return false;
  }
});

/**
 * Steam's notification store, which owns the toast queue and the tray.
 *
 * Steam assigns it at module scope, so it is there before any panel is loaded
 * and reading it once is a reading of the install rather than of a moment —
 * which is what lets the start-up check ask about it at all.
 */
export const NotificationStore: SteamNotificationStore | undefined = (
  window as unknown as { NotificationStore?: SteamNotificationStore }
).NotificationStore;

/**
 * Whether *value* is a component whose render function's source names *name*.
 *
 * Steam's Quick Access renderers are exported under minified single letters,
 * and their component names survive only inside the function bodies — so the
 * source text is the only handle there is, as it is for Decky.
 */
export const rendersNamed =
  (name: string) =>
  (value: unknown): boolean => {
    try {
      const type = (value as { type?: { toString?: () => unknown } } | null | undefined)?.type;
      const source = type?.toString?.();
      return typeof source === "string" && source.includes(name);
    } catch {
      return false;
    }
  };

/**
 * The module Steam draws its Quick Access menu from: the one exporting the
 * browser view Gaming Mode uses, beside the embedded one. Tender's own Quick
 * Access entry is installed into its renderers (`qam/installEntry.tsx`).
 *
 * Asked the way `findModuleByExport` asks — any export of the module, or of its
 * `default`, rendering `QuickAccessMenuBrowserView` — through `findModule`, so
 * the search runs once, when this module is evaluated, like every other here.
 * One difference: a module whose exports throw on read answers no here, where
 * `findModuleByExport` reads each export outside its own catch
 * (`dist/webpack.js`) and the throw would end the search and this module's
 * evaluation with it.
 */
export const QuickAccessMenuBrowserView: Readonly<Record<string, unknown>> | undefined = findModule((m: unknown) => {
  try {
    if (typeof m !== "object" || m === null || m === window) return false;
    return Object.values(m).some(rendersNamed("QuickAccessMenuBrowserView"));
  } catch {
    return false;
  }
});

/**
 * The class names Steam's own notification templates are drawn with.
 *
 * Several class maps carry these template names; exactly one carries
 * `ShortTemplate`, which is what the probe keys on.
 */
export interface ToastClasses {
  readonly ShortTemplate?: string;
  readonly TwoLine?: string;
  readonly StandardTemplateContainer?: string;
  readonly StandardTemplate?: string;
  readonly StandardTemplateDesktop?: string;
  readonly DesktopToastTemplate?: string;
  readonly Content?: string;
  readonly Header?: string;
  readonly Title?: string;
  readonly Timestamp?: string;
  readonly Body?: string;
  readonly StandardNotificationDescription?: string;
  readonly StandardNotificationSubText?: string;
  readonly Multiline?: string;
  readonly NewIndicator?: string;
}

export const toastClasses: ToastClasses | undefined =
  findClassModule((m: ClassModule) => Boolean(m.ShortTemplate)) ?? undefined;

/**
 * Steam's own error boundary, which `@decky/ui` reaches with a `findModuleExport`
 * predicate — so it is `undefined` whenever that predicate misses, where
 * upstream types it as a component that is always there.
 *
 * It is what keeps a throw inside a toast of ours from reaching Steam's
 * notification tree, which draws everyone's entries.
 */
export const ErrorBoundary: FC<PropsWithChildren> | undefined = _ErrorBoundary;
