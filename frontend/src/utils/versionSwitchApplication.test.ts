import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import * as backend from "../api/backend";
import { artworkThumbToken } from "./artworkSlotsStore";
import { applyCommittedVersionSwitch, publishCommittedVersionSwitch } from "./versionSwitchApplication";
import { capturePruneLeaseAdmission, mountPruneLeaseOwner, releasePruneLeasesByOwner } from "./pruneLease";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  logWarn: vi.fn(),
}));

vi.mock("./sessionManager", () => ({ refreshAppIdMap: vi.fn().mockResolvedValue(undefined) }));

describe("publishCommittedVersionSwitch's cover (#2381 D16)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", { Apps: { SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined) } });
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "ROMM=" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("writes the cover the user chose for the version, never RomM's", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({
      base64: "CHOSEN=",
      no_api_key: false,
      image_id: 6,
      sha256: "sha6",
      sgdb_id: 9999,
      prune_lease_token: "lease",
    });
    const onCover = vi.fn();

    await publishCommittedVersionSwitch(5000, 42, onCover);

    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mock.calls).toEqual([[5000, "CHOSEN=", "png", 0]]);
    expect(vi.mocked(backend.fetchCoverBase64)).not.toHaveBeenCalled();
    expect(onCover).toHaveBeenCalledWith(42, "CHOSEN=");
  });

  it("writes RomM's cover where none was chosen", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });

    await publishCommittedVersionSwitch(5000, 42);

    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mock.calls).toEqual([[5000, "ROMM=", "png", 0]]);
  });

  it("writes Tender's SteamGridDB pick where neither a chosen cover nor RomM's exists, and reports it (#2381 D28)", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: null });
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({
      status: "image",
      base64: "SGDB=",
      image_id: 6,
      sha256: "sha6",
      sgdb_id: 9999,
      prune_lease_token: "lease",
    });
    const onCover = vi.fn();

    await publishCommittedVersionSwitch(5000, 42, onCover);

    expect(vi.mocked(backend.getSgdbSlotArtwork)).toHaveBeenCalledWith(42, 5000, "cover", true);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mock.calls).toEqual([[5000, "SGDB=", "png", 0]]);
    expect(vi.mocked(backend.reportSgdbSlotArtwork)).toHaveBeenCalledWith(42, "cover", "set", 9999, 6, "sha6");
    expect(onCover).toHaveBeenCalledWith(42, "SGDB=");
  });

  it("asks SteamGridDB for no cover where RomM's was written", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });

    await publishCommittedVersionSwitch(5000, 42);

    expect(vi.mocked(backend.getSgdbSlotArtwork)).not.toHaveBeenCalled();
  });

  it("writes no cover over a chosen one that cannot be had now", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: true, chosen: true });

    await publishCommittedVersionSwitch(5000, 42);

    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.fetchCoverBase64)).not.toHaveBeenCalled();
  });

  it("logs that a chosen cover cannot be served now, and why the cover Steam shows stays", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: true, chosen: true });

    await publishCommittedVersionSwitch(5000, 42);

    expect(vi.mocked(backend.logWarn)).toHaveBeenCalledWith(
      "Version switch: the cover chosen for rom 42 cannot be served now; the cover Steam shows stays",
    );
  });

  it("writes no cover where the read was refused, and logs the refusal without claiming a chosen cover", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({
      success: false,
      reason: "prune_active",
      message: "A cleanup is running",
    });

    await publishCommittedVersionSwitch(5000, 42);

    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.fetchCoverBase64)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logWarn)).toHaveBeenCalledWith(
      "Version switch: whether a cover was chosen for rom 42 could not be read (A cleanup is running); " +
        "the cover Steam shows stays",
    );
  });

  it("answers that it broadcast the switch", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    const broadcasts: unknown[] = [];
    const onChanged = (e: Event) => broadcasts.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onChanged);

    const broadcast = await publishCommittedVersionSwitch(5000, 42);

    globalThis.removeEventListener("romm_data_changed", onChanged);
    expect(broadcast).toBe(true);
    expect(broadcasts).toEqual([{ type: "version_switched", app_id: 5000, rom_id: 42 }]);
  });

  it("answers that it did not broadcast once its signal is aborted", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    const controller = new AbortController();
    controller.abort();
    const broadcasts: unknown[] = [];
    const onChanged = (e: Event) => broadcasts.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onChanged);

    const broadcast = await publishCommittedVersionSwitch(5000, 42, undefined, controller.signal);

    globalThis.removeEventListener("romm_data_changed", onChanged);
    expect(broadcast).toBe(false);
    expect(broadcasts).toEqual([]);
  });

  it("answers that it did not broadcast when its signal is aborted during the cover write", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    let finishWrite!: () => void;
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockReturnValue(
      new Promise<void>((resolve) => (finishWrite = resolve)) as never,
    );
    const controller = new AbortController();
    const broadcasts: unknown[] = [];
    const onChanged = (e: Event) => broadcasts.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onChanged);

    const published = publishCommittedVersionSwitch(5000, 42, undefined, controller.signal);
    await vi.waitFor(() => expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalled());
    controller.abort();
    finishWrite();
    const broadcast = await published;

    globalThis.removeEventListener("romm_data_changed", onChanged);
    expect(broadcast).toBe(false);
    expect(broadcasts).toEqual([]);
  });

  it("has the thumbnails load anew after the switch", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    const before = artworkThumbToken(5000, "cover");

    await publishCommittedVersionSwitch(5000, 42);

    expect(artworkThumbToken(5000, "cover")).not.toBe(before);
  });
});

describe("applyCommittedVersionSwitch's own lease", () => {
  it("writes nothing and broadcasts nothing when its caller goes before the writes begin", async () => {
    const owner = "version-picker:5000";
    mountPruneLeaseOwner(owner);
    const broadcasts: unknown[] = [];
    const onChanged = (e: Event) => broadcasts.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onChanged);

    const applied = applyCommittedVersionSwitch(
      {
        success: true,
        app_id: 5000,
        rom_id: 42,
        target_installed: true,
        prune_lease_token: "t",
      },
      undefined,
      capturePruneLeaseAdmission(owner),
    );
    const teardown = releasePruneLeasesByOwner(owner);
    const outcome = await applied;
    await teardown;

    globalThis.removeEventListener("romm_data_changed", onChanged);
    expect(outcome).toBe(false);
    expect(backend.getSgdbArtworkBase64).not.toHaveBeenCalled();
    expect(broadcasts).toEqual([]);
  });
});

describe("applyCommittedVersionSwitch while its cover write is open", () => {
  it("answers broadcast false when its lease is released while the cover write is open", async () => {
    const owner = "version-picker:5000";
    mountPruneLeaseOwner(owner);
    let finishArtwork!: () => void;
    vi.stubGlobal("SteamClient", { Apps: { SetCustomArtworkForApp: vi.fn() } });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "COVER=" });
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockReturnValue(
      new Promise<void>((resolve) => (finishArtwork = resolve)) as never,
    );
    const broadcasts: unknown[] = [];
    const onChanged = (e: Event) => broadcasts.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onChanged);

    const applied = applyCommittedVersionSwitch(
      {
        success: true,
        app_id: 5000,
        rom_id: 42,
        target_installed: true,
        prune_lease_token: "t",
      },
      undefined,
      capturePruneLeaseAdmission(owner),
    );
    await vi.waitFor(() => expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalled());
    const teardown = releasePruneLeasesByOwner(owner);
    finishArtwork();
    const outcome = await applied;
    await teardown;

    globalThis.removeEventListener("romm_data_changed", onChanged);
    expect(outcome).toBe(false);
    expect(broadcasts).toEqual([]);
  });
});

describe("applyCommittedVersionSwitch's lease", () => {
  const result = {
    success: true as const,
    app_id: 5000,
    rom_id: 42,
    target_installed: true,
    prune_lease_token: "switch-lease",
  };

  /** Run the switch's publish under *owner*, tearing *tornDown* down while RomM's cover is being read. */
  const switchWhileTearingDown = async (owner: string, tornDown: string): Promise<boolean> => {
    let answerCover!: (cover: { base64: null }) => void;
    vi.mocked(backend.fetchCoverBase64).mockReturnValue(new Promise((resolve) => (answerCover = resolve)));
    const broadcasts: unknown[] = [];
    const onChanged = (e: Event) => broadcasts.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onChanged);
    mountPruneLeaseOwner(owner);
    const applied = applyCommittedVersionSwitch(result, undefined, capturePruneLeaseAdmission(owner));
    await vi.waitFor(() => expect(backend.fetchCoverBase64).toHaveBeenCalled());
    const teardown = releasePruneLeasesByOwner(tornDown);
    answerCover({ base64: null });
    await applied.catch(() => undefined);
    await teardown;
    globalThis.removeEventListener("romm_data_changed", onChanged);
    await releasePruneLeasesByOwner(owner);
    return broadcasts.length > 0;
  };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
  });

  it("is held under the caller's owner, so that owner's teardown cancels the writes", async () => {
    expect(await switchWhileTearingDown("desktop-version-picker:5000", "desktop-version-picker:5000")).toBe(false);
  });

  it("is not cancelled by another owner's teardown for the same game", async () => {
    expect(await switchWhileTearingDown("desktop-version-picker:5000", "version-picker:5000")).toBe(true);
    expect(backend.logWarn).not.toHaveBeenCalledWith(expect.stringContaining("was cancelled"));
  });

  it("logs what a teardown before the writes began left undone, and writes nothing", async () => {
    const owner = "desktop-version-picker:5000";
    mountPruneLeaseOwner(owner);
    const applied = applyCommittedVersionSwitch(result, undefined, capturePruneLeaseAdmission(owner));
    const teardown = releasePruneLeasesByOwner(owner);
    await applied.catch(() => undefined);
    await teardown;

    expect(backend.getSgdbArtworkBase64).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logWarn)).toHaveBeenCalledWith(
      "Version switch: the lease for rom 42 (appId 5000) was cancelled; the cover and the broadcast were skipped",
    );
  });

  it("logs what a teardown before the lease is registered left undone, and writes nothing", async () => {
    const owner = "desktop-version-picker:5000";
    mountPruneLeaseOwner(owner);
    const admission = capturePruneLeaseAdmission(owner);
    await releasePruneLeasesByOwner(owner);

    await expect(applyCommittedVersionSwitch(result, undefined, admission)).rejects.toThrow();

    expect(backend.getSgdbArtworkBase64).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logWarn)).toHaveBeenCalledWith(
      "Version switch: the lease for rom 42 (appId 5000) was cancelled; the cover and the broadcast were skipped",
    );
  });
});

describe("publishCommittedVersionSwitch stopped by its signal", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
  });

  const broadcastsDuring = async (run: () => Promise<boolean>): Promise<unknown[]> => {
    const broadcasts: unknown[] = [];
    const onChanged = (e: Event) => broadcasts.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onChanged);
    try {
      await run();
    } finally {
      globalThis.removeEventListener("romm_data_changed", onChanged);
    }
    return broadcasts;
  };

  it("logs that the cover and the broadcast were skipped when stopped before RomM's cover is written", async () => {
    vi.stubGlobal("SteamClient", { Apps: { SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined) } });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "ROMM=" });
    const controller = new AbortController();
    controller.abort();

    const broadcasts = await broadcastsDuring(() =>
      publishCommittedVersionSwitch(5000, 42, undefined, controller.signal),
    );

    expect(broadcasts).toEqual([]);
    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logWarn)).toHaveBeenCalledWith(
      "Version switch: the lease for rom 42 (appId 5000) was cancelled; the cover and the broadcast were skipped",
    );
  });

  it("logs that only the broadcast was skipped when RomM has no cover to write", async () => {
    vi.stubGlobal("SteamClient", { Apps: { SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined) } });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: null });
    const controller = new AbortController();
    controller.abort();

    const broadcasts = await broadcastsDuring(() =>
      publishCommittedVersionSwitch(5000, 42, undefined, controller.signal),
    );

    expect(broadcasts).toEqual([]);
    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
    expect(vi.mocked(backend.logWarn)).toHaveBeenCalledWith(
      "Version switch: the lease for rom 42 (appId 5000) was cancelled; the broadcast was skipped",
    );
  });

  it("logs that only the broadcast was skipped when the chosen cover was already written", async () => {
    vi.stubGlobal("SteamClient", { Apps: { SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined) } });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({
      base64: "CHOSEN=",
      no_api_key: false,
      image_id: 6,
      sha256: "sha6",
      sgdb_id: 9999,
      prune_lease_token: "lease",
    });
    const controller = new AbortController();
    controller.abort();

    const broadcasts = await broadcastsDuring(() =>
      publishCommittedVersionSwitch(5000, 42, undefined, controller.signal),
    );

    expect(broadcasts).toEqual([]);
    expect(vi.mocked(backend.logWarn)).toHaveBeenCalledWith(
      "Version switch: the lease for rom 42 (appId 5000) was cancelled; the broadcast was skipped",
    );
  });

  it("logs that the broadcast was skipped when stopped while RomM's cover was written", async () => {
    const controller = new AbortController();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockImplementation(() => {
          controller.abort();
          return Promise.resolve(undefined);
        }),
      },
    });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "ROMM=" });

    const broadcasts = await broadcastsDuring(() =>
      publishCommittedVersionSwitch(5000, 42, undefined, controller.signal),
    );

    expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalled();
    expect(broadcasts).toEqual([]);
    expect(vi.mocked(backend.logWarn)).toHaveBeenCalledWith(
      "Version switch: the lease for rom 42 (appId 5000) was cancelled; the broadcast was skipped",
    );
  });
});
