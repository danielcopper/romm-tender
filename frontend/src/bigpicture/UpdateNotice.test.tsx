import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { UpdateNotice } from "./UpdateNotice";
import * as backend from "../api/backend";
import { dismissUpdateNotice } from "../api/backend";
import {
  getUpdateNoticeState,
  resetUpdateNoticeStoreForTests,
  setUpdateNoticeState,
  type UpdateNoticeState,
} from "../utils/updateNoticeStore";

const AVAILABLE: UpdateNoticeState = {
  available: true,
  newer: true,
  latestVersion: "0.34.0",
  currentVersion: "0.33.0",
  enabled: true,
  installedProgram: true,
};

describe("UpdateNotice", () => {
  beforeEach(() => {
    resetUpdateNoticeStoreForTests();
    vi.mocked(dismissUpdateNotice).mockReset().mockResolvedValue({ success: true });
  });

  it("shows nothing while no update is available", () => {
    const { container } = render(<UpdateNotice onOpenUpdates={vi.fn()} />);
    expect(container.textContent).toBe("");
  });

  it("shows nothing for a dismissed release the section still names", () => {
    setUpdateNoticeState({ ...AVAILABLE, available: false });
    const { queryByTestId } = render(<UpdateNotice onOpenUpdates={vi.fn()} />);
    expect(queryByTestId("update-notice")).toBeNull();
  });

  it("names the release and the one the reader has", () => {
    setUpdateNoticeState(AVAILABLE);
    const { getByTestId } = render(<UpdateNotice onOpenUpdates={vi.fn()} />);
    expect(getByTestId("update-notice").textContent).toContain("Tender 0.34.0 is available");
    expect(getByTestId("update-notice").textContent).toContain("You have 0.33.0.");
  });

  it("offers no install — the notice jumps to its home instead", () => {
    setUpdateNoticeState(AVAILABLE);
    const onOpenUpdates = vi.fn();
    const { getByText, queryByText } = render(<UpdateNotice onOpenUpdates={onOpenUpdates} />);

    fireEvent.click(getByText("Open Updates"));

    expect(onOpenUpdates).toHaveBeenCalledTimes(1);
    expect(queryByText(/install/i)).toBeNull();
  });

  it("Dismiss waves this version away and takes the card down", async () => {
    setUpdateNoticeState(AVAILABLE);
    const { getByText, queryByTestId } = render(<UpdateNotice onOpenUpdates={vi.fn()} />);

    await act(async () => {
      fireEvent.click(getByText("Dismiss"));
    });

    expect(dismissUpdateNotice).toHaveBeenCalledWith("0.34.0");
    expect(queryByTestId("update-notice")).toBeNull();
  });

  it("a Dismiss that failed to persist is logged and leaves the card up", async () => {
    setUpdateNoticeState(AVAILABLE);
    vi.mocked(dismissUpdateNotice).mockRejectedValue(new Error("socket closed"));
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
    const { getByText, getByTestId } = render(<UpdateNotice onOpenUpdates={vi.fn()} />);

    await act(async () => {
      fireEvent.click(getByText("Dismiss"));
    });

    expect(logError).toHaveBeenCalledWith(expect.stringContaining("Failed to dismiss the update notice"));
    expect(getByTestId("update-notice")).toBeInTheDocument();
    expect(getUpdateNoticeState().available).toBe(true);
    logError.mockRestore();
  });

  it("the card itself is a focus stop, so the panel can scroll to it", () => {
    setUpdateNoticeState(AVAILABLE);
    const { getByTestId } = render(<UpdateNotice onOpenUpdates={vi.fn()} />);
    expect(getByTestId("update-notice").closest("[data-activate]")).not.toBeNull();
  });
});
