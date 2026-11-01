import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import { artworkThumbToken } from "./artworkSlotsStore";
import { applyCommittedVersionSwitch, publishCommittedVersionSwitch } from "./versionSwitchApplication";
import { capturePruneLeaseAdmission, mountPruneLeaseOwner, releasePruneLeasesByOwner } from "./pruneLease";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  logWarn: vi.fn(),
}));

vi.mock("./steamShortcuts", () => ({ setLaunchOptionsConfirmed: vi.fn() }));
vi.mock("./sessionManager", () => ({ refreshAppIdMap: vi.fn().mockResolvedValue(undefined) }));

describe("publishCommittedVersionSwitch's cover (#2381 D16)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", { Apps: { SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined) } });
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "ROMM=" });
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
        launch_options: "cmd",
        prune_lease_token: "t",
      },
      undefined,
      capturePruneLeaseAdmission(owner),
    );
    const teardown = releasePruneLeasesByOwner(owner);
    const outcome = await applied;
    await teardown;

    globalThis.removeEventListener("romm_data_changed", onChanged);
    expect(outcome).toEqual({ confirmed: false, broadcast: false });
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(broadcasts).toEqual([]);
  });
});

describe("applyCommittedVersionSwitch after its launch-command write", () => {
  it("publishes nothing when its caller goes while that write is open", async () => {
    const owner = "version-picker:5000";
    mountPruneLeaseOwner(owner);
    let confirm!: (ok: boolean) => void;
    vi.mocked(setLaunchOptionsConfirmed).mockReturnValue(new Promise((resolve) => (confirm = resolve)));
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false, chosen: false });
    const broadcasts: unknown[] = [];
    const onChanged = (e: Event) => broadcasts.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onChanged);

    const applied = applyCommittedVersionSwitch(
      {
        success: true,
        app_id: 5000,
        rom_id: 42,
        target_installed: true,
        launch_options: "cmd",
        prune_lease_token: "t",
      },
      undefined,
      capturePruneLeaseAdmission(owner),
    );
    await vi.waitFor(() => expect(setLaunchOptionsConfirmed).toHaveBeenCalled());
    const teardown = releasePruneLeasesByOwner(owner);
    confirm(true);
    const outcome = await applied;
    await teardown;

    globalThis.removeEventListener("romm_data_changed", onChanged);
    expect(outcome).toEqual({ confirmed: true, broadcast: false });
    expect(backend.getSgdbArtworkBase64).not.toHaveBeenCalled();
    expect(broadcasts).toEqual([]);
  });
});
