/**
 * Where the panel's update dot sits and what it carries. happy-dom lays
 * nothing out, so where the dot lands beside the word is a device question;
 * what is held here is that it takes no room and moves only in its fade.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { WithUpdateDot } from "./UpdateDot";
import { DOT_FADE_MS } from "../utils/updateDot";
import { resetUpdateNoticeStoreForTests, setUpdateNoticeState } from "../utils/updateNoticeStore";
import { resetUpdateOutcomeStoreForTests } from "../utils/updateOutcomeStore";
import { resetStoppedUpdateStoreForTests } from "../utils/stoppedUpdateStore";

const CARD = {
  available: true,
  newer: true,
  latestVersion: "1.1.0",
  currentVersion: "1.0.0",
  enabled: true,
  installedProgram: true,
  toastOwed: false,
  seen: false,
} as const;

const dotOf = (container: HTMLElement) => container.querySelector<HTMLElement>('[data-testid="update-dot"]');

describe("WithUpdateDot", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetUpdateNoticeStoreForTests();
    resetUpdateOutcomeStoreForTests();
    resetStoppedUpdateStoreForTests();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the label alone where no dot shows", () => {
    const { container } = render(<WithUpdateDot>Settings</WithUpdateDot>);

    expect(container.textContent).toBe("Settings");
    expect(dotOf(container)).toBeNull();
  });

  it("puts the dot beside the label, taking no room, in the card's blue, and still at rest", () => {
    setUpdateNoticeState(CARD);
    const { container } = render(<WithUpdateDot>Settings</WithUpdateDot>);

    const dot = dotOf(container);
    expect(container.textContent).toBe("Settings");
    expect(dot?.parentElement?.style.position).toBe("relative");
    expect(dot?.style.position).toBe("absolute");
    expect(dot?.style.left).toBe("100%");
    expect(dot?.style.width).toBe("9px");
    expect(dot?.getAttribute("aria-hidden")).toBe("true");
    expect(dot?.getAttribute("style")).not.toMatch(/animation|transition|transform|opacity/);
  });

  it("fades out once when its release is seen, then is gone", () => {
    setUpdateNoticeState(CARD);
    const { container } = render(<WithUpdateDot>Settings</WithUpdateDot>);

    act(() => setUpdateNoticeState({ ...CARD, seen: true }));
    const style = dotOf(container)?.getAttribute("style") ?? "";
    expect(style).toMatch(/transform: scale\(2\.2\)/);
    expect(style).toMatch(/opacity: 0/);
    expect(style).toMatch(/transition: transform 450ms ease-out, opacity 450ms ease-out/);

    act(() => {
      vi.advanceTimersByTime(DOT_FADE_MS);
    });
    expect(dotOf(container)).toBeNull();
    expect(container.textContent).toBe("Settings");
  });

  it("goes without a fade when the card is dismissed", () => {
    setUpdateNoticeState(CARD);
    const { container } = render(<WithUpdateDot>Settings</WithUpdateDot>);

    act(() => setUpdateNoticeState({ ...CARD, available: false }));

    expect(dotOf(container)).toBeNull();
  });
});
