import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { toaster } from "../api/host";
import { setStrandedAnswer } from "../test-utils/stranded-panel";
import { useStrandedAnswer, watchStrandedPanel } from "./strandedPanelStore";

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
