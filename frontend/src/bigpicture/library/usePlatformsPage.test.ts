// The Platforms page's state, asked for a folder the listing does not hold.
// No row of the page can be that folder — the rows and the listing come from one
// answer — so `PlatformsTab.test.tsx`, which drives the page, cannot reach it;
// the hook is driven alone here for that one case.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import * as backend from "../../api/backend";
import { usePlatformsPage } from "./usePlatformsPage";

describe("usePlatformsPage › a folder the listing does not hold", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(backend.getPlatforms).mockResolvedValue({
      success: true,
      platforms: [{ id: 1, name: "Game Boy Advance", slug: "gba", fs_slug: "gba", rom_count: 12, sync_enabled: true }],
    });
    vi.mocked(backend.getFirmwareStatus).mockResolvedValue({ success: true, server_offline: false });
    vi.mocked(backend.getPlatformFirmwareStatus).mockReturnValue(new Promise(() => {}));
    vi.mocked(backend.getRegistryPlatforms).mockResolvedValue({ platforms: [] });
    vi.mocked(backend.getSystemCoreInfo).mockReturnValue(new Promise(() => {}));
    vi.mocked(backend.countPlatformSaves).mockResolvedValue({ count: 0 });
    vi.mocked(backend.getCollections).mockResolvedValue({ success: true, collections: [] });
    vi.mocked(backend.debugLog).mockResolvedValue(undefined);
  });

  it("is logged and asked nothing about, rather than thrown out of the selection", async () => {
    const { result } = renderHook(() => usePlatformsPage());
    await waitFor(() => expect(result.current.selectedFolder).toBe("gba"));
    vi.mocked(backend.getSystemCoreInfo).mockClear();
    const logError = vi.spyOn(backend, "logError");

    await act(async () => result.current.select("ghost"));

    expect(logError).toHaveBeenCalledWith(expect.stringContaining("ghost"));
    expect(vi.mocked(backend.getSystemCoreInfo)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.countPlatformSaves)).not.toHaveBeenCalledWith("ghost");
    expect(result.current.selectedFolder).toBe("ghost");
    // Failed on the spot rather than left "not read yet", which the pane shows as a read still under way.
    expect(result.current.coreFor("ghost")).toBeNull();
    expect(result.current.saveCountFor("ghost")).toBeNull();
  });
});
