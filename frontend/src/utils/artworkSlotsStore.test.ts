import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import * as backend from "../api/backend";
import {
  _resetArtworkSlotsForTests,
  artworkThumbToken,
  heldArtworkSlots,
  isKnownWithoutSgdbGame,
  keepArtworkTabOpen,
  keptArtworkSlot,
  loadArtworkSlots,
  noteArtworkWritten,
  writeSlotArtwork,
} from "./artworkSlotsStore";

const slots = (sgdbId: number | null): backend.ArtworkSlots => ({
  success: true,
  has_api_key: true,
  game: { sgdb_id: sgdbId, source: sgdbId === null ? null : "romm", name: null },
  slots: [],
});

describe("artworkSlotsStore", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    _resetArtworkSlotsForTests();
  });

  it("knows a ROM without a SteamGridDB game once read", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue(slots(null));
    expect(isKnownWithoutSgdbGame(5000, 42)).toBe(false);

    await loadArtworkSlots(5000, 42);

    expect(isKnownWithoutSgdbGame(5000, 42)).toBe(true);
  });

  it("a ROM with a game is not without one", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue(slots(7));
    await loadArtworkSlots(5000, 42);
    expect(isKnownWithoutSgdbGame(5000, 42)).toBe(false);
  });

  it("an answer about another ROM of the shortcut is not taken for this one", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue(slots(null));
    await loadArtworkSlots(5000, 41);
    expect(heldArtworkSlots(5000, 42)).toBeNull();
    expect(isKnownWithoutSgdbGame(5000, 42)).toBe(false);
  });

  it("a failed read holds its message and no answer", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue({ success: false, reason: "no_grid_dir", message: "no grid" });
    await expect(loadArtworkSlots(5000, 42)).resolves.toBeNull();
    expect(heldArtworkSlots(5000, 42)).toEqual({ romId: 42, slots: null, failure: "no grid" });
  });

  it("an earlier read that lands later is not kept over the newer one", async () => {
    let first: (value: backend.ArtworkSlots) => void = () => undefined;
    vi.mocked(backend.getArtworkSlots)
      .mockReturnValueOnce(new Promise((resolve) => (first = resolve)))
      .mockResolvedValueOnce(slots(7));
    const older = loadArtworkSlots(5000, 42);
    await loadArtworkSlots(5000, 42);
    first(slots(null));
    await older;
    expect(isKnownWithoutSgdbGame(5000, 42)).toBe(false);
  });
});

describe("the ARTWORK tab kept across a remount (#2381 device test)", () => {
  beforeEach(() => {
    _resetArtworkSlotsForTests();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps the tab and the slot for a while after a press, then no longer", () => {
    keepArtworkTabOpen(5000, "hero");
    expect(keptArtworkSlot(5000)).toBe("hero");
    expect(keptArtworkSlot(6000)).toBeNull();

    vi.advanceTimersByTime(16_000);

    expect(keptArtworkSlot(5000)).toBeNull();
  });

  it("moves only the token of the slot written, and every slot's where none is named", () => {
    const logo = artworkThumbToken(5000, "logo");
    const hero = artworkThumbToken(5000, "hero");

    noteArtworkWritten(5000, ["logo"]);
    expect(artworkThumbToken(5000, "logo")).not.toBe(logo);
    expect(artworkThumbToken(5000, "hero")).toBe(hero);
    expect(artworkThumbToken(6000, "logo")).toBe(logo);

    noteArtworkWritten(5000);
    expect(artworkThumbToken(5000, "hero")).not.toBe(hero);
  });

  it("moves the slot's token after a write and answers what the write answered", async () => {
    const logo = artworkThumbToken(5000, "logo");

    await expect(writeSlotArtwork(5000, "logo", () => "done")).resolves.toBe("done");

    expect(artworkThumbToken(5000, "logo")).not.toBe(logo);
  });

  it("moves the slot's token also where Steam refuses the write, and passes the refusal on", async () => {
    const logo = artworkThumbToken(5000, "logo");
    await expect(writeSlotArtwork(5000, "logo", () => Promise.reject(new Error("refused")))).rejects.toThrow("refused");
    expect(artworkThumbToken(5000, "logo")).not.toBe(logo);

    const after = artworkThumbToken(5000, "logo");
    await expect(
      writeSlotArtwork(5000, "logo", () => {
        throw new Error("no such app");
      }),
    ).rejects.toThrow("no such app");
    expect(artworkThumbToken(5000, "logo")).not.toBe(after);
  });
});
