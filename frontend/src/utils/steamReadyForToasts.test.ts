import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { detach } from "./detach";
import {
  TOAST_READINESS_DEADLINE_MS,
  TOAST_READINESS_POLL_MS,
  waitUntilSteamCanShowToasts,
  type ToastReadiness,
} from "./steamReadyForToasts";

/** Steam as the three conditions read it; each test changes a field to move it along. */
interface SteamState {
  services: boolean;
  locked: boolean;
  bigPicture: "none" | "hidden" | "visible";
}

function stubSteam(state: SteamState): void {
  vi.stubGlobal("App", { GetServicesInitialized: () => state.services });
  vi.stubGlobal("securitystore", { IsLockScreenActive: () => state.locked });
  vi.stubGlobal("SteamUIStore", {
    WindowStore: {
      get GamepadUIMainWindowInstance() {
        if (state.bigPicture === "none") return null;
        return { BrowserWindow: { document: { visibilityState: state.bigPicture } } };
      },
    },
  });
}

/** Start the wait and report whether it has ended, without awaiting it. */
function startWaiting(): { answer: () => ToastReadiness | null } {
  let answer: ToastReadiness | null = null;
  detach(
    waitUntilSteamCanShowToasts().then((a) => {
      answer = a;
    }),
  );
  return { answer: () => answer };
}

describe("waitUntilSteamCanShowToasts", () => {
  let state: SteamState;

  beforeEach(() => {
    vi.useFakeTimers();
    state = { services: true, locked: false, bigPicture: "visible" };
    stubSteam(state);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("answers at once when Steam's services are up, the lock screen is down and Big Picture is visible", async () => {
    const wait = startWaiting();
    await vi.advanceTimersByTimeAsync(0);

    expect(wait.answer()).toEqual({ inTime: true, unmet: [] });
  });

  it("counts the desktop client, which has no Big Picture window, as visible", async () => {
    state.bigPicture = "none";
    const wait = startWaiting();
    await vi.advanceTimersByTimeAsync(0);

    expect(wait.answer()).toEqual({ inTime: true, unmet: [] });
  });

  const UNMET: Record<string, Partial<SteamState>> = {
    services_initialized: { services: false },
    lock_screen_inactive: { locked: true },
    big_picture_window_visible: { bigPicture: "hidden" },
  };

  it.each(Object.keys(UNMET))("waits while %s is unmet and answers on the next round once it is met", async (key) => {
    const met = { ...state };
    Object.assign(state, UNMET[key]);
    const wait = startWaiting();
    await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS * 4);
    expect(wait.answer()).toBeNull();

    Object.assign(state, met);
    await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS);

    expect(wait.answer()).toEqual({ inTime: true, unmet: [] });
  });

  it("gives up at the deadline, not before, and names every condition still unmet", async () => {
    state.services = false;
    state.bigPicture = "hidden";
    const wait = startWaiting();

    await vi.advanceTimersByTimeAsync(TOAST_READINESS_DEADLINE_MS - 1);
    expect(wait.answer()).toBeNull();
    await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS);

    expect(wait.answer()).toEqual({
      inTime: false,
      unmet: ["services_initialized", "big_picture_window_visible"],
    });
  });

  describe("Steam's own wait for its services", () => {
    function stubServicesWait(getter: boolean): { resolve: () => void; reject: () => void } {
      let resolve!: () => void;
      let reject!: () => void;
      const waiting = new Promise<boolean>((res, rej) => {
        resolve = () => res(true);
        reject = () => rej(new Error("gone"));
      });
      vi.stubGlobal("App", { WaitForServicesInitialized: () => waiting, GetServicesInitialized: () => getter });
      return { resolve, reject };
    }

    it("is what decides where it exists, whatever the getter says", async () => {
      const services = stubServicesWait(true);
      const wait = startWaiting();
      await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS * 4);
      expect(wait.answer()).toBeNull();

      services.resolve();
      await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS);

      expect(wait.answer()).toEqual({ inTime: true, unmet: [] });
    });

    it("hands the question to the getter once it rejects", async () => {
      const services = stubServicesWait(true);
      const wait = startWaiting();

      services.reject();
      await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS);

      expect(wait.answer()).toEqual({ inTime: true, unmet: [] });
    });
  });

  describe("a condition nothing can read counts as unmet", () => {
    it.each([
      ["App is absent", () => vi.stubGlobal("App", undefined), "services_initialized"],
      [
        "the services getter throws",
        () =>
          vi.stubGlobal("App", {
            GetServicesInitialized: () => {
              throw new Error("boom");
            },
          }),
        "services_initialized",
      ],
      ["securitystore is absent", () => vi.stubGlobal("securitystore", undefined), "lock_screen_inactive"],
      [
        "the lock screen answers something other than a boolean",
        () => vi.stubGlobal("securitystore", { IsLockScreenActive: () => undefined }),
        "lock_screen_inactive",
      ],
      ["SteamUIStore is absent", () => vi.stubGlobal("SteamUIStore", undefined), "big_picture_window_visible"],
      [
        "the window store throws",
        () =>
          vi.stubGlobal("SteamUIStore", {
            get WindowStore() {
              throw new Error("boom");
            },
          }),
        "big_picture_window_visible",
      ],
    ])("%s", async (_case, breakIt, unmet) => {
      breakIt();
      const wait = startWaiting();

      await vi.advanceTimersByTimeAsync(TOAST_READINESS_DEADLINE_MS + TOAST_READINESS_POLL_MS);

      expect(wait.answer()).toEqual({ inTime: false, unmet: [unmet] });
    });
  });
});
