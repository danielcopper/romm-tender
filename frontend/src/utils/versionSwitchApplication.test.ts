import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import { publishCommittedVersionSwitch } from "./versionSwitchApplication";

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
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });

    await publishCommittedVersionSwitch(5000, 42);

    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mock.calls).toEqual([[5000, "ROMM=", "png", 0]]);
  });
});
