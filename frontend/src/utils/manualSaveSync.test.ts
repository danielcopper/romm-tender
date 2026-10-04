import { describe, it, expect, vi, beforeEach } from "vitest";
import { executeManualSaveSync } from "./manualSaveSync";
import * as backend from "../api/backend";
import * as toast from "./toast";
import * as gameDetailStore from "./gameDetailStore";

vi.mock("../api/backend", () => ({
  syncRomSaves: vi.fn(),
}));

vi.mock("./toast", () => ({
  showToast: vi.fn(),
}));

vi.mock("./gameDetailStore", () => ({
  noteSaveSyncDisplay: vi.fn(),
}));

describe("executeManualSaveSync", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("handles successful sync with uploaded and downloaded counts", async () => {
    vi.mocked(backend.syncRomSaves).mockResolvedValueOnce({
      success: true,
      message: "Synced",
      synced: 2,
      uploaded: 1,
      downloaded: 1,
      conflicts: [],
    });
    const dispatchSpy = vi.spyOn(globalThis, "dispatchEvent");

    const result = await executeManualSaveSync(100, 42);

    expect(result).toBe(true);
    expect(toast.showToast).toHaveBeenCalledWith("Saves synced with RomM (1 up, 1 down)");
    expect(dispatchSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "romm_data_changed",
        detail: { type: "save_sync", rom_id: 42 },
      }),
    );
    expect(gameDetailStore.noteSaveSyncDisplay).toHaveBeenCalledWith(100, 42, {
      status: "synced",
      label: "Just now",
      last_sync_check_at: null,
    });
  });

  it("shows 'Saves already up to date' when no files moved and no conflicts exist", async () => {
    vi.mocked(backend.syncRomSaves).mockResolvedValueOnce({
      success: true,
      message: "Up to date",
      synced: 0,
      uploaded: 0,
      downloaded: 0,
      conflicts: [],
    });

    const result = await executeManualSaveSync(100, 42);

    expect(result).toBe(true);
    expect(toast.showToast).toHaveBeenCalledWith("Saves already up to date");
  });

  it("alerts on conflicts and avoids 'already up to date'", async () => {
    vi.mocked(backend.syncRomSaves).mockResolvedValueOnce({
      success: true,
      message: "Conflicts",
      synced: 0,
      uploaded: 0,
      downloaded: 0,
      conflicts: [{ filename: "save1.srm" } as never],
    });

    const result = await executeManualSaveSync(100, 42);

    expect(result).toBe(true);
    expect(toast.showToast).not.toHaveBeenCalledWith("Saves already up to date");
    expect(toast.showToast).toHaveBeenCalledWith("1 conflict(s) need resolution");
  });

  it("handles sync failure response with backend message", async () => {
    vi.mocked(backend.syncRomSaves).mockResolvedValueOnce({
      success: false,
      message: "Server returned 500",
      synced: 0,
    });

    const result = await executeManualSaveSync(100, 42);

    expect(result).toBe(false);
    expect(toast.showToast).toHaveBeenCalledWith("Server returned 500");
  });

  it("handles exception thrown during sync", async () => {
    vi.mocked(backend.syncRomSaves).mockRejectedValueOnce(new Error("Network boom"));

    const result = await executeManualSaveSync(100, 42);

    expect(result).toBe(false);
    expect(toast.showToast).toHaveBeenCalledWith("Save sync failed");
  });
});
