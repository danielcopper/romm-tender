import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import { toaster } from "../api/host";
import * as artwork from "./artwork";
import { _resetArtworkSlotsForTests, isKnownWithoutSgdbGame } from "./artworkSlotsStore";
import { settleArtworkGame, supersededNotice } from "./artworkGameChange";

vi.mock("./artwork", () => ({
  applyAutomaticArtwork: vi.fn(),
  applyKeptCurrentArtwork: vi.fn(),
  applyTabSlotImage: vi.fn(),
}));

const answer = (superseded: backend.SupersededGame | null): backend.ArtworkGame => ({
  success: true,
  has_api_key: true,
  game: { sgdb_id: 9999, source: "romm", name: null },
  superseded,
});

describe("settleArtworkGame (#2381 D17)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    _resetArtworkSlotsForTests();
    vi.mocked(backend.keepArtworkCurrent).mockResolvedValue({ status: "not_due", replaced: [] });
  });

  it("does nothing more where RomM's game replaced nothing", async () => {
    vi.mocked(backend.getArtworkGame).mockResolvedValue(answer(null));

    await settleArtworkGame(5000, 42);

    expect(vi.mocked(artwork.applyAutomaticArtwork)).not.toHaveBeenCalled();
    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
  });

  it("says so once and sets the slots to Tender's pick under RomM's game", async () => {
    vi.mocked(backend.getArtworkGame).mockResolvedValue(answer({ name: "Mario Golf", slots: ["logo"] }));

    await settleArtworkGame(5000, 42);

    expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
      expect.objectContaining({
        body: "RomM now maps this game to Mario Golf; your chosen images went back to automatic.",
      }),
    );
    expect(vi.mocked(artwork.applyAutomaticArtwork)).toHaveBeenCalledWith(42, 5000);
    expect(vi.mocked(backend.setArtworkSlotAutomatic)).not.toHaveBeenCalled();
  });

  it("sets a chosen cover back to RomM's through the endpoint that reloads it", async () => {
    const cover: backend.ArtworkSlotImage = { status: "cover", prune_lease_token: "l" };
    vi.mocked(backend.getArtworkGame).mockResolvedValue(answer({ name: null, slots: ["cover", "logo"] }));
    vi.mocked(backend.setArtworkSlotAutomatic).mockResolvedValue(cover);

    await settleArtworkGame(5000, 42);

    expect(vi.mocked(backend.setArtworkSlotAutomatic)).toHaveBeenCalledWith(42, "cover");
    expect(vi.mocked(artwork.applyTabSlotImage)).toHaveBeenCalledWith(42, 5000, "cover", cover, false);
  });

  it("keeps the automatic artwork current once RomM's game has settled (#2381 D5)", async () => {
    vi.mocked(backend.getArtworkGame).mockResolvedValue(answer({ name: "Mario Golf", slots: ["logo"] }));
    vi.mocked(backend.keepArtworkCurrent).mockResolvedValue({ status: "checked", replaced: ["hero"] });
    vi.mocked(artwork.applyKeptCurrentArtwork).mockResolvedValue(["hero"]);
    vi.mocked(backend.getArtworkSlots).mockResolvedValue({
      success: false,
      reason: "no_grid_dir",
      message: "Steam grid directory not found",
    });

    await settleArtworkGame(5000, 42);

    expect(vi.mocked(artwork.applyKeptCurrentArtwork)).toHaveBeenCalledWith(42, 5000, ["hero"]);
    const [automatic] = vi.mocked(artwork.applyAutomaticArtwork).mock.invocationCallOrder;
    const [check] = vi.mocked(backend.keepArtworkCurrent).mock.invocationCallOrder;
    expect(automatic).toBeLessThan(check ?? 0);
  });

  it("holds whether the ROM has a SteamGridDB game", async () => {
    vi.mocked(backend.getArtworkGame).mockResolvedValue({
      ...answer(null),
      game: { sgdb_id: null, source: null, name: null },
    });

    await settleArtworkGame(5000, 42);

    expect(isKnownWithoutSgdbGame(5000, 42)).toBe(true);
  });

  it("names another game where SteamGridDB's name could not be asked", () => {
    expect(supersededNotice({ name: null, slots: [] })).toBe(
      "RomM now maps this game to another game; your chosen images went back to automatic.",
    );
  });
});
