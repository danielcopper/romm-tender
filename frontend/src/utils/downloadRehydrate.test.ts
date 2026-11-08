import { describe, it, expect, vi, beforeEach } from "vitest";
import { isDownloadUnderWay, seedDownloadFromQueue } from "./downloadRehydrate";
import * as backend from "../api/backend";
import type { DownloadItem } from "../types";

vi.mock("../api/backend", () => ({
  getDownloadQueue: vi.fn(),
  logWarn: vi.fn(),
}));

const entry = (overrides: Partial<DownloadItem> = {}): DownloadItem => ({
  rom_id: 100,
  rom_name: "Super Mario World",
  platform_name: "snes",
  file_name: "smw.sfc",
  status: "paused",
  progress: 0.5,
  bytes_downloaded: 1024,
  total_bytes: 2048,
  resumable: true,
  ...overrides,
});

function storeHolding(items: DownloadItem[]) {
  const held = items.slice();
  return {
    getDownloadState: () => held,
    updateDownload: vi.fn((item: DownloadItem) => {
      held.push(item);
    }),
  };
}

describe("isDownloadUnderWay", () => {
  it.each(["queued", "downloading", "paused", "extracting"] as const)("is true for a %s download", (status) => {
    expect(isDownloadUnderWay(entry({ status }))).toBe(true);
  });

  it.each(["completed", "failed", "cancelled"] as const)("is false for a %s one", (status) => {
    expect(isDownloadUnderWay(entry({ status }))).toBe(false);
  });

  it("is false for no download at all", () => {
    expect(isDownloadUnderWay(undefined)).toBe(false);
  });
});

describe("seedDownloadFromQueue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each(["queued", "downloading", "paused", "extracting"] as const)(
    "puts the ROM's %s download from the queue into the store",
    async (status) => {
      const queued = entry({ status });
      vi.mocked(backend.getDownloadQueue).mockResolvedValueOnce({ downloads: [entry({ rom_id: 7 }), queued] });
      const store = storeHolding([]);

      await seedDownloadFromQueue(100, "Tag", store);

      expect(store.updateDownload).toHaveBeenCalledExactlyOnceWith(queued);
    },
  );

  it.each(["completed", "failed", "cancelled"] as const)("leaves a %s download out", async (status) => {
    vi.mocked(backend.getDownloadQueue).mockResolvedValueOnce({ downloads: [entry({ status })] });
    const store = storeHolding([]);

    await seedDownloadFromQueue(100, "Tag", store);

    expect(store.updateDownload).not.toHaveBeenCalled();
  });

  it("leaves another ROM's download out", async () => {
    vi.mocked(backend.getDownloadQueue).mockResolvedValueOnce({ downloads: [entry({ rom_id: 7 })] });
    const store = storeHolding([]);

    await seedDownloadFromQueue(100, "Tag", store);

    expect(store.updateDownload).not.toHaveBeenCalled();
  });

  it("leaves an entry the store already holds as it is, since the queue's answer may be older than its last frame", async () => {
    vi.mocked(backend.getDownloadQueue).mockResolvedValueOnce({ downloads: [entry()] });
    const store = storeHolding([entry({ status: "downloading" })]);

    await seedDownloadFromQueue(100, "Tag", store);

    expect(store.updateDownload).not.toHaveBeenCalled();
  });

  it("logs a read that fails as a warning, and throws nothing", async () => {
    vi.mocked(backend.getDownloadQueue).mockRejectedValueOnce(new Error("socket closed"));
    const store = storeHolding([]);

    await expect(seedDownloadFromQueue(100, "Tag", store)).resolves.toBeUndefined();

    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "Tag: could not read the download queue for romId=100: Error: socket closed",
    );
    expect(store.updateDownload).not.toHaveBeenCalled();
  });
});
