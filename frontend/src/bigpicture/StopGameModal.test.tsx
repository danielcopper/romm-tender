import { describe, it, expect, beforeEach, vi } from "vitest";
import { showModal } from "@decky/ui";
import type { ReactElement } from "react";
import { showStopGameModal } from "./StopGameModal";

// The global @decky/ui stub (frontend/src/test-setup.ts) exposes showModal as a
// vi.fn; the element handed to it carries the ConfirmModal's props.
interface ConfirmModalProps {
  strTitle?: string;
  strDescription?: string;
  strOKButtonText?: string;
  strCancelButtonText?: string;
  onOK?: () => void;
  onCancel?: () => void;
}

function lastConfirmModalProps(): ConfirmModalProps {
  const calls = vi.mocked(showModal).mock.calls;
  const el = calls[calls.length - 1]?.[0] as ReactElement<ConfirmModalProps> | undefined;
  if (!el) throw new Error("showModal was not called");
  return el.props;
}

describe("StopGameModal — showStopGameModal", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
  });

  it("asks Stop Game? with the lost-progress sentence and Stop Game / Cancel buttons", () => {
    void showStopGameModal();
    const props = lastConfirmModalProps();
    expect(props.strTitle).toBe("Stop Game?");
    expect(props.strDescription).toBe("Any progress since the last in-game save may be lost.");
    expect(props.strOKButtonText).toBe("Stop Game");
    expect(props.strCancelButtonText).toBe("Cancel");
  });

  it("resolves true when Stop Game is pressed", async () => {
    const promise = showStopGameModal();
    lastConfirmModalProps().onOK?.();
    await expect(promise).resolves.toBe(true);
  });

  it("resolves false when Cancel is pressed", async () => {
    const promise = showStopGameModal();
    lastConfirmModalProps().onCancel?.();
    await expect(promise).resolves.toBe(false);
  });
});
