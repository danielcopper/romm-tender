import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import { markUpdateAvailableSeen } from "../api/backend";
import { DOT_FADE_MS, SEEN_AFTER_MS, useSeenAfterDwell, useUpdateDot } from "./updateDot";
import { resetUpdateNoticeStoreForTests, setUpdateNoticeState, type UpdateNoticeState } from "./updateNoticeStore";
import { resetUpdateOutcomeStoreForTests, setUpdateOutcomeState, type UpdateOutcomeState } from "./updateOutcomeStore";
import { resetStoppedUpdateStoreForTests } from "./stoppedUpdateStore";

// The Quick Access menu's own visibility, backed by a store the tests flip:
// test-setup.ts's `() => true` cannot close the menu.
let qamVisible = true;
const visibilityListeners = new Set<() => void>();
const subscribeVisibility = (onChange: () => void) => {
  visibilityListeners.add(onChange);
  return () => {
    visibilityListeners.delete(onChange);
  };
};
const setQamVisible = (visible: boolean) =>
  act(() => {
    qamVisible = visible;
    visibilityListeners.forEach((fn) => fn());
  });

vi.mock("./quickAccessVisible", () => ({
  useQuickAccessVisible: () => useSyncExternalStore(subscribeVisibility, () => qamVisible),
}));

const AVAILABLE: UpdateNoticeState = {
  available: true,
  newer: true,
  latestVersion: "1.1.0",
  currentVersion: "1.0.0",
  enabled: true,
  installedProgram: true,
  toastOwed: false,
  seen: false,
};

/** Move the fake clock on by *ms*, inside act so what the timers set is rendered. */
const pass = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });

/** Let a resolved write's continuation run, inside act so its store write is flushed. */
const settle = () => act(async () => undefined);

describe("useUpdateDot", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetUpdateNoticeStoreForTests();
    resetUpdateOutcomeStoreForTests();
    resetStoppedUpdateStoreForTests();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("is not drawn before any store has answered", () => {
    expect(renderHook(() => useUpdateDot()).result.current).toBe("none");
  });

  it("is drawn while the card would show and its release was not seen", () => {
    setUpdateNoticeState(AVAILABLE);

    expect(renderHook(() => useUpdateDot()).result.current).toBe("shown");
  });

  it("is not drawn, and does not fade, where the release was seen before it mounted", () => {
    setUpdateNoticeState({ ...AVAILABLE, seen: true });

    const { result } = renderHook(() => useUpdateDot());

    expect(result.current).toBe("none");
  });

  it("fades once when its release is seen, then is gone for good", () => {
    setUpdateNoticeState(AVAILABLE);
    const { result } = renderHook(() => useUpdateDot());

    act(() => setUpdateNoticeState({ ...AVAILABLE, seen: true }));
    expect(result.current).toBe("fading");

    pass(DOT_FADE_MS - 1);
    expect(result.current).toBe("fading");
    pass(1);
    expect(result.current).toBe("none");

    act(() => setUpdateNoticeState({ ...AVAILABLE, seen: true, enabled: false }));
    pass(DOT_FADE_MS);
    expect(result.current).toBe("none");
  });

  it.each<[string, Partial<UpdateNoticeState>]>([
    ["the card is dismissed", { available: false }],
    ["the release is installed", { available: false, newer: false, currentVersion: "1.1.0" }],
  ])("goes without a fade when %s", (_, change) => {
    setUpdateNoticeState(AVAILABLE);
    const { result } = renderHook(() => useUpdateDot());

    act(() => setUpdateNoticeState({ ...AVAILABLE, ...change }));

    expect(result.current).toBe("none");
  });

  it("goes without a fade when a failed update to its release takes the card's place", () => {
    setUpdateNoticeState(AVAILABLE);
    const { result } = renderHook(() => useUpdateDot());

    act(() =>
      setUpdateOutcomeState({
        announcement: null,
        failure: {
          attemptedVersion: "1.1.0",
          restoredVersion: "1.0.0",
          rolledBackAt: "2026-09-29T10:00:00Z",
          kind: "rollback",
        },
        failureDismissed: false,
      }),
    );

    expect(result.current).toBe("none");
  });

  it("comes back for a newer release, at rest", () => {
    setUpdateNoticeState({ ...AVAILABLE, seen: true });
    const { result } = renderHook(() => useUpdateDot());

    act(() => setUpdateNoticeState({ ...AVAILABLE, latestVersion: "1.2.0" }));

    expect(result.current).toBe("shown");
  });

  it("draws no dot, and does not throw, over a store state the answer cannot be worked out from", () => {
    setUpdateNoticeState(AVAILABLE);
    setUpdateOutcomeState(null as unknown as UpdateOutcomeState);

    expect(renderHook(() => useUpdateDot()).result.current).toBe("none");
  });
});

describe("useSeenAfterDwell", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetUpdateNoticeStoreForTests();
    resetUpdateOutcomeStoreForTests();
    resetStoppedUpdateStoreForTests();
    vi.mocked(markUpdateAvailableSeen).mockReset().mockResolvedValue({ success: true });
    qamVisible = true;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const dwell = (shown: boolean) =>
    renderHook(({ on }: { on: boolean }) => useSeenAfterDwell(on), { initialProps: { on: shown } });

  it("records nothing when Updates is left a moment before the second is up", () => {
    setUpdateNoticeState(AVAILABLE);
    const { rerender } = dwell(true);

    pass(SEEN_AFTER_MS - 1);
    rerender({ on: false });
    pass(SEEN_AFTER_MS * 5);

    expect(markUpdateAvailableSeen).not.toHaveBeenCalled();
  });

  it("records the release once, after a second on Updates", async () => {
    setUpdateNoticeState(AVAILABLE);
    dwell(true);

    pass(SEEN_AFTER_MS);
    await settle();
    pass(SEEN_AFTER_MS * 5);
    await settle();

    expect(markUpdateAvailableSeen).toHaveBeenCalledExactlyOnceWith("1.1.0");
  });

  it("starts the wait over when Updates is left and shown again", async () => {
    setUpdateNoticeState(AVAILABLE);
    const { rerender } = dwell(true);

    pass(SEEN_AFTER_MS - 100);
    rerender({ on: false });
    rerender({ on: true });
    pass(SEEN_AFTER_MS - 1);
    expect(markUpdateAvailableSeen).not.toHaveBeenCalled();

    pass(1);
    await settle();
    expect(markUpdateAvailableSeen).toHaveBeenCalledExactlyOnceWith("1.1.0");
  });

  it("counts the wait from when the card's release first shows on Updates", async () => {
    const { rerender } = dwell(true);
    pass(SEEN_AFTER_MS * 2);
    expect(markUpdateAvailableSeen).not.toHaveBeenCalled();

    act(() => setUpdateNoticeState(AVAILABLE));
    rerender({ on: true });
    pass(SEEN_AFTER_MS);
    await settle();

    expect(markUpdateAvailableSeen).toHaveBeenCalledExactlyOnceWith("1.1.0");
  });

  it.each<[string, Partial<UpdateNoticeState>]>([
    ["was already seen", { seen: true }],
    ["has no card", { available: false }],
  ])("records nothing for a release that %s", (_, change) => {
    setUpdateNoticeState({ ...AVAILABLE, ...change });
    dwell(true);

    pass(SEEN_AFTER_MS * 2);

    expect(markUpdateAvailableSeen).not.toHaveBeenCalled();
  });

  it("records nothing when the menu is closed a moment before the second is up", () => {
    setUpdateNoticeState(AVAILABLE);
    dwell(true);

    pass(SEEN_AFTER_MS - 1);
    setQamVisible(false);
    pass(SEEN_AFTER_MS * 5);

    expect(markUpdateAvailableSeen).not.toHaveBeenCalled();
  });

  it("records nothing for a newer release that arrives while the menu is closed on Updates", () => {
    setUpdateNoticeState({ ...AVAILABLE, seen: true });
    dwell(true);
    setQamVisible(false);

    act(() => setUpdateNoticeState({ ...AVAILABLE, latestVersion: "1.2.0" }));
    pass(SEEN_AFTER_MS * 5);

    expect(markUpdateAvailableSeen).not.toHaveBeenCalled();
  });

  it("starts the wait over when the menu is opened again on Updates", async () => {
    setUpdateNoticeState(AVAILABLE);
    dwell(true);

    pass(SEEN_AFTER_MS - 100);
    setQamVisible(false);
    setQamVisible(true);
    pass(SEEN_AFTER_MS - 1);
    expect(markUpdateAvailableSeen).not.toHaveBeenCalled();

    pass(1);
    await settle();
    expect(markUpdateAvailableSeen).toHaveBeenCalledExactlyOnceWith("1.1.0");
  });

  it("records nothing while Updates is not the section on screen", () => {
    setUpdateNoticeState(AVAILABLE);
    dwell(false);

    pass(SEEN_AFTER_MS * 2);

    expect(markUpdateAvailableSeen).not.toHaveBeenCalled();
  });
});
