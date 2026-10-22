import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import { applyArtwork } from "./artwork";
import { artworkThumbToken } from "./artworkSlotsStore";
import { releasePruneLeasesByOwner } from "./pruneLease";
import { applyShortcutArtwork } from "./shortcutArtwork";

describe("applyShortcutArtwork", () => {
  const setShortcutIcon = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: { SetShortcutIcon: setShortcutIcon, SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined) },
    });
  });

  const batch = {
    icons: [
      { app_id: 100, icon_path: "/grid/100_icon.png" },
      { app_id: 200, icon_path: "/grid/tender-icon.png" },
    ],
    artwork: [],
    prune_lease_token: "icon-lease",
  };

  it("points each shortcut in the batch at its icon file", async () => {
    await expect(applyShortcutArtwork(batch)).resolves.toBe(2);

    expect(setShortcutIcon.mock.calls).toEqual([
      [100, "/grid/100_icon.png"],
      [200, "/grid/tender-icon.png"],
    ]);
  });

  it("gives the batch's lease back after the last icon is set", async () => {
    await applyShortcutArtwork(batch);

    const release = vi.mocked(backend.releasePruneConflictLease);
    expect(release).toHaveBeenCalledWith("icon-lease");
    expect(release.mock.invocationCallOrder[0]).toBeGreaterThan(setShortcutIcon.mock.invocationCallOrder[1]!);
  });

  it("leaves a shortcut whose artwork the game page is applying right now", async () => {
    let finish: (value: { base64: null; no_api_key: boolean }) => void = () => {};
    vi.mocked(backend.getSgdbArtworkBase64).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "none" });
    const applying = applyArtwork(42, 200);

    await expect(applyShortcutArtwork(batch)).resolves.toBe(1);

    expect(setShortcutIcon.mock.calls).toEqual([[100, "/grid/100_icon.png"]]);
    finish({ base64: null, no_api_key: false });
    await applying;
  });

  it("goes on past an icon Steam refuses, and logs it", async () => {
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => {});
    setShortcutIcon.mockImplementationOnce(() => {
      throw new Error("no such app");
    });

    await expect(applyShortcutArtwork(batch)).resolves.toBe(1);

    expect(setShortcutIcon).toHaveBeenCalledWith(200, "/grid/tender-icon.png");
    expect(logError).toHaveBeenCalledWith("Shortcut artwork: could not set the icon of appId 100: Error: no such app");
    logError.mockRestore();
  });

  it("sets no more icons once the batch's lease is cancelled", async () => {
    setShortcutIcon.mockImplementationOnce(() => {
      void releasePruneLeasesByOwner("Shortcut artwork");
    });

    await expect(applyShortcutArtwork(batch)).resolves.toBe(1);

    expect(setShortcutIcon.mock.calls).toEqual([[100, "/grid/100_icon.png"]]);
  });
});

describe("applyShortcutArtwork — wide grids and logos", () => {
  const setCustomArtwork = vi.fn();
  const image = (base64: string): backend.SgdbSlotArtwork => ({
    status: "image",
    base64,
    image_id: 12,
    sha256: `sha-${base64}`,
    sgdb_id: 9999,
  });

  beforeEach(() => {
    vi.resetAllMocks();
    setCustomArtwork.mockResolvedValue(undefined);
    vi.stubGlobal("SteamClient", {
      Apps: { SetShortcutIcon: vi.fn(), SetCustomArtworkForApp: setCustomArtwork },
    });
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
    vi.mocked(backend.getSgdbSlotArtwork).mockImplementation(async (_romId, appId, slot) => image(`${slot}-${appId}`));
  });

  const batch = {
    icons: [{ app_id: 100, icon_path: "/grid/100_icon.png" }],
    artwork: [
      { app_id: 100, rom_id: 1, slot: "grid" as const },
      { app_id: 200, rom_id: 2, slot: "logo" as const },
    ],
    prune_lease_token: "batch-lease",
  };

  it("sets each wide grid and logo the batch names after its icons, and counts them", async () => {
    await expect(applyShortcutArtwork(batch)).resolves.toBe(3);

    expect(vi.mocked(backend.getSgdbSlotArtwork).mock.calls).toEqual([
      [1, 100, "grid"],
      [2, 200, "logo"],
    ]);
    expect(setCustomArtwork.mock.calls).toEqual([
      [100, "grid-100", "png", 3],
      [200, "logo-200", "png", 2],
    ]);
    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([
      [1, "grid", "set", 9999, 12, "sha-grid-100"],
      [2, "logo", "set", 9999, 12, "sha-logo-200"],
    ]);
  });

  it("fetches the next image only once the one before it is set", async () => {
    let finish: () => void = () => {};
    setCustomArtwork.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );

    const applying = applyShortcutArtwork(batch);
    await vi.waitFor(() => expect(setCustomArtwork).toHaveBeenCalledTimes(1));
    expect(backend.getSgdbSlotArtwork).toHaveBeenCalledTimes(1);

    finish();
    await applying;
    expect(backend.getSgdbSlotArtwork).toHaveBeenCalledTimes(2);
  });

  it("gives the batch's lease back after the last image is set", async () => {
    await applyShortcutArtwork(batch);

    const release = vi.mocked(backend.releasePruneConflictLease);
    expect(release).toHaveBeenCalledWith("batch-lease");
    expect(Math.max(...release.mock.invocationCallOrder)).toBeGreaterThan(
      setCustomArtwork.mock.invocationCallOrder[1]!,
    );
  });

  it("leaves a shortcut whose artwork the game page is applying right now", async () => {
    let finish: (value: { base64: null; no_api_key: boolean }) => void = () => {};
    vi.mocked(backend.getSgdbArtworkBase64).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const applying = applyArtwork(42, 200);

    await applyShortcutArtwork(batch);

    // The game page's own replacing icon fetch is left out: only the job's fetches are asserted.
    const jobFetches = vi.mocked(backend.getSgdbSlotArtwork).mock.calls.filter(([, , , replace]) => !replace);
    expect(jobFetches).toEqual([[1, 100, "grid"]]);
    finish({ base64: null, no_api_key: false });
    await applying;
  });

  it("sets no more images once the batch's lease is cancelled", async () => {
    setCustomArtwork.mockImplementationOnce(async () => {
      void releasePruneLeasesByOwner("Shortcut artwork");
    });

    await expect(applyShortcutArtwork(batch)).resolves.toBe(2);

    expect(setCustomArtwork.mock.calls).toEqual([[100, "grid-100", "png", 3]]);
    expect(backend.getSgdbSlotArtwork).toHaveBeenCalledTimes(1);
  });
});

describe("applyShortcutArtwork — what Steam made of a SteamGridDB icon", () => {
  const setShortcutIcon = vi.fn();
  const record = { rom_id: 1, sgdb_id: 9999, image_id: 31, sha256: "sha-31" };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: { SetShortcutIcon: setShortcutIcon, SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined) },
    });
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
  });

  const batch = {
    icons: [
      { app_id: 100, icon_path: "/grid/100_icon.png", record },
      { app_id: 200, icon_path: "/grid/tender-icon.png" },
    ],
    artwork: [],
    prune_lease_token: "icon-lease",
  };

  it("reports a set icon with its image id and hash, and nothing for the logo icon", async () => {
    await expect(applyShortcutArtwork(batch)).resolves.toBe(2);

    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([[1, "icon", "set", 9999, 31, "sha-31"]]);
  });

  it("moves the thumbnail token of each icon it sets, and of one Steam refuses", async () => {
    vi.spyOn(backend, "logError").mockImplementation(() => {});
    setShortcutIcon.mockImplementationOnce(() => {
      throw new Error("no such app");
    });
    const tokens = () => [
      artworkThumbToken(100, "icon"),
      artworkThumbToken(200, "icon"),
      artworkThumbToken(100, "hero"),
    ];
    const [first, second, hero] = tokens();

    await applyShortcutArtwork(batch);

    const [firstAfter, secondAfter, heroAfter] = tokens();
    expect(firstAfter).not.toBe(first);
    expect(secondAfter).not.toBe(second);
    expect(heroAfter).toBe(hero);
  });

  it("reports an icon SetShortcutIcon throws on as refused", async () => {
    vi.spyOn(backend, "logError").mockImplementation(() => {});
    setShortcutIcon.mockImplementationOnce(() => {
      throw new Error("no such app");
    });

    await expect(applyShortcutArtwork(batch)).resolves.toBe(1);

    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([[1, "icon", "refused", 9999, 31, "sha-31"]]);
  });

  it("reports nothing for an icon left to the game page's apply", async () => {
    let finish: (value: { base64: null; no_api_key: boolean }) => void = () => {};
    vi.mocked(backend.getSgdbArtworkBase64).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "none" });
    const applying = applyArtwork(42, 100);

    await applyShortcutArtwork(batch);

    expect(backend.reportSgdbSlotArtwork).not.toHaveBeenCalled();
    finish({ base64: null, no_api_key: false });
    await applying;
  });

  it("gives the batch's lease back only once the icon's report is in", async () => {
    let finish: () => void = () => {};
    vi.mocked(backend.reportSgdbSlotArtwork).mockReturnValue(
      new Promise((resolve) => {
        finish = () => resolve({ success: true });
      }),
    );

    const applying = applyShortcutArtwork(batch);
    await vi.waitFor(() => expect(backend.reportSgdbSlotArtwork).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(backend.releasePruneConflictLease).not.toHaveBeenCalled();

    finish();
    await applying;
    expect(backend.releasePruneConflictLease).toHaveBeenCalledWith("icon-lease");
  });

  it("goes on when a report fails", async () => {
    vi.mocked(backend.reportSgdbSlotArtwork).mockRejectedValue(new Error("socket closed"));

    await expect(applyShortcutArtwork(batch)).resolves.toBe(2);
  });
});
