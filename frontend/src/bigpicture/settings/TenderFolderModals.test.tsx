import { beforeEach, describe, expect, it, vi } from "vitest";
import { showModal } from "@decky/ui";
import type { ReactElement } from "react";
import { showCreateMissingModal, showForgetRecordedModal, showTenderNamedModal } from "./TenderFolderModals";

interface ConfirmModalProps {
  strTitle?: string;
  strDescription?: string;
  strOKButtonText?: string;
  strMiddleButtonText?: string;
  strCancelButtonText?: string;
  onOK?: () => void;
  onMiddleButton?: () => void;
  onCancel?: () => void;
}

function lastProps(): ConfirmModalProps {
  const calls = vi.mocked(showModal).mock.calls;
  const el = calls[calls.length - 1]?.[0] as ReactElement<ConfirmModalProps> | undefined;
  if (!el) throw new Error("showModal was not called");
  return el.props;
}

describe("the confirmations around Tender's folder", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
  });

  it("forgetting names the folder, says no file is deleted, and resolves true only on Forget", async () => {
    const forgot = showForgetRecordedModal("/sd/Tender");
    const props = lastProps();
    expect(props.strTitle).toBe("Forget everything Tender has recorded under /sd/Tender?");
    expect(props.strDescription).toBe(
      "Its games will show as not downloaded, and Tender's links to its files are removed. No file is deleted.",
    );
    expect([props.strOKButtonText, props.strCancelButtonText]).toEqual(["Forget", "Cancel"]);
    props.onOK?.();
    await expect(forgot).resolves.toBe(true);

    const kept = showForgetRecordedModal("/sd/Tender");
    lastProps().onCancel?.();
    await expect(kept).resolves.toBe(false);
  });

  it("asks before creating missing folders, with Create and Cancel", async () => {
    const created = showCreateMissingModal("/home/deck/Games", "/home/deck", "Games");
    const props = lastProps();
    expect(props.strDescription).toBe(
      "/home/deck/Games does not exist yet; only /home/deck does. Create Games/ there?",
    );
    expect([props.strOKButtonText, props.strCancelButtonText]).toEqual(["Create", "Cancel"]);
    props.onCancel?.();
    await expect(created).resolves.toBe(false);
  });

  it("asks about a place named Tender: its parent, as typed, or nothing at all", async () => {
    const parent = showTenderNamedModal("/sd/Tender", "/sd");
    const props = lastProps();
    expect(props.strOKButtonText).toBe("Use /sd");
    expect(props.strMiddleButtonText).toBe("Keep as typed");
    props.onOK?.();
    await expect(parent).resolves.toBe("parent");

    const typed = showTenderNamedModal("/sd/Tender", "/sd");
    lastProps().onMiddleButton?.();
    await expect(typed).resolves.toBe("typed");

    const nothing = showTenderNamedModal("/sd/Tender", "/sd");
    lastProps().onCancel?.();
    await expect(nothing).resolves.toBe("cancel");
  });
});
