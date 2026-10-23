import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import * as backend from "../api/backend";
import type { Achievement, AchievementList, AchievementProgress } from "../types";
import {
  getRommConnectionState,
  getServerRetryProgress,
  reportServerReachable,
  setRommConnectionState,
  setServerRetryProgress,
} from "./connectionState";
import { earnedByBadgeId, orderEarnedFirst, useAchievements } from "./achievements";

type ListAnswer = Awaited<ReturnType<typeof backend.getAchievements>>;
type ProgressAnswer = Awaited<ReturnType<typeof backend.getAchievementProgress>>;

function cheevo(raId: number, badgeId: string, displayOrder: number): Achievement {
  return {
    ra_id: raId,
    badge_id: badgeId,
    title: `Title ${raId}`,
    description: "",
    points: 5,
    badge_url: "",
    badge_url_lock: "",
    display_order: displayOrder,
    type: "",
    num_awarded: 0,
    num_awarded_hardcore: 0,
  };
}

const LIST: AchievementList = { success: true, total: 2, achievements: [cheevo(1, "b1", 1), cheevo(2, "b2", 2)] };
const PROGRESS: AchievementProgress = {
  success: true,
  earned: 1,
  total: 2,
  earned_achievements: [{ id: "b1", date: "2025-02-14 15:45:38", date_hardcore: null }],
};
const UNREACHABLE = { success: false, reason: "server_unreachable", message: "down" } as const;

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function mount(romId = 88, raId: number | null = 42, isActive = true) {
  return renderHook((props: { isActive: boolean }) => useAchievements(romId, raId, props.isActive), {
    initialProps: { isActive },
  });
}

describe("useAchievements", () => {
  beforeEach(() => {
    setRommConnectionState("connected");
    setServerRetryProgress(null);
    vi.mocked(backend.getAchievements).mockReset();
    vi.mocked(backend.getAchievementProgress).mockReset();
    vi.mocked(backend.debugLog).mockClear();
    vi.mocked(backend.getAchievements).mockResolvedValue(LIST);
    vi.mocked(backend.getAchievementProgress).mockResolvedValue(PROGRESS);
  });

  it("loads nothing until it is first active, then both answers once, and keeps them while inactive", async () => {
    const hook = mount(88, 42, false);
    await settle();
    expect(backend.getAchievements).not.toHaveBeenCalled();
    expect(backend.getAchievementProgress).not.toHaveBeenCalled();

    hook.rerender({ isActive: true });
    await settle();
    expect(backend.getAchievements).toHaveBeenCalledExactlyOnceWith(88);
    expect(backend.getAchievementProgress).toHaveBeenCalledExactlyOnceWith(88);
    expect(hook.result.current).toEqual({
      achievements: LIST.achievements,
      progress: PROGRESS,
      loading: false,
      isOffline: false,
    });

    hook.rerender({ isActive: false });
    expect(hook.result.current).toEqual({
      achievements: LIST.achievements,
      progress: PROGRESS,
      loading: false,
      isOffline: false,
    });
    hook.rerender({ isActive: true });
    await settle();
    expect(backend.getAchievements).toHaveBeenCalledTimes(1);
    expect(hook.result.current).toEqual({
      achievements: LIST.achievements,
      progress: PROGRESS,
      loading: false,
      isOffline: false,
    });
  });

  it("loads nothing without a RetroAchievements id", async () => {
    mount(88, null);
    await settle();
    expect(backend.getAchievements).not.toHaveBeenCalled();
  });

  it("is loading while the two calls are in flight", async () => {
    vi.mocked(backend.getAchievements).mockReturnValue(new Promise(() => {}));
    const hook = mount();
    await settle();
    expect(hook.result.current.loading).toBe(true);
  });

  it("drops a prior load's retry progress when it starts, and its own once it settles", async () => {
    setServerRetryProgress({ attempt: 2, maxAttempts: 3 });
    const list = deferred<ListAnswer>();
    vi.mocked(backend.getAchievements).mockReturnValue(list.promise);
    mount();
    await settle();
    expect(getServerRetryProgress()).toBeNull();

    act(() => setServerRetryProgress({ attempt: 1, maxAttempts: 3 }));
    list.resolve(LIST);
    await settle();
    expect(getServerRetryProgress()).toBeNull();
  });

  it("while known offline, calls nothing and answers isOffline, then loads on reconnect", async () => {
    setRommConnectionState("offline");
    const hook = mount();
    await settle();
    expect(backend.getAchievements).not.toHaveBeenCalled();
    expect(hook.result.current).toEqual({ achievements: [], progress: null, loading: false, isOffline: true });

    await act(async () => {
      reportServerReachable(true);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(backend.getAchievements).toHaveBeenCalledExactlyOnceWith(88);
    expect(hook.result.current.achievements).toEqual(LIST.achievements);
  });

  it.each([
    ["the list", "getAchievements" as const],
    ["the progress", "getAchievementProgress" as const],
  ])("an unreachable answer to %s reports offline and lets a reconnect load again", async (_what, call) => {
    vi.mocked(backend[call]).mockResolvedValueOnce(UNREACHABLE);
    mount();
    await settle();
    expect(getRommConnectionState()).toBe("offline");

    await act(async () => {
      reportServerReachable(true);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(backend[call]).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["a fresh list", { ...LIST, stale: false }, { ...PROGRESS, stale: true }],
    ["fresh progress", { ...LIST, stale: true }, { ...PROGRESS, stale: false }],
  ])("%s reports the server reachable", async (_what, list, progress) => {
    setRommConnectionState("checking");
    vi.mocked(backend.getAchievements).mockResolvedValue(list);
    vi.mocked(backend.getAchievementProgress).mockResolvedValue(progress);
    mount();
    await settle();
    expect(getRommConnectionState()).toBe("connected");
  });

  it.each([
    ["two stale answers", { ...LIST, stale: true }, { ...PROGRESS, stale: true }],
    [
      "a stale list beside a no_ra_username failure",
      { ...LIST, stale: true },
      { success: false, reason: "no_ra_username", message: "No RA username configured in RomM" } as const,
    ],
    [
      "a no_ra_username list failure beside stale progress",
      { success: false, reason: "no_ra_username", message: "No RA username configured in RomM" } as const,
      { ...PROGRESS, stale: true },
    ],
    [
      "a not_found list failure beside stale progress",
      { success: false, reason: "not_found", message: "gone" } as const,
      { ...PROGRESS, stale: true },
    ],
    [
      "a stale list beside an auth_failed progress failure",
      { ...LIST, stale: true },
      { success: false, reason: "auth_failed", message: "denied" } as const,
    ],
  ])("%s leave the connection state alone and stand", async (_what, list: ListAnswer, progress: ProgressAnswer) => {
    setRommConnectionState("checking");
    vi.mocked(backend.getAchievements).mockResolvedValue(list);
    vi.mocked(backend.getAchievementProgress).mockResolvedValue(progress);
    const hook = mount();
    await settle();
    expect(getRommConnectionState()).toBe("checking");

    hook.rerender({ isActive: false });
    hook.rerender({ isActive: true });
    await settle();
    expect(backend.getAchievements).toHaveBeenCalledTimes(1);
    expect(backend.getAchievementProgress).toHaveBeenCalledTimes(1);
  });

  it("keeps the last-known list and progress where a later load fails", async () => {
    vi.mocked(backend.getAchievementProgress).mockResolvedValueOnce(UNREACHABLE);
    const hook = mount();
    await settle();
    expect(hook.result.current.achievements).toEqual(LIST.achievements);
    expect(hook.result.current.progress).toBeNull();

    vi.mocked(backend.getAchievements).mockResolvedValueOnce(UNREACHABLE);
    await act(async () => {
      reportServerReachable(true);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(backend.getAchievements).toHaveBeenCalledTimes(2);
    expect(hook.result.current.achievements).toEqual(LIST.achievements);
    expect(hook.result.current.progress).toEqual(PROGRESS);

    vi.mocked(backend.getAchievementProgress).mockResolvedValueOnce(UNREACHABLE);
    await act(async () => {
      reportServerReachable(true);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(backend.getAchievementProgress).toHaveBeenCalledTimes(3);
    expect(hook.result.current.progress).toEqual(PROGRESS);
  });

  it("a call that throws is logged, ends loading, and lets the next activation load again", async () => {
    vi.mocked(backend.getAchievements).mockRejectedValueOnce(new Error("net"));
    const hook = mount();
    await settle();
    expect(backend.debugLog).toHaveBeenCalledExactlyOnceWith("Failed to load achievements: Error: net");
    expect(hook.result.current.loading).toBe(false);

    hook.rerender({ isActive: false });
    hook.rerender({ isActive: true });
    await settle();
    expect(backend.getAchievements).toHaveBeenCalledTimes(2);
    expect(hook.result.current.achievements).toEqual(LIST.achievements);
  });

  it("drops its retry progress when a call throws", async () => {
    let rejectList!: (e: Error) => void;
    vi.mocked(backend.getAchievements).mockReturnValueOnce(
      new Promise<ListAnswer>((_resolve, reject) => {
        rejectList = reject;
      }),
    );
    mount();
    await settle();

    act(() => setServerRetryProgress({ attempt: 2, maxAttempts: 3 }));
    rejectList(new Error("net"));
    await settle();
    expect(getServerRetryProgress()).toBeNull();
  });

  it("torn down mid-flight, it ends loading, ignores the late answer, and loads again on reconnect", async () => {
    const list = deferred<ListAnswer>();
    vi.mocked(backend.getAchievements).mockReturnValueOnce(list.promise);
    const hook = mount();
    await settle();
    expect(hook.result.current.loading).toBe(true);

    act(() => setRommConnectionState("offline"));
    expect(hook.result.current.loading).toBe(false);
    act(() => setServerRetryProgress({ attempt: 1, maxAttempts: 3 }));
    list.resolve({ ...LIST, achievements: [cheevo(9, "b9", 9)] });
    await settle();
    expect(hook.result.current.achievements).toEqual([]);
    expect(getServerRetryProgress()).toBeNull();

    await act(async () => {
      reportServerReachable(true);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(backend.getAchievements).toHaveBeenCalledTimes(2);
    expect(hook.result.current.achievements).toEqual(LIST.achievements);
  });

  it("a torn-down load that throws late leaves the newer load in flight", async () => {
    let rejectFirst!: (e: Error) => void;
    const second = deferred<ListAnswer>();
    vi.mocked(backend.getAchievements)
      .mockReturnValueOnce(
        new Promise<ListAnswer>((_resolve, reject) => {
          rejectFirst = reject;
        }),
      )
      .mockReturnValueOnce(second.promise);
    const hook = mount();
    await settle();

    act(() => setRommConnectionState("offline"));
    await act(async () => {
      reportServerReachable(true);
      await Promise.resolve();
    });
    expect(backend.getAchievements).toHaveBeenCalledTimes(2);
    expect(hook.result.current.loading).toBe(true);

    rejectFirst(new Error("late"));
    await settle();
    expect(hook.result.current.loading).toBe(true);

    second.resolve(LIST);
    await settle();
    hook.rerender({ isActive: false });
    hook.rerender({ isActive: true });
    await settle();
    expect(backend.getAchievements).toHaveBeenCalledTimes(2);
  });
});

describe("earnedByBadgeId", () => {
  it("keys each earned entry by its id, which is the achievement's badge id", () => {
    const map = earnedByBadgeId(PROGRESS);
    expect([...map.keys()]).toEqual(["b1"]);
    expect(map.get("b1")).toBe(PROGRESS.earned_achievements[0]);
  });

  it("is empty without progress", () => {
    expect(earnedByBadgeId(null).size).toBe(0);
  });
});

describe("orderEarnedFirst", () => {
  it("puts the earned first, each group by display order, and leaves its input alone", () => {
    const input = [cheevo(4, "b4", 4), cheevo(3, "b3", 2), cheevo(1, "b1", 3), cheevo(2, "b2", 1)];
    const earned = earnedByBadgeId({
      ...PROGRESS,
      earned_achievements: [
        { id: "b4", date: "2025-01-01 00:00:00", date_hardcore: null },
        { id: "b1", date: "2025-01-02 00:00:00", date_hardcore: null },
      ],
    });
    expect(orderEarnedFirst(input, earned).map((a) => a.ra_id)).toEqual([1, 4, 2, 3]);
    expect(input.map((a) => a.ra_id)).toEqual([4, 3, 1, 2]);
  });

  it("orders a missing display order as 0", () => {
    const noOrder = { ...cheevo(1, "b1", 0), display_order: undefined as unknown as number };
    expect(orderEarnedFirst([cheevo(2, "b2", 1), noOrder], new Map()).map((a) => a.ra_id)).toEqual([1, 2]);
  });
});
