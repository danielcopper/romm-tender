import { describe, it, expect, beforeEach, vi } from "vitest";
import { showModal } from "@decky/ui";
import type { ReactElement } from "react";
import { showUncheckedSourceModal } from "./UncheckedSourceModal";

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

describe("UncheckedSourceModal — showUncheckedSourceModal", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
  });

  it("asks the question with Start and Cancel", () => {
    void showUncheckedSourceModal();
    const props = lastConfirmModalProps();
    expect(props.strDescription).toBe(
      "Tender could not check whether the emulator source is switched on. Start anyway?",
    );
    expect(props.strOKButtonText).toBe("Start");
    expect(props.strCancelButtonText).toBe("Cancel");
  });

  it("resolves true on Start", async () => {
    const answer = showUncheckedSourceModal();
    lastConfirmModalProps().onOK?.();
    await expect(answer).resolves.toBe(true);
  });

  it("resolves false on Cancel", async () => {
    const answer = showUncheckedSourceModal();
    lastConfirmModalProps().onCancel?.();
    await expect(answer).resolves.toBe(false);
  });
});
