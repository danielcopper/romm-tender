import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { executeBiosReplace } from "./biosReplace";
import { forgetBiosPresses, noteBiosPress, noteReplaceFailed, noteReplaced, useBiosPresses } from "./biosAttempts";
import * as backend from "../api/backend";
import * as toast from "./toast";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  replaceBiosFile: vi.fn(),
}));

vi.mock("./toast", () => ({
  showToast: vi.fn(),
}));

/** Record the platform slugs every bios "romm_data_changed" announces until the returned stop is called. */
function listenForBiosChanges(): { slugs: string[]; stop: () => void } {
  const slugs: string[] = [];
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<{ type: string; platform_slug?: string }>).detail;
    if (detail.type === "bios" && detail.platform_slug) slugs.push(detail.platform_slug);
  };
  globalThis.addEventListener("romm_data_changed", listener);
  return { slugs, stop: () => globalThis.removeEventListener("romm_data_changed", listener) };
}

const attemptAt = (place: string | null, fileName: string | null = null) => {
  const { result, unmount } = renderHook(() => useBiosPresses());
  const attempt = result.current(place, fileName);
  unmount();
  return attempt;
};

describe("executeBiosReplace", () => {
  beforeEach(() => {
    vi.mocked(backend.replaceBiosFile).mockReset();
    vi.mocked(toast.showToast).mockClear();
  });
  afterEach(() => forgetBiosPresses());

  it("says on the rows where the old file went and which links it could not place, and re-reads the platform", async () => {
    vi.mocked(backend.replaceBiosFile).mockResolvedValue({
      success: true,
      placed: 1,
      left_in_place: 0,
      removed: 0,
      link_failures: [{ place: "/emudeck/bios/saturn_bios.bin", file_name: "saturn_bios.bin", reason: "denied" }],
      backup_path: "/data/backups/bios/2026-10-22 10-00-00/bios/saturn_bios.bin",
    });
    const changes = listenForBiosChanges();

    const done = await executeBiosReplace("saturn", "/bios/saturn_bios.bin");
    changes.stop();

    expect(done).toBe(true);
    expect(vi.mocked(backend.replaceBiosFile)).toHaveBeenCalledWith("saturn", "/bios/saturn_bios.bin");
    expect(attemptAt("/bios/saturn_bios.bin").replacedTo).toBe(
      "/data/backups/bios/2026-10-22 10-00-00/bios/saturn_bios.bin",
    );
    expect(attemptAt("/emudeck/bios/saturn_bios.bin").linkFailure).toBe("denied");
    expect(changes.slugs).toEqual(["saturn"]);
    expect(toast.showToast).not.toHaveBeenCalled();
  });

  it("says a refusal on its row, never in a toast, and re-reads nothing", async () => {
    vi.mocked(backend.replaceBiosFile).mockResolvedValue({
      success: false,
      reason: "replace_failed",
      message: "Could not put Tender's copy in place; the old file is back.",
    });
    const changes = listenForBiosChanges();

    const done = await executeBiosReplace("saturn", "/bios/saturn_bios.bin");
    changes.stop();

    expect(done).toBe(false);
    expect(toast.showToast).not.toHaveBeenCalled();
    expect(attemptAt("/bios/saturn_bios.bin")).toEqual({
      replaceFailure: "Could not put Tender's copy in place; the old file is back.",
    });
    expect(changes.slugs).toEqual([]);
  });

  it("says a failed call on its row, never in a toast", async () => {
    vi.mocked(backend.replaceBiosFile).mockRejectedValue(new Error("connection lost"));

    const done = await executeBiosReplace("saturn", "/bios/saturn_bios.bin");

    expect(done).toBe(false);
    expect(toast.showToast).not.toHaveBeenCalled();
    expect(attemptAt("/bios/saturn_bios.bin").replaceFailure).toBe("Error: connection lost");
  });

  it("drops a failure an earlier press left on the row once the replacement succeeds", async () => {
    noteReplaceFailed("/bios/saturn_bios.bin", "denied");
    vi.mocked(backend.replaceBiosFile).mockResolvedValue({
      success: true,
      placed: 1,
      left_in_place: 0,
      removed: 0,
      link_failures: [],
      backup_path: "/data/backups/bios/z/saturn_bios.bin",
    });

    await executeBiosReplace("saturn", "/bios/saturn_bios.bin");

    expect(attemptAt("/bios/saturn_bios.bin")).toEqual({ replacedTo: "/data/backups/bios/z/saturn_bios.bin" });
  });
});

describe("noteBiosPress", () => {
  afterEach(() => forgetBiosPresses());

  it("keeps what a Replace… said on a row until the page is left", () => {
    noteReplaced("/bios/saturn_bios.bin", "/data/backups/bios/y/saturn_bios.bin", []);
    noteReplaceFailed("/bios/mpr.bin", "denied");

    noteBiosPress({
      link_failures: [{ place: "/bios/saturn_bios.bin", file_name: "saturn_bios.bin", reason: "busy" }],
    });

    expect(attemptAt("/bios/saturn_bios.bin")).toEqual({
      replacedTo: "/data/backups/bios/y/saturn_bios.bin",
      linkFailure: "busy",
    });
    expect(attemptAt("/bios/mpr.bin")).toEqual({ replaceFailure: "denied" });
  });
});

describe("noteReplaced", () => {
  afterEach(() => forgetBiosPresses());

  it("replaces the links an earlier press could not place, and keeps what else it said", () => {
    noteBiosPress({
      link_failures: [{ place: "/bios/mpr.bin", file_name: "mpr.bin", reason: "denied" }],
      backups: ["/data/backups/bios/x/cart.bin"],
    });

    noteReplaced("/bios/saturn_bios.bin", "/data/backups/bios/y/saturn_bios.bin", []);

    expect(attemptAt("/bios/mpr.bin").linkFailure).toBeUndefined();
    expect(attemptAt(null, "cart.bin").movedAside).toBe("/data/backups/bios/x/cart.bin");
    expect(attemptAt("/bios/saturn_bios.bin").replacedTo).toBe("/data/backups/bios/y/saturn_bios.bin");
  });
});
