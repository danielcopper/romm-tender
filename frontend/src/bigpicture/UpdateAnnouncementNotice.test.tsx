import "@testing-library/jest-dom/vitest";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { UpdateAnnouncementNotice } from "./UpdateAnnouncementNotice";
import * as backend from "../api/backend";
import { dismissUpdateAnnouncement } from "../api/backend";
import {
  getUpdateOutcomeState,
  resetUpdateOutcomeStoreForTests,
  setUpdateOutcomeState,
  type UpdateOutcomeState,
} from "../utils/updateOutcomeStore";

const UPDATED: UpdateOutcomeState = {
  announcement: { version: "1.3.0", direction: "updated" },
  failure: null,
  failureDismissed: false,
};

describe("UpdateAnnouncementNotice", () => {
  beforeEach(() => {
    resetUpdateOutcomeStoreForTests();
    vi.mocked(dismissUpdateAnnouncement).mockReset().mockResolvedValue({ success: true });
  });

  it("shows nothing while no version moved", () => {
    const { container } = render(<UpdateAnnouncementNotice />);
    expect(container.textContent).toBe("");
  });

  it("names the version an update went to", () => {
    setUpdateOutcomeState(UPDATED);
    const { getByTestId } = render(<UpdateAnnouncementNotice />);
    expect(getByTestId("update-announcement-notice").textContent).toBe("Tender was updated to 1.3.0.");
  });

  it("names the earlier release a return went back to", () => {
    setUpdateOutcomeState({ ...UPDATED, announcement: { version: "1.2.3", direction: "back" } });
    const { getByTestId } = render(<UpdateAnnouncementNotice />);
    expect(getByTestId("update-announcement-notice").textContent).toBe("Tender is back on 1.2.3.");
  });

  it("carries Dismiss and no jump, since it has no home", () => {
    setUpdateOutcomeState(UPDATED);
    const { container } = render(<UpdateAnnouncementNotice />);
    expect([...container.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Dismiss"]);
  });

  it("Dismiss tells the backend and takes the card down", async () => {
    setUpdateOutcomeState(UPDATED);
    const { getByText, queryByTestId } = render(<UpdateAnnouncementNotice />);

    fireEvent.click(getByText("Dismiss"));

    await waitFor(() => expect(queryByTestId("update-announcement-notice")).toBeNull());
    expect(dismissUpdateAnnouncement).toHaveBeenCalledTimes(1);
    expect(getUpdateOutcomeState().announcement).toBeNull();
  });

  it("a Dismiss that failed is logged and leaves the card up", async () => {
    setUpdateOutcomeState(UPDATED);
    vi.mocked(dismissUpdateAnnouncement).mockRejectedValue(new Error("socket closed"));
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
    const { getByText, getByTestId } = render(<UpdateAnnouncementNotice />);

    fireEvent.click(getByText("Dismiss"));

    await waitFor(() =>
      expect(logError).toHaveBeenCalledWith("Failed to dismiss the update announcement: Error: socket closed"),
    );
    expect(getByTestId("update-announcement-notice")).toBeInTheDocument();
    expect(getUpdateOutcomeState().announcement).toEqual({ version: "1.3.0", direction: "updated" });
    logError.mockRestore();
  });

  it("the card itself is a focus stop, so the panel can scroll to it", () => {
    setUpdateOutcomeState(UPDATED);
    const { getByTestId } = render(<UpdateAnnouncementNotice />);
    expect(getByTestId("update-announcement-notice").closest("[data-activate]")).not.toBeNull();
  });
});
