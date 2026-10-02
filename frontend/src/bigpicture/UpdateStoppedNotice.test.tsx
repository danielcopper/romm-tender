import "@testing-library/jest-dom/vitest";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, render, fireEvent, waitFor } from "@testing-library/react";
import { UpdateStoppedNotice } from "./UpdateStoppedNotice";
import { UpdateNotice } from "./UpdateNotice";
import { dismissStoppedUpdateAttempt, getStoppedUpdateAttempt } from "../api/backend";
import { fetchStoppedUpdateAttempt, resetStoppedUpdateStoreForTests } from "../utils/stoppedUpdateStore";
import { resetUpdateNoticeStoreForTests, setUpdateNoticeState } from "../utils/updateNoticeStore";

const stopAt = async (attempted: string) => {
  vi.mocked(getStoppedUpdateAttempt).mockResolvedValue({
    attempted_version: attempted,
    from_version: "1.0.0",
    started_at: "2026-09-29T10:00:00Z",
    toast_owed: false,
  });
  await act(() => fetchStoppedUpdateAttempt());
};

describe("UpdateStoppedNotice", () => {
  beforeEach(() => {
    resetStoppedUpdateStoreForTests();
    resetUpdateNoticeStoreForTests();
    vi.mocked(dismissStoppedUpdateAttempt).mockReset().mockResolvedValue({ success: true });
  });

  it("shows nothing while no installer stopped", () => {
    const { container } = render(<UpdateStoppedNotice onOpenUpdates={vi.fn()} />);
    expect(container.textContent).toBe("");
  });

  it("names both versions and points at the installer's journal", async () => {
    await stopAt("1.1.0");
    const { getByTestId } = render(<UpdateStoppedNotice onOpenUpdates={vi.fn()} />);

    const text = getByTestId("update-stopped-notice").textContent;
    expect(text).toContain("Update to 1.1.0 failed — you are still on 1.0.0.");
    expect(text).toContain("The installer stopped without updating. Details: journalctl --user -u romm-tender-update");
  });

  it("jumps to its home and is dismissed through the backend", async () => {
    await stopAt("1.1.0");
    const onOpenUpdates = vi.fn();
    const { getByText, queryByTestId } = render(<UpdateStoppedNotice onOpenUpdates={onOpenUpdates} />);

    fireEvent.click(getByText("Open Updates"));
    expect(onOpenUpdates).toHaveBeenCalledTimes(1);

    fireEvent.click(getByText("Dismiss"));
    await waitFor(() => expect(queryByTestId("update-stopped-notice")).toBeNull());
    expect(dismissStoppedUpdateAttempt).toHaveBeenCalledTimes(1);
  });

  it("takes the place of the available card for the version it tried", async () => {
    setUpdateNoticeState({
      available: true,
      newer: true,
      latestVersion: "1.1.0",
      currentVersion: "1.0.0",
      enabled: true,
      installedProgram: true,
      toastOwed: false,
      seen: false,
    });
    await stopAt("1.1.0");

    const { queryByTestId } = render(<UpdateNotice onOpenUpdates={vi.fn()} />);

    expect(queryByTestId("update-notice")).toBeNull();
  });

  it("leaves the available card for a newer version than the one it tried", async () => {
    setUpdateNoticeState({
      available: true,
      newer: true,
      latestVersion: "1.2.0",
      currentVersion: "1.0.0",
      enabled: true,
      installedProgram: true,
      toastOwed: false,
      seen: false,
    });
    await stopAt("1.1.0");

    const { getByTestId } = render(<UpdateNotice onOpenUpdates={vi.fn()} />);

    expect(getByTestId("update-notice")).toBeTruthy();
  });
});
