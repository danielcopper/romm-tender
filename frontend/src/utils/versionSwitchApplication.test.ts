import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import { publishCommittedVersionSwitch } from "./versionSwitchApplication";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  logWarn: vi.fn(),
}));

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
});
