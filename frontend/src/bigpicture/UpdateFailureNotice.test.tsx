import "@testing-library/jest-dom/vitest";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { UpdateFailureNotice } from "./UpdateFailureNotice";
import * as backend from "../api/backend";
import { dismissUpdateFailure } from "../api/backend";
import {
  UPDATE_CHECK_FAILURE_REASON,
  UPDATE_FAILURE_REASON,
  UPDATE_UNKNOWN_FAILURE_REASON,
  getUpdateOutcomeState,
  resetUpdateOutcomeStoreForTests,
  setUpdateOutcomeState,
  type UpdateOutcomeState,
} from "../utils/updateOutcomeStore";

const ROLLED_BACK: UpdateOutcomeState = {
  announcement: null,
  failure: {
    attemptedVersion: "1.3.0",
    restoredVersion: "1.2.3",
    rolledBackAt: "2026-09-25T10:15:00Z",
    kind: "rollback",
  },
  failureDismissed: false,
};

describe("UpdateFailureNotice", () => {
  beforeEach(() => {
    resetUpdateOutcomeStoreForTests();
    vi.mocked(dismissUpdateFailure).mockReset().mockResolvedValue({ success: true });
  });

  it("shows nothing while no update was rolled back", () => {
    const { container } = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />);
    expect(container.textContent).toBe("");
  });

  it("shows nothing for a record the reader dismissed", () => {
    setUpdateOutcomeState({ ...ROLLED_BACK, failureDismissed: true });
    const { queryByTestId } = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />);
    expect(queryByTestId("update-failure-notice")).toBeNull();
  });

  it("names both versions and says where the reason is", () => {
    setUpdateOutcomeState(ROLLED_BACK);
    const { getByTestId } = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />);
    const text = getByTestId("update-failure-notice").textContent;
    expect(text).toContain("Update to 1.3.0 failed — you are still on 1.2.3.");
    expect(text).toContain(UPDATE_FAILURE_REASON);
  });

  it("says nothing was changed for an update the pre-install check refused, in place of the log line", () => {
    setUpdateOutcomeState({ ...ROLLED_BACK, failure: { ...ROLLED_BACK.failure!, kind: "check" } });
    const { getByTestId } = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />);
    const text = getByTestId("update-failure-notice").textContent;
    expect(text).toContain("Update to 1.3.0 failed — you are still on 1.2.3.");
    expect(text).toContain(UPDATE_CHECK_FAILURE_REASON);
    expect(text).not.toContain(UPDATE_FAILURE_REASON);
  });

  it("names no cause for a record of a kind this version does not know, only where the installer's output is", () => {
    setUpdateOutcomeState({ ...ROLLED_BACK, failure: { ...ROLLED_BACK.failure!, kind: "unknown" } });
    const { getByTestId } = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />);
    const text = getByTestId("update-failure-notice").textContent;
    expect(text).toContain("Update to 1.3.0 failed — you are still on 1.2.3.");
    expect(text).toContain(UPDATE_UNKNOWN_FAILURE_REASON);
    expect(text).not.toContain(UPDATE_FAILURE_REASON);
    expect(text).not.toContain(UPDATE_CHECK_FAILURE_REASON);
  });

  it("names the backend log as where the reason is, and the journal for a version that failed before it", () => {
    expect(UPDATE_FAILURE_REASON).toBe(
      "Tender's log, backend.log, says why — or the journal (journalctl --user -u romm-tender), if the new version failed before it could write to the log.",
    );
  });

  it("puts Open Updates and Dismiss side by side, Open Updates first, and jumps to its home", () => {
    setUpdateOutcomeState(ROLLED_BACK);
    const onOpenUpdates = vi.fn();
    const { container, getByText } = render(<UpdateFailureNotice onOpenUpdates={onOpenUpdates} />);

    const buttons = [...container.querySelectorAll("button")];
    expect(buttons.map((b) => b.textContent)).toEqual(["Open Updates", "Dismiss"]);
    expect(buttons[1]!.parentElement).toBe(buttons[0]!.parentElement);
    fireEvent.click(getByText("Open Updates"));
    expect(onOpenUpdates).toHaveBeenCalledTimes(1);
  });

  it("Dismiss waves this record away and takes the card down", async () => {
    setUpdateOutcomeState(ROLLED_BACK);
    const { getByText, queryByTestId } = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />);

    fireEvent.click(getByText("Dismiss"));

    await waitFor(() => expect(queryByTestId("update-failure-notice")).toBeNull());
    expect(dismissUpdateFailure).toHaveBeenCalledWith("2026-09-25T10:15:00Z");
  });

  it("a Dismiss that failed to persist is logged and leaves the card up", async () => {
    setUpdateOutcomeState(ROLLED_BACK);
    vi.mocked(dismissUpdateFailure).mockRejectedValue(new Error("socket closed"));
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
    const { getByText, getByTestId } = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />);

    fireEvent.click(getByText("Dismiss"));

    await waitFor(() =>
      expect(logError).toHaveBeenCalledWith(expect.stringContaining("Failed to dismiss the rolled-back update notice")),
    );
    expect(getByTestId("update-failure-notice")).toBeInTheDocument();
    expect(getUpdateOutcomeState().failureDismissed).toBe(false);
    logError.mockRestore();
  });

  it("the card itself is a focus stop, so the panel can scroll to it", () => {
    setUpdateOutcomeState(ROLLED_BACK);
    const { getByTestId } = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />);
    expect(getByTestId("update-failure-notice").closest("[data-activate]")).not.toBeNull();
  });
});
