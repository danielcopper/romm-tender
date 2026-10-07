import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useSyncExternalStore } from "react";

import { recheckStranded, toaster } from "../api/host";
import { setStrandedAnswer } from "../test-utils/stranded-panel";
import { setOwningQamTabActive } from "./owningQamTab";
import { useRecheckStrandedWhenOpened, useStrandedAnswer, watchStrandedPanel } from "./strandedPanelStore";

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
const setTabActive = (active: boolean) => act(() => setOwningQamTabActive(active));

vi.mock("./quickAccessVisible", () => ({
  useQuickAccessVisible: () => useSyncExternalStore(subscribeVisibility, () => qamVisible),
}));

const raised = () => vi.mocked(toaster.toast).mock.calls.map(([toast]) => toast);

describe("the notification a stranded panel raises", () => {
  let stop: () => void = () => {};

  beforeEach(() => {
    vi.mocked(toaster.toast).mockClear();
  });

  afterEach(() => {
    stop();
  });

  it.each([
    ["reloads", "It reloads Steam's interface once no game is running."],
    ["restart_steam", "Restart Steam to use it again."],
  ] as const)("is one, with the answer (%s) as its subtext, when the panel becomes stranded", (answer, detail) => {
    stop = watchStrandedPanel();

    setStrandedAnswer(answer);

    expect(raised()).toEqual([{ title: "Tender", body: "Tender was restarted", subtext: detail }]);
  });

  it("is one more each time the answer changes, and none for the same answer again", () => {
    stop = watchStrandedPanel();
    setStrandedAnswer("reloads");
    setStrandedAnswer("reloads");

    setStrandedAnswer("restart_steam");

    expect(raised().map((toast) => toast.subtext)).toEqual([
      "It reloads Steam's interface once no game is running.",
      "Restart Steam to use it again.",
    ]);
  });

  it("is raised for an answer the panel already held when watching began", () => {
    setStrandedAnswer("restart_steam");

    stop = watchStrandedPanel();

    expect(raised().map((toast) => toast.subtext)).toEqual(["Restart Steam to use it again."]);
  });

  it("is none while the panel is not stranded", () => {
    stop = watchStrandedPanel();

    expect(raised()).toEqual([]);
  });
});

describe("useStrandedAnswer", () => {
  it("re-renders with the answer as it arrives and as it changes", () => {
    const { result } = renderHook(() => useStrandedAnswer());
    expect(result.current).toBeNull();

    act(() => setStrandedAnswer("reloads"));
    expect(result.current).toBe("reloads");

    act(() => setStrandedAnswer("restart_steam"));
    expect(result.current).toBe("restart_steam");
  });
});

describe("useRecheckStrandedWhenOpened", () => {
  beforeEach(() => {
    vi.mocked(recheckStranded).mockClear();
  });

  afterEach(() => {
    qamVisible = true;
    setOwningQamTabActive(true);
  });

  it("asks once when Tender's page is opened, and not again while it stays open", () => {
    const { rerender } = renderHook(() => useRecheckStrandedWhenOpened());
    rerender();

    expect(recheckStranded).toHaveBeenCalledTimes(1);
  });

  it("asks again when the menu is closed and opened again, with the panel still mounted", () => {
    renderHook(() => useRecheckStrandedWhenOpened());

    setQamVisible(false);
    expect(recheckStranded).toHaveBeenCalledTimes(1);
    setQamVisible(true);

    expect(recheckStranded).toHaveBeenCalledTimes(2);
  });

  it("asks again when Tender's tab is chosen again after another one", () => {
    renderHook(() => useRecheckStrandedWhenOpened());

    setTabActive(false);
    setTabActive(true);

    expect(recheckStranded).toHaveBeenCalledTimes(2);
  });

  it("asks nothing while mounted behind a closed menu, and once it opens", () => {
    qamVisible = false;
    renderHook(() => useRecheckStrandedWhenOpened());
    expect(recheckStranded).not.toHaveBeenCalled();

    setQamVisible(true);

    expect(recheckStranded).toHaveBeenCalledTimes(1);
  });
});
