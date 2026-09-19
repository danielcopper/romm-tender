// What a test here CANNOT see: Steam's notification store, its toast queue and
// its popup windows are all absent under happy-dom, so the store below records
// what it was handed and nothing reacts to it. Whether the popup appears at all
// is a device question. What is pinned is the shape of the push, which of the
// two ways a toast can end up in the tray, and the renderer chain — the last
// being the one half that can silently orphan another program's patch.

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { createElement, type PropsWithChildren, type ReactNode } from "react";

import type { ToastData } from "../api/host";
import type {
  SteamNotification,
  SteamNotificationGroup,
  SteamNotificationInfo,
  SteamNotificationStore,
  SteamToastRenderFn,
  SteamToastRenderProps,
  SteamToastRenderer,
} from "./deckyUiInternals";
import { createSteamToaster, type SteamToasterSeams } from "./steamToaster";

interface PushedNotification {
  info: SteamNotificationInfo;
  notification: SteamNotification;
  eToastType: number;
}

/** A store that records what it was handed, plus the tray Steam would own. */
function fakeStore(): SteamNotificationStore & {
  pushed: PushedNotification[];
  tray: SteamNotificationGroup[];
  removed: SteamNotificationGroup[];
} {
  const pushed: PushedNotification[] = [];
  const tray: SteamNotificationGroup[] = [];
  const removed: SteamNotificationGroup[] = [];
  return {
    pushed,
    tray,
    removed,
    m_nNextTestNotificationID: 700,
    ProcessNotification(info, notification, eToastType) {
      pushed.push({ info, notification, eToastType });
      // Steam calls the tray callback itself, which is the only way the group
      // a dismiss would remove ever comes into existence.
      info.fnTray?.(notification, tray);
    },
    // Matched the way Steam's does — on the first notification's
    // `notificationID`, not on the group object's identity.
    RemoveGroupFromTray(group) {
      const id = group.notifications[0]?.notificationID;
      const index = tray.findIndex((held) => held.notifications[0]?.notificationID === id);
      if (index === -1) return;
      removed.push(tray[index]!);
      tray.splice(index, 1);
    },
  };
}

/**
 * A stand-in for Steam's toast renderer: a plain function, because what is
 * patched is its `prototype.render` and an arrow function has no prototype.
 */
function fakeRenderer(): SteamToastRenderer {
  function ValveToastRenderer(): null {
    return null;
  }
  return ValveToastRenderer as unknown as SteamToastRenderer;
}

/** What `injectFCTrampoline` does to the prototype, without Steam's React. */
const installTrampoline = (renderer: SteamToastRenderer): void => {
  renderer.prototype.render = function (this: { props: SteamToastRenderProps }): ReactNode {
    return createElement("div", { "data-testid": "valve-drawing" }, String(this.props.location));
  };
};

/** Steam's own boundary, reduced to the one thing a test can see: it renders. */
const fakeErrorBoundary = ({ children }: PropsWithChildren): ReactNode =>
  createElement("div", { "data-testid": "error-boundary" }, children);

const seams = (over: Partial<SteamToasterSeams> = {}): SteamToasterSeams => ({
  renderer: fakeRenderer(),
  store: fakeStore(),
  classes: { ShortTemplate: "c-short", StandardTemplate: "c-standard", Body: "c-body" },
  errorBoundary: fakeErrorBoundary,
  installTrampoline,
  log: vi.fn(),
  ...over,
});

const TOAST: ToastData = { title: "Tender", body: "Sync finished" };

/** One of Steam's own, carrying no mark of ours. */
const VALVE_NOTIFICATION: SteamNotification = {
  notificationID: 1,
  nNotificationID: 1,
  rtCreated: 0,
  eType: 4,
  eSource: 2,
  nToastDurationMS: 5000,
  bNewIndicator: false,
  data: { title: "Steam", body: "A friend is online" },
};

/** One group as Steam builds it for a popup: the notification, wrapped. */
const groupFor = (notification: SteamNotification): SteamNotificationGroup => ({
  eType: notification.eType,
  notifications: [notification],
});

/**
 * Draw one group through whatever now sits on the renderer's prototype —
 * called the way React calls it, as a method with the props on `this`.
 */
const drawWith = (renderer: SteamToastRenderer, props: SteamToastRenderProps) => {
  const drawFn = renderer.prototype.render;
  if (drawFn === undefined) throw new Error("nothing is installed on the renderer's prototype");
  const Drawn = () => drawFn.call({ props }) as ReactNode;
  return render(<Drawn />);
};

/** Raise a toast and answer with the notification Steam was handed for it. */
const raiseAndDraw = (
  toaster: ReturnType<typeof createSteamToaster>,
  store: ReturnType<typeof fakeStore>,
): SteamNotification => {
  toaster.toast({ ...TOAST, subtext: "14 games" });
  return store.pushed[store.pushed.length - 1]!.notification;
};

describe("what a toast pushes into Steam's notification store", () => {
  it("pushes one new notification, with the fields Steam's queue reads", () => {
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ store }));
    toaster.toast(TOAST);

    expect(store.pushed).toHaveLength(1);
    const { info, notification, eToastType } = store.pushed[0]!;
    expect(eToastType).toBe(0);
    expect(notification.notificationID).toBe(700);
    expect(notification.nNotificationID).toBe(700);
    expect(notification.eType).toBe(31);
    expect(notification.eSource).toBe(1);
    expect(notification.bNewIndicator).toBe(true);
    expect(notification.data).toBe(TOAST);
    expect(typeof notification.rtCreated).toBe("number");
    expect(info).toMatchObject({
      showToast: true,
      sound: 6,
      playSound: true,
      eFeature: 0,
    });
  });

  it("takes the next id from the store's own counter, so two toasts never share one", () => {
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ store }));
    toaster.toast(TOAST);
    toaster.toast(TOAST);
    expect(store.pushed.map((p) => p.notification.notificationID)).toEqual([700, 701]);
    expect(store.pushed.map((p) => p.notification.nNotificationID)).toEqual([700, 701]);
    expect(store.m_nNextTestNotificationID).toBe(702);
  });

  it("marks the notification as Tender's, and not with Decky Loader's name", () => {
    const store = fakeStore();
    createSteamToaster(seams({ store })).toast(TOAST);
    const notification = store.pushed[0]!.notification as unknown as Record<string, unknown>;
    expect(notification.tender).toBe(true);
    expect(notification.decky).toBeUndefined();
  });

  it("stays up for five seconds, or for as long as the call site said", () => {
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ store }));
    toaster.toast(TOAST);
    toaster.toast({ ...TOAST, duration: 12000 });
    expect(store.pushed.map((p) => p.notification.nToastDurationMS)).toEqual([5000, 12000]);
    expect(store.pushed.map((p) => p.info.toastDurationMS)).toEqual([5000, 12000]);
  });

  it("hands the caller back the toast it raised", () => {
    const raised = createSteamToaster(seams()).toast(TOAST);
    expect(raised.data).toBe(TOAST);
  });
});

describe("which toasts are kept in the notifications tab", () => {
  it("keeps one that carries subtext, because the popup had no room to show it", () => {
    const store = fakeStore();
    createSteamToaster(seams({ store })).toast({ ...TOAST, subtext: "14 games" });
    expect(store.tray).toHaveLength(1);
    expect(store.tray[0]!.notifications[0]!.data.subtext).toBe("14 games");
    expect(store.tray[0]!.eType).toBe(31);
  });

  it("keeps no entry for one without, so nothing is left for the reader to clear", () => {
    const store = fakeStore();
    createSteamToaster(seams({ store })).toast(TOAST);
    expect(store.pushed[0]!.info.fnTray).toBeNull();
    expect(store.tray).toEqual([]);
  });

  it("drops the tray entry on dismiss, and does nothing where there was none", () => {
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ store }));
    const kept = toaster.toast({ ...TOAST, subtext: "14 games" });
    const transient = toaster.toast(TOAST);

    transient.dismiss();
    expect(store.removed).toEqual([]);
    kept.dismiss();
    expect(store.removed.map((g) => g.notifications[0]!.notificationID)).toEqual([700]);
    expect(store.tray).toEqual([]);
  });

  it("drops the toast that was dismissed, where the tray holds more than one", () => {
    // Steam matches a group on `notifications[0].notificationID` rather than on
    // the object, so an id it cannot find removes the wrong entry or none.
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ store }));
    const older = toaster.toast({ ...TOAST, subtext: "14 games" });
    toaster.toast({ ...TOAST, subtext: "3 saves" });

    older.dismiss();
    expect(store.removed.map((g) => g.notifications[0]!.notificationID)).toEqual([700]);
    expect(store.tray.map((g) => g.notifications[0]!.notificationID)).toEqual([701]);
  });

  it("never throws out of dismiss, whatever Steam does with the group", () => {
    const store = fakeStore();
    const log = vi.fn();
    store.RemoveGroupFromTray = () => {
      throw new Error("gone");
    };
    const raised = createSteamToaster(seams({ store, log })).toast({ ...TOAST, subtext: "14 games" });
    expect(() => raised.dismiss()).not.toThrow();
    expect(log).toHaveBeenCalledWith(expect.stringContaining("refused to drop a toast"));
  });

  it("never throws out of the push either, and says what happened", () => {
    const store = fakeStore();
    const log = vi.fn();
    store.ProcessNotification = () => {
      throw new Error("unknown eType");
    };
    expect(() => createSteamToaster(seams({ store, log })).toast(TOAST)).not.toThrow();
    expect(log).toHaveBeenCalledWith(expect.stringContaining("Steam refused a toast"));
  });
});

describe("a Steam that answers for something a toast is raised through", () => {
  it("logs the toast and hands back an inert handle when the store is missing", () => {
    const log = vi.fn();
    const toaster = createSteamToaster(seams({ store: undefined, log }));
    const raised = toaster.toast(TOAST);
    expect(log).toHaveBeenCalledWith(expect.stringContaining("Tender — Sync finished"));
    expect(raised.data).toBe(TOAST);
    expect(() => raised.dismiss()).not.toThrow();
  });

  it("pushes nothing when the renderer is missing, even though the store would take it", () => {
    const store = fakeStore();
    const log = vi.fn();
    createSteamToaster(seams({ renderer: undefined, store, log })).toast(TOAST);
    expect(store.pushed).toEqual([]);
    expect(log).toHaveBeenCalledWith(expect.stringContaining("what Tender raises toasts through is missing"));
  });

  it("pushes nothing when the error boundary is missing, so a throw of ours cannot reach Steam's tree", () => {
    const store = fakeStore();
    const log = vi.fn();
    createSteamToaster(seams({ errorBoundary: undefined, store, log })).toast(TOAST);
    expect(store.pushed).toEqual([]);
    expect(log).toHaveBeenCalledWith(expect.stringContaining("what Tender raises toasts through is missing"));
  });

  it("pushes nothing when the trampoline refuses to install", () => {
    const store = fakeStore();
    const log = vi.fn();
    createSteamToaster(
      seams({
        store,
        log,
        installTrampoline: () => {
          throw new Error("no SP_REACTDOM");
        },
      }),
    ).toast(TOAST);
    expect(store.pushed).toEqual([]);
    expect(log).toHaveBeenCalledWith(expect.stringContaining("could not install the toast drawing"));
  });

  it("pushes nothing when the trampoline installs no render at all", () => {
    const store = fakeStore();
    createSteamToaster(seams({ store, installTrampoline: () => {} })).toast(TOAST);
    expect(store.pushed).toEqual([]);
  });
});

describe("the renderer's render chain", () => {
  it("installs a trampoline itself when nothing has patched the renderer", () => {
    const renderer = fakeRenderer();
    const store = fakeStore();
    const install = vi.fn(installTrampoline);
    createSteamToaster(seams({ renderer, store, installTrampoline: install })).toast(TOAST);
    expect(install).toHaveBeenCalledTimes(1);
    expect(renderer.prototype.render).toBeTypeOf("function");
  });

  it("wraps a render that is already there rather than applying a second trampoline", () => {
    const renderer = fakeRenderer();
    installTrampoline(renderer);
    const install = vi.fn(installTrampoline);
    createSteamToaster(seams({ renderer, installTrampoline: install })).toast(TOAST);
    expect(install).not.toHaveBeenCalled();
  });

  it("draws our own toast for a group of ours, inside Steam's error boundary", () => {
    const renderer = fakeRenderer();
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ renderer, store }));
    const notification = raiseAndDraw(toaster, store);

    const drawn = drawWith(renderer, { group: groupFor(notification), location: 3 });
    expect(drawn.queryByText("Sync finished")).not.toBeNull();
    expect(drawn.queryByTestId("error-boundary")).not.toBeNull();
    expect(drawn.queryByTestId("valve-drawing")).toBeNull();
  });

  it("hands a group that is not ours straight back to whoever was there first", () => {
    const renderer = fakeRenderer();
    const toaster = createSteamToaster(seams({ renderer }));
    toaster.toast(TOAST);

    const drawn = drawWith(renderer, { group: groupFor(VALVE_NOTIFICATION), location: 1 });
    expect(drawn.queryByTestId("valve-drawing")).not.toBeNull();
    expect(drawn.queryByTestId("error-boundary")).toBeNull();
  });

  it("hands back a render with no group at all rather than drawing an empty toast", () => {
    const renderer = fakeRenderer();
    createSteamToaster(seams({ renderer })).toast(TOAST);
    expect(drawWith(renderer, {}).queryByTestId("valve-drawing")).not.toBeNull();
  });

  it("wraps again when somebody replaced our render between two toasts", () => {
    const renderer = fakeRenderer();
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ renderer, store }));
    toaster.toast(TOAST);

    const ours = renderer.prototype.render;
    installTrampoline(renderer);
    expect(renderer.prototype.render).not.toBe(ours);

    const notification = raiseAndDraw(toaster, store);
    expect(renderer.prototype.render).not.toBe(ours);
    const drawn = drawWith(renderer, { group: groupFor(notification), location: 3 });
    expect(drawn.queryByText("Sync finished")).not.toBeNull();
  });

  it("keeps the replacement reachable for a group that is not ours", () => {
    // The other half of the re-wrap: the link put on top has to delegate to
    // what replaced us, not to what we found the first time.
    const renderer = fakeRenderer();
    const toaster = createSteamToaster(seams({ renderer }));
    toaster.toast(TOAST);
    renderer.prototype.render = function (this: { props: SteamToastRenderProps }): ReactNode {
      return createElement("div", { "data-testid": "second-patcher" });
    };
    toaster.toast(TOAST);

    expect(
      drawWith(renderer, { group: groupFor(VALVE_NOTIFICATION), location: 1 }).queryByTestId("second-patcher"),
    ).not.toBeNull();
  });

  it("stops the link it replaces from drawing, so only one of ours is ever live", () => {
    // Whatever overwrote the replaced link may still delegate through it, as
    // the second patcher below does — so without retiring it a teardown that
    // restores nothing would leave it drawing from underneath.
    const renderer = fakeRenderer();
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ renderer, store }));
    toaster.toast(TOAST);

    const ours = renderer.prototype.render as SteamToastRenderFn;
    renderer.prototype.render = function (this: { props: SteamToastRenderProps }, ...args: unknown[]): ReactNode {
      return createElement("div", { "data-testid": "second-patcher" }, ours.apply(this, args) as ReactNode);
    };
    const notification = raiseAndDraw(toaster, store);
    toaster.teardown();

    const drawn = drawWith(renderer, { group: groupFor(notification), location: 3 });
    expect(drawn.queryByTestId("second-patcher")).not.toBeNull();
    expect(drawn.queryByText("Sync finished")).toBeNull();
  });
});

describe("handing the renderer back at dismount", () => {
  it("puts back what it found when ours is still the one on top", () => {
    const renderer = fakeRenderer();
    installTrampoline(renderer);
    const valveRender = renderer.prototype.render;

    const toaster = createSteamToaster(seams({ renderer }));
    toaster.toast(TOAST);
    expect(renderer.prototype.render).not.toBe(valveRender);

    toaster.teardown();
    expect(renderer.prototype.render).toBe(valveRender);
  });

  it("goes transparent instead of cutting the chain when somebody wrapped over ours", () => {
    // Restoring here would take the later patcher's drawing with it: what it
    // delegates to is our link.
    const renderer = fakeRenderer();
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ renderer, store }));
    const notification = raiseAndDraw(toaster, store);

    const ours = renderer.prototype.render as SteamToastRenderFn;
    renderer.prototype.render = function (this: { props: SteamToastRenderProps }, ...args: unknown[]): ReactNode {
      return createElement("div", { "data-testid": "later-patcher" }, ours.apply(this, args) as ReactNode);
    };

    toaster.teardown();
    expect(renderer.prototype.render).not.toBe(ours);
    const drawn = drawWith(renderer, { group: groupFor(notification), location: 3 });
    // The later patcher still draws, and what it delegates to now falls through
    // to Steam's own instead of to a toast of ours.
    expect(drawn.queryByTestId("later-patcher")).not.toBeNull();
    expect(drawn.queryByText("Sync finished")).toBeNull();
    expect(drawn.queryByTestId("valve-drawing")).not.toBeNull();
  });

  it("does nothing at all when no toast was ever raised", () => {
    const renderer = fakeRenderer();
    createSteamToaster(seams({ renderer })).teardown();
    expect(renderer.prototype.render).toBeUndefined();
  });

  it("is safe to call twice, and a toast after it installs the drawing again", () => {
    const renderer = fakeRenderer();
    const store = fakeStore();
    const toaster = createSteamToaster(seams({ renderer, store }));
    toaster.toast(TOAST);
    toaster.teardown();
    toaster.teardown();

    const notification = raiseAndDraw(toaster, store);
    expect(
      drawWith(renderer, { group: groupFor(notification), location: 3 }).queryByText("Sync finished"),
    ).not.toBeNull();
  });
});
