import { describe, it, expect, vi, beforeEach } from "vitest";
import { executeDownloadCancel, executeDownloadPause } from "./downloadPauseCancel";
import * as backend from "../api/backend";
import { getDownloadState, setDownloads, updateDownload } from "./downloadStore";
import type { DownloadItem } from "../types";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  cancelDownload: vi.fn(),
  pauseDownload: vi.fn(),
  logWarn: vi.fn(),
  logError: vi.fn(),
}));

const entry = (romId: number) => ({ rom_id: romId, status: "downloading" }) as DownloadItem;

describe("executeDownloadPause", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("asks the backend to pause the ROM's download and logs nothing when it does", async () => {
    vi.mocked(backend.pauseDownload).mockResolvedValueOnce({ success: true, message: "" });

    await executeDownloadPause(7, "Tag");

    expect(backend.pauseDownload).toHaveBeenCalledExactlyOnceWith(7);
    expect(backend.logWarn).not.toHaveBeenCalled();
    expect(backend.logError).not.toHaveBeenCalled();
  });

  it("logs a refusal as a warning with the ROM and the backend's reason", async () => {
    vi.mocked(backend.pauseDownload).mockResolvedValueOnce({
      success: false,
      reason: "no_active_download",
      message: "No active download",
    });

    await executeDownloadPause(7, "Tag");

    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "Tag: the backend refused to pause the download of romId=7: no_active_download: No active download",
    );
  });

  it("logs a call that got no answer as an error", async () => {
    vi.mocked(backend.pauseDownload).mockRejectedValueOnce(new Error("socket closed"));

    await executeDownloadPause(7, "Tag");

    expect(backend.logError).toHaveBeenCalledExactlyOnceWith(
      "Tag: could not pause the download of romId=7: Error: socket closed",
    );
  });
});

describe("executeDownloadCancel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setDownloads([entry(7), entry(8)]);
  });

  it("asks the backend to cancel the ROM's download and leaves the store and the bar to its frame", async () => {
    vi.mocked(backend.cancelDownload).mockResolvedValueOnce({ success: true, message: "" });
    const onNothingToCancel = vi.fn();

    await executeDownloadCancel(7, "Tag", onNothingToCancel);

    expect(backend.cancelDownload).toHaveBeenCalledExactlyOnceWith(7);
    expect(onNothingToCancel).not.toHaveBeenCalled();
    expect(getDownloadState().map((d) => d.rom_id)).toEqual([7, 8]);
    expect(backend.logWarn).not.toHaveBeenCalled();
    expect(backend.logError).not.toHaveBeenCalled();
  });

  it("drops the ROM from the store and ends the bar when there is no download to cancel", async () => {
    vi.mocked(backend.cancelDownload).mockResolvedValueOnce({
      success: false,
      reason: "no_active_download",
      message: "No active download",
    });
    const onNothingToCancel = vi.fn();

    await executeDownloadCancel(7, "Tag", onNothingToCancel);

    expect(onNothingToCancel).toHaveBeenCalledOnce();
    expect(getDownloadState().map((d) => d.rom_id)).toEqual([8]);
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "Tag: the backend refused to cancel the download of romId=7: no_active_download: No active download",
    );
  });

  /** A cancel whose answer is held until `answer` is called, with a store write landing before it. */
  function cancelAnsweredAfter(write: () => void) {
    let answer!: (value: backend.BackendResult) => void;
    vi.mocked(backend.cancelDownload).mockReturnValueOnce(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    const onNothingToCancel = vi.fn();
    const sent = executeDownloadCancel(7, "Tag", onNothingToCancel);
    write();
    answer({ success: false, reason: "no_active_download", message: "No active download" });
    return { sent, onNothingToCancel };
  }

  it.each(["completed", "failed", "cancelled"] as const)(
    "leaves a download that ended %s while the cancel was out, and the bar, alone",
    async (status) => {
      const ended = { rom_id: 7, status } as DownloadItem;
      const { sent, onNothingToCancel } = cancelAnsweredAfter(() => updateDownload(ended));

      await sent;

      expect(onNothingToCancel).not.toHaveBeenCalled();
      expect(getDownloadState()[0]).toBe(ended);
      expect(getDownloadState()).toHaveLength(2);
    },
  );

  it.each(["queued", "paused", "extracting"] as const)(
    "drops a download that moved to %s while the cancel was out, and ends the bar",
    async (status) => {
      const { sent, onNothingToCancel } = cancelAnsweredAfter(() =>
        updateDownload({ rom_id: 7, status } as DownloadItem),
      );

      await sent;

      expect(onNothingToCancel).toHaveBeenCalledOnce();
      expect(getDownloadState().map((d) => d.rom_id)).toEqual([8]);
    },
  );

  it("drops an earlier attempt's ended entry and ends the new press's bar", async () => {
    setDownloads([{ rom_id: 7, status: "failed" } as DownloadItem, entry(8)]);
    const { sent, onNothingToCancel } = cancelAnsweredAfter(() => undefined);

    await sent;

    expect(onNothingToCancel).toHaveBeenCalledOnce();
    expect(getDownloadState().map((d) => d.rom_id)).toEqual([8]);
  });

  it("logs a caller's bar that throws on ending rather than dropping it", async () => {
    vi.mocked(backend.cancelDownload).mockResolvedValueOnce({
      success: false,
      reason: "no_active_download",
      message: "No active download",
    });

    await executeDownloadCancel(7, "Tag", () => {
      throw new Error("bar broke");
    });

    expect(backend.logError).toHaveBeenCalledExactlyOnceWith(
      "Tag: could not cancel the download of romId=7: Error: bar broke",
    );
  });

  it("keeps the store and the bar on a refusal for another reason", async () => {
    vi.mocked(backend.cancelDownload).mockResolvedValueOnce({
      success: false,
      reason: "server_unreachable",
      message: "RomM cannot be reached",
    });
    const onNothingToCancel = vi.fn();

    await executeDownloadCancel(7, "Tag", onNothingToCancel);

    expect(onNothingToCancel).not.toHaveBeenCalled();
    expect(getDownloadState().map((d) => d.rom_id)).toEqual([7, 8]);
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "Tag: the backend refused to cancel the download of romId=7: server_unreachable: RomM cannot be reached",
    );
  });

  it("keeps the store and the bar, and logs an error, when the call got no answer", async () => {
    vi.mocked(backend.cancelDownload).mockRejectedValueOnce(new Error("socket closed"));
    const onNothingToCancel = vi.fn();

    await executeDownloadCancel(7, "Tag", onNothingToCancel);

    expect(onNothingToCancel).not.toHaveBeenCalled();
    expect(getDownloadState().map((d) => d.rom_id)).toEqual([7, 8]);
    expect(backend.logError).toHaveBeenCalledExactlyOnceWith(
      "Tag: could not cancel the download of romId=7: Error: socket closed",
    );
  });
});
