import { describe, it, expect, vi, beforeEach } from "vitest";
import { executeDownloadResume } from "./downloadResume";
import * as backend from "../api/backend";
import * as toast from "./toast";
import { RESUME_TARGET_OCCUPIED_TOAST } from "./adoptWording";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  resumeDownload: vi.fn(),
  logWarn: vi.fn(),
  logError: vi.fn(),
}));

vi.mock("./toast", () => ({
  showToast: vi.fn(),
}));

describe("executeDownloadResume", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("asks the backend to resume the ROM's download and says nothing when it does", async () => {
    vi.mocked(backend.resumeDownload).mockResolvedValueOnce({ success: true } as never);

    await executeDownloadResume(7, "Tag");

    expect(backend.resumeDownload).toHaveBeenCalledWith(7);
    expect(toast.showToast).not.toHaveBeenCalled();
    expect(backend.logWarn).not.toHaveBeenCalled();
    expect(backend.logError).not.toHaveBeenCalled();
  });

  it("names the files in the way when the game's location is taken", async () => {
    vi.mocked(backend.resumeDownload).mockResolvedValueOnce({
      success: false,
      reason: "target_occupied",
      message: "occupied",
    } as never);

    await executeDownloadResume(7, "Tag");

    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith(RESUME_TARGET_OCCUPIED_TOAST);
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "Tag: the backend refused to resume the download of romId=7: target_occupied: occupied",
    );
  });

  it("says the backend's own sentence for any other refusal", async () => {
    vi.mocked(backend.resumeDownload).mockResolvedValueOnce({
      success: false,
      reason: "not_found",
      message: "No paused download",
    } as never);

    await executeDownloadResume(7, "Tag");

    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("No paused download");
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "Tag: the backend refused to resume the download of romId=7: not_found: No paused download",
    );
  });

  it("says it could not resume when a refusal carries no sentence", async () => {
    vi.mocked(backend.resumeDownload).mockResolvedValueOnce({ success: false, reason: "not_found" } as never);

    await executeDownloadResume(7, "Tag");

    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("Couldn't resume the download");
  });

  it("asks whether the server runs when the call itself fails", async () => {
    vi.mocked(backend.resumeDownload).mockRejectedValueOnce(new Error("socket closed"));

    await executeDownloadResume(7, "Tag");

    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("Couldn't resume the download — is RomM server running?");
    expect(backend.logError).toHaveBeenCalledExactlyOnceWith(
      "Tag: could not resume the download of romId=7: Error: socket closed",
    );
  });
});
