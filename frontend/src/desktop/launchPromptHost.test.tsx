import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { Window as HappyWindow } from "happy-dom";
import { fireEvent, screen } from "@testing-library/react";
import * as backend from "../api/backend";
import * as desktopWindow from "./desktopWindow";
import { askInDesktopWindow, offerDesktopLaunchPrompts, withdrawDesktopLaunchPrompts } from "./launchPromptHost";
import { desktopLaunchPrompts } from "./gameview/dialogs/desktopDialogs";
import { OFFLINE_DRIFT_RETRY_LABEL } from "../utils/launchPromptWording";
import { launchPromptsForThisStart } from "../utils/launchPromptRouter";
import type { LaunchPrompts } from "../utils/launchVerdict";

vi.mock("../api/backend", async (importActual) => ({
  ...(await importActual<typeof import("../api/backend")>()),
  logWarn: vi.fn(),
  logError: vi.fn(),
}));

vi.mock("./desktopWindow", () => ({
  findDesktopWindow: vi.fn(),
  findReactClient: vi.fn(),
}));

// The test's own window stands in for the desktop client's, and one test hands
// the host a second happy-dom window to pin which window is drawn into. That the
// cross-realm reads hold is device only.
const asTheDesktopWindow = (): void => {
  vi.mocked(desktopWindow.findDesktopWindow).mockReturnValue(window);
  vi.mocked(desktopWindow.findReactClient).mockReturnValue({ createRoot });
};

const hosts = (): Element[] => [...document.body.querySelectorAll(".tender-desktop-launch-prompt")];

const gamepad: LaunchPrompts = {
  confirmCoreChange: vi.fn(),
  resolveConflicts: vi.fn(),
  askOfflineDrift: vi.fn(),
  confirmFallbackLaunch: vi.fn(),
  confirmUncheckedSource: vi.fn(),
  askSyncWait: vi.fn(),
};

/** Ask a yes/no question whose dialog is a single "Yes" button. */
const askYes = (): Promise<string> =>
  askInDesktopWindow("dismissed", (resolve) => (
    <button type="button" onClick={() => resolve("yes")}>
      Yes
    </button>
  ));

const lines = (log: (msg: string) => unknown): string[] => vi.mocked(log).mock.calls.map((c) => String(c[0]));

beforeEach(() => {
  vi.mocked(backend.logWarn).mockClear();
  vi.mocked(backend.logError).mockClear();
  asTheDesktopWindow();
});

afterEach(() => {
  act(() => withdrawDesktopLaunchPrompts());
});

describe("askInDesktopWindow", () => {
  it("draws the dialog into the desktop window's body and takes it away once answered", async () => {
    let answer: Promise<string> | undefined;
    act(() => {
      answer = askYes();
    });

    expect(hosts()).toHaveLength(1);
    expect(hosts()[0]?.parentElement).toBe(document.body);

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    });

    await expect(answer).resolves.toBe("yes");
    expect(hosts()).toHaveLength(0);
  });

  it("draws into the body of the window it finds, not the panel's own document", async () => {
    const desktop = new HappyWindow();
    vi.mocked(desktopWindow.findDesktopWindow).mockReturnValue(desktop as unknown as Window);
    const drawnThere = (): boolean => desktop.document.body.querySelector(".tender-desktop-launch-prompt") !== null;
    let answer: Promise<string> | undefined;
    act(() => {
      answer = askYes();
    });

    expect(drawnThere()).toBe(true);
    expect(hosts()).toHaveLength(0);

    act(() => {
      desktop.dispatchEvent(new desktop.Event("pagehide"));
    });
    await expect(answer).resolves.toBe("dismissed");
    expect(drawnThere()).toBe(false);
    expect(lines(backend.logWarn)).toEqual([
      "desktop: a launch question was answered as dismissed because its window went",
    ]);
    await desktop.happyDOM.close();
  });

  it("dismisses a dialog on an Escape pressed in its own window, and not on one pressed in the panel's", async () => {
    const desktop = new HappyWindow();
    vi.mocked(desktopWindow.findDesktopWindow).mockReturnValue(desktop as unknown as Window);
    let answer: Promise<"start_anyway" | "retry" | "cancel"> | undefined;
    act(() => {
      answer = desktopLaunchPrompts(askInDesktopWindow).askOfflineDrift();
    });
    let settled = false;
    void answer?.then(() => {
      settled = true;
    });

    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    await Promise.resolve();
    expect(settled).toBe(false);

    act(() => {
      desktop.dispatchEvent(new desktop.KeyboardEvent("keydown", { key: "Escape" }));
    });
    await expect(answer).resolves.toBe("cancel");
    await desktop.happyDOM.close();
  });

  it("settles with the dismissed answer and takes the dialog away when the window goes", async () => {
    let answer: Promise<string> | undefined;
    act(() => {
      answer = askYes();
    });
    expect(hosts()).toHaveLength(1);

    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });

    await expect(answer).resolves.toBe("dismissed");
    expect(hosts()).toHaveLength(0);
    expect(lines(backend.logWarn)).toEqual([
      "desktop: a launch question was answered as dismissed because its window went",
    ]);
  });

  it("stops listening for the window going once the question is answered", async () => {
    const removeSpy = vi.spyOn(window, "removeEventListener");
    let answer: Promise<string> | undefined;
    act(() => {
      answer = askYes();
    });
    expect(removeSpy).not.toHaveBeenCalledWith("pagehide", expect.anything(), undefined);

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    });
    await expect(answer).resolves.toBe("yes");

    const removed = removeSpy.mock.calls.map(([type]) => type);
    expect(removed).toEqual(expect.arrayContaining(["pagehide", "unload"]));
  });

  it("unmounts an answered dialog, so its Escape listener goes with it", async () => {
    const removeSpy = vi.spyOn(window, "removeEventListener");
    let answer: Promise<"start_anyway" | "retry" | "cancel"> | undefined;
    act(() => {
      answer = desktopLaunchPrompts(askInDesktopWindow).askOfflineDrift();
    });
    expect(removeSpy).not.toHaveBeenCalledWith("keydown", expect.anything());

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: OFFLINE_DRIFT_RETRY_LABEL }));
    });
    await expect(answer).resolves.toBe("retry");

    expect(removeSpy.mock.calls.map(([type]) => type)).toContain("keydown");
  });

  it("logs an answer that arrives after its question was dismissed, and drops it", async () => {
    let late: ((value: string) => void) | undefined;
    let answer: Promise<string> | undefined;
    act(() => {
      answer = askInDesktopWindow("dismissed", (resolve) => {
        late = resolve;
        return <span>Resolving</span>;
      });
    });
    act(() => withdrawDesktopLaunchPrompts());
    await expect(answer).resolves.toBe("dismissed");

    late?.("keep_local");

    expect(lines(backend.logWarn)).toEqual([
      "desktop: a launch question was answered as dismissed because the desktop dialogs were withdrawn",
      "desktop: a launch question was answered after it had been dismissed, so the answer was dropped: keep_local",
    ]);
  });

  it("answers a closed window's question dismissed at once", async () => {
    vi.mocked(desktopWindow.findDesktopWindow).mockReturnValue({ closed: true } as Window);

    await expect(askYes()).resolves.toBe("dismissed");
    expect(lines(backend.logWarn)).toEqual([
      "desktop: a launch question had nothing to draw into (window=closed, createRoot=found), so it was answered as dismissed",
    ]);
  });

  it("answers dismissed at once when there is no desktop window", async () => {
    vi.mocked(desktopWindow.findDesktopWindow).mockReturnValue(undefined);

    await expect(askYes()).resolves.toBe("dismissed");
    expect(hosts()).toHaveLength(0);
    expect(lines(backend.logWarn)).toEqual([
      "desktop: a launch question had nothing to draw into (window=none, createRoot=found), so it was answered as dismissed",
    ]);
  });

  it("answers dismissed at once when React's createRoot cannot be found", async () => {
    vi.mocked(desktopWindow.findReactClient).mockReturnValue(undefined);

    await expect(askYes()).resolves.toBe("dismissed");
    expect(lines(backend.logWarn)).toEqual([
      "desktop: a launch question had nothing to draw into (window=open, createRoot=missing), so it was answered as dismissed",
    ]);
  });

  it.each([
    ["the desktop window", desktopWindow.findDesktopWindow],
    ["React's client module", desktopWindow.findReactClient],
  ])("answers dismissed when looking for %s throws", async (_what, lookup) => {
    vi.mocked(lookup).mockImplementation(() => {
      throw new Error("lookup boom");
    });

    await expect(askYes()).resolves.toBe("dismissed");
    expect(hosts()).toHaveLength(0);
    expect(lines(backend.logError)).toEqual([
      "desktop: could not look for a window to draw a launch question into, so it was answered as dismissed: Error: lookup boom",
    ]);
  });

  it("answers dismissed and leaves nothing behind when the root cannot be created", async () => {
    vi.mocked(desktopWindow.findReactClient).mockReturnValue({
      createRoot: () => {
        throw new Error("boom");
      },
    });

    await expect(askYes()).resolves.toBe("dismissed");
    expect(hosts()).toHaveLength(0);
    expect(lines(backend.logError)).toEqual([
      "desktop: could not draw a launch question, so it was answered as dismissed: Error: boom",
    ]);
  });

  it("answers dismissed and takes the dialog away when it throws while React renders it", async () => {
    const Broken = (): never => {
      throw new Error("render boom");
    };
    // Inside `act`, React rethrows a render error rather than handing it to
    // `onUncaughtError`, so this render runs outside it.
    const actEnvironment = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    actEnvironment.IS_REACT_ACT_ENVIRONMENT = false;
    try {
      await expect(askInDesktopWindow("dismissed", () => <Broken />)).resolves.toBe("dismissed");
    } finally {
      actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;
    }
    expect(hosts()).toHaveLength(0);
    expect(lines(backend.logError)).toEqual([
      "desktop: could not draw a launch question, so it was answered as dismissed: Error: render boom",
    ]);
  });

  it("settles with the dismissed answer when the window unloads", async () => {
    let answer: Promise<string> | undefined;
    act(() => {
      answer = askYes();
    });

    act(() => {
      window.dispatchEvent(new Event("unload"));
    });

    await expect(answer).resolves.toBe("dismissed");
    expect(hosts()).toHaveLength(0);
    expect(lines(backend.logWarn)).toEqual([
      "desktop: a launch question was answered as dismissed because its window unloaded",
    ]);
  });

  it("dismisses both of two open launch dialogs on one Escape", async () => {
    offerDesktopLaunchPrompts();
    vi.stubGlobal("SteamUIStore", { MainInstanceUIMode: 7, SetRunningApp: vi.fn() });
    const prompts = launchPromptsForThisStart(gamepad);
    let first: Promise<"start_anyway" | "retry" | "cancel"> | undefined;
    let second: Promise<boolean> | undefined;
    act(() => {
      first = prompts.askOfflineDrift();
      second = prompts.confirmUncheckedSource();
    });
    expect(hosts()).toHaveLength(2);

    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });

    await expect(first).resolves.toBe("cancel");
    await expect(second).resolves.toBe(false);
    expect(hosts()).toHaveLength(0);
  });

  it("dismisses a launch dialog on Escape", async () => {
    offerDesktopLaunchPrompts();
    vi.stubGlobal("SteamUIStore", { MainInstanceUIMode: 7, SetRunningApp: vi.fn() });
    const prompts = launchPromptsForThisStart(gamepad);
    expect(prompts).not.toBe(gamepad);

    let answer: Promise<"start_anyway" | "retry" | "cancel"> | undefined;
    act(() => {
      answer = prompts.askOfflineDrift();
    });
    expect(screen.getByRole("dialog", { name: "RomM Unreachable" })).toBeInTheDocument();

    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });

    await expect(answer).resolves.toBe("cancel");
    expect(hosts()).toHaveLength(0);
    expect(lines(backend.logWarn)).toEqual([]);
  });
});

describe("offering the desktop dialogs to the launch watcher", () => {
  beforeEach(() => {
    vi.stubGlobal("SteamUIStore", { MainInstanceUIMode: 7, SetRunningApp: vi.fn() });
  });

  it("offers them only while there is a desktop window to draw into", () => {
    offerDesktopLaunchPrompts();
    expect(launchPromptsForThisStart(gamepad)).not.toBe(gamepad);

    vi.mocked(desktopWindow.findDesktopWindow).mockReturnValue(undefined);
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
  });

  it("offers nothing while the desktop window it finds is closed", () => {
    offerDesktopLaunchPrompts();
    vi.mocked(desktopWindow.findDesktopWindow).mockReturnValue({ closed: true } as Window);

    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
  });

  it("withdrawing them settles every open question with its dismissed answer and takes the dialogs away", async () => {
    offerDesktopLaunchPrompts();
    let first: Promise<string> | undefined;
    let second: Promise<string> | undefined;
    act(() => {
      first = askYes();
      second = askInDesktopWindow("no", () => <span>Second</span>);
    });
    expect(hosts()).toHaveLength(2);

    act(() => withdrawDesktopLaunchPrompts());

    await expect(first).resolves.toBe("dismissed");
    await expect(second).resolves.toBe("no");
    expect(hosts()).toHaveLength(0);
    expect(launchPromptsForThisStart(gamepad)).toBe(gamepad);
    expect(lines(backend.logWarn)).toEqual([
      "desktop: a launch question was answered as dismissed because the desktop dialogs were withdrawn",
      "desktop: a launch question was answered as dismissed because the desktop dialogs were withdrawn",
    ]);
  });
});
