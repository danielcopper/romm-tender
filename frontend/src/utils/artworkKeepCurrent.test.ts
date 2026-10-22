import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import { toaster } from "../api/host";
import * as artwork from "./artwork";
import * as slotsStore from "./artworkSlotsStore";
import { keepArtworkCurrentOnVisit, keptCurrentNotice } from "./artworkKeepCurrent";

vi.mock("./artwork", () => ({ applyKeptCurrentArtwork: vi.fn() }));
vi.mock("./artworkSlotsStore", () => ({ loadArtworkSlots: vi.fn() }));

const checked = (replaced: backend.ArtworkTabSlot[]): backend.ArtworkKeptCurrent => ({ status: "checked", replaced });

describe("keptCurrentNotice (#2381 D26)", () => {
  it("names one slot", () => {
    expect(keptCurrentNotice(["logo"])).toBe("Updated the logo from SteamGridDB");
  });

  it("names two slots", () => {
    expect(keptCurrentNotice(["hero", "logo"])).toBe("Updated the hero and the logo from SteamGridDB");
  });

  it("names every slot", () => {
    expect(keptCurrentNotice(["cover", "grid", "hero", "logo", "icon"])).toBe(
      "Updated the cover, the wide grid, the hero, the logo and the icon from SteamGridDB",
    );
  });
});

describe("keepArtworkCurrentOnVisit (#2381 D5, D26)", () => {
  const coverRefreshed = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    globalThis.addEventListener("romm_data_changed", coverRefreshed);
    return () => globalThis.removeEventListener("romm_data_changed", coverRefreshed);
  });

  it("hands Steam each replaced image and says once which slots changed", async () => {
    vi.mocked(backend.keepArtworkCurrent).mockResolvedValue(checked(["hero", "logo"]));
    vi.mocked(artwork.applyKeptCurrentArtwork).mockResolvedValue(["hero", "logo"]);

    await keepArtworkCurrentOnVisit(5000, 42);

    expect(vi.mocked(backend.keepArtworkCurrent)).toHaveBeenCalledWith(42);
    expect(vi.mocked(artwork.applyKeptCurrentArtwork)).toHaveBeenCalledWith(42, 5000, ["hero", "logo"]);
    expect(vi.mocked(toaster.toast)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
      expect.objectContaining({ body: "Updated the hero and the logo from SteamGridDB" }),
    );
    expect(vi.mocked(slotsStore.loadArtworkSlots)).toHaveBeenCalledWith(5000, 42);
    expect(coverRefreshed).not.toHaveBeenCalled();
  });

  it("names only the slots Steam took", async () => {
    vi.mocked(backend.keepArtworkCurrent).mockResolvedValue(checked(["hero", "logo"]));
    vi.mocked(artwork.applyKeptCurrentArtwork).mockResolvedValue(["logo"]);

    await keepArtworkCurrentOnVisit(5000, 42);

    expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
      expect.objectContaining({ body: "Updated the logo from SteamGridDB" }),
    );
  });

  it("tells the game page's cover a replaced cover", async () => {
    vi.mocked(backend.keepArtworkCurrent).mockResolvedValue(checked(["cover"]));
    vi.mocked(artwork.applyKeptCurrentArtwork).mockResolvedValue(["cover"]);

    await keepArtworkCurrentOnVisit(5000, 42);

    expect(coverRefreshed).toHaveBeenCalledTimes(1);
    expect((coverRefreshed.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({ type: "cover_refreshed", rom_id: 42 });
  });

  it("writes and says nothing where nothing was replaced", async () => {
    vi.mocked(backend.keepArtworkCurrent).mockResolvedValue({ status: "not_due", replaced: [] });

    await keepArtworkCurrentOnVisit(5000, 42);

    expect(vi.mocked(artwork.applyKeptCurrentArtwork)).not.toHaveBeenCalled();
    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
  });

  it("says nothing where Steam took none of the replaced images", async () => {
    vi.mocked(backend.keepArtworkCurrent).mockResolvedValue(checked(["logo"]));
    vi.mocked(artwork.applyKeptCurrentArtwork).mockResolvedValue([]);

    await keepArtworkCurrentOnVisit(5000, 42);

    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
    expect(vi.mocked(slotsStore.loadArtworkSlots)).not.toHaveBeenCalled();
  });

  it("writes and says nothing where the check was refused or did not arrive", async () => {
    vi.mocked(backend.keepArtworkCurrent)
      .mockResolvedValueOnce({ success: false, reason: "prune_active", message: "A cleanup is running" })
      .mockRejectedValueOnce(new Error("connection lost"));

    await keepArtworkCurrentOnVisit(5000, 42);
    await keepArtworkCurrentOnVisit(5000, 42);

    expect(vi.mocked(artwork.applyKeptCurrentArtwork)).not.toHaveBeenCalled();
    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.debugLog)).toHaveBeenCalledWith(expect.stringContaining("connection lost"));
  });
});
