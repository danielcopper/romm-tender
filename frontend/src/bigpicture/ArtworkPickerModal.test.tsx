import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, act, fireEvent } from "@testing-library/react";
import * as backend from "../api/backend";
import { ArtworkPickerModal } from "./ArtworkPickerModal";

const PAGE: backend.ArtworkCandidatePage = {
  success: true,
  candidates: [
    { image_id: 1, thumb: "https://cdn.example/t1.jpg", width: 460, height: 215 },
    { image_id: 2, thumb: "https://cdn.example/t2.jpg", width: 920, height: 430 },
  ],
  has_more: true,
  tenders_pick: 2,
};

function row(overrides: Partial<backend.ArtworkSlotRow> = {}): backend.ArtworkSlotRow {
  return {
    slot: "grid",
    state: "automatic",
    source: "tender",
    image_id: 2,
    has_choice: false,
    signature: "",
    ...overrides,
  };
}

function button(container: HTMLElement, text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button")).find((b) => b.textContent.includes(text));
  if (!found) throw new Error(`button containing "${text}" not found`);
  return found;
}

const flush = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

async function open(slotRow: backend.ArtworkSlotRow) {
  const onPick = vi.fn();
  const closeModal = vi.fn();
  const view = render(
    <ArtworkPickerModal romId={42} row={slotRow} gameName="Mario Golf" onPick={onPick} closeModal={closeModal} />,
  );
  await flush();
  return { ...view, onPick, closeModal };
}

describe("ArtworkPickerModal", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(backend.getArtworkSlotCandidates).mockResolvedValue(PAGE);
  });

  it("titles the slot and names the game and SteamGridDB's order", async () => {
    const { container } = await open(row());
    expect(container.textContent).toContain("Wide grid — choose an image");
    expect(container.textContent).toContain("SteamGridDB · Mario Golf · in SteamGridDB's order");
  });

  it("puts Automatic first and labels each candidate with its size and SteamGridDB thumbnail", async () => {
    const { container } = await open(row());
    const tiles = Array.from(container.querySelectorAll("button")).map((b) => b.textContent);
    expect(tiles[0]).toContain("Automatic — Tender picks by its own rule");
    expect(tiles[1]).toContain("460×215");
    expect(tiles[2]).toContain("920×430");
    expect(Array.from(container.querySelectorAll("img")).map((img) => img.getAttribute("src"))).toEqual([
      "https://cdn.example/t1.jpg",
      "https://cdn.example/t2.jpg",
    ]);
  });

  it("calls the cover's Automatic RomM's cover", async () => {
    const { container } = await open(row({ slot: "cover", source: "romm", image_id: null }));
    expect(button(container, "Automatic").textContent).toContain(
      "Automatic — RomM cover, follows RomM when it changes",
    );
  });

  it("marks Automatic CURRENT for a slot on automatic, and Tender's pick today", async () => {
    const { container } = await open(row());
    expect(button(container, "Automatic").textContent).toContain("CURRENT");
    expect(button(container, "920×430").textContent).toContain("Tender's pick today");
    expect(button(container, "920×430").textContent).not.toContain("CURRENT");
    expect(button(container, "460×215").textContent).not.toContain("Tender's pick today");
  });

  it("marks the chosen image CURRENT for a chosen slot", async () => {
    const { container } = await open(row({ state: "chosen", source: "chosen", image_id: 1 }));
    expect(button(container, "460×215").textContent).toContain("CURRENT");
    expect(button(container, "Automatic").textContent).not.toContain("CURRENT");
  });

  it("loads the next page on Load more", async () => {
    vi.mocked(backend.getArtworkSlotCandidates)
      .mockResolvedValueOnce(PAGE)
      .mockResolvedValueOnce({
        success: true,
        candidates: [{ image_id: 3, thumb: null, width: 920, height: 430 }],
        has_more: false,
        tenders_pick: null,
      });
    const { container } = await open(row());
    await act(async () => {
      fireEvent.click(button(container, "Load more"));
    });
    await flush();
    expect(vi.mocked(backend.getArtworkSlotCandidates)).toHaveBeenLastCalledWith(42, "grid", 1);
    expect(container.querySelectorAll("button").length).toBeGreaterThan(3);
    expect(container.textContent).not.toContain("Load more");
    expect(button(container, "920×430").textContent).toContain("Tender's pick today");
  });

  it("answers the selected image on Use this image", async () => {
    const { container, onPick, closeModal } = await open(row());
    fireEvent.click(button(container, "460×215"));
    fireEvent.click(button(container, "Use this image"));
    expect(onPick).toHaveBeenCalledWith({ kind: "image", imageId: 1 }, false);
    expect(closeModal).toHaveBeenCalled();
  });

  it("answers Automatic", async () => {
    const { container, onPick } = await open(row({ state: "chosen", source: "chosen" }));
    fireEvent.click(button(container, "Automatic"));
    fireEvent.click(button(container, "Use this image"));
    expect(onPick).toHaveBeenCalledWith({ kind: "automatic" }, false);
  });

  it("asks once more before it replaces an image set in Steam, and answers to replace it", async () => {
    const { container, onPick } = await open(row({ state: "set_in_steam", source: "steam", image_id: null }));
    fireEvent.click(button(container, "460×215"));
    fireEvent.click(button(container, "Use this image"));
    expect(onPick).not.toHaveBeenCalled();
    expect(container.textContent).toContain(
      "This image was set through Steam. Replacing it removes it; Tender keeps no copy.",
    );
    fireEvent.click(button(container, "Replace"));
    expect(onPick).toHaveBeenCalledWith({ kind: "image", imageId: 1 }, true);
  });

  it("Cancel on the second question replaces nothing", async () => {
    const { container, onPick } = await open(row({ state: "set_in_steam", source: "steam", image_id: null }));
    fireEvent.click(button(container, "Automatic"));
    fireEvent.click(button(container, "Use this image"));
    fireEvent.click(button(container, "Cancel"));
    expect(onPick).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Use this image");
  });

  it("asks nothing more for an icon set in Steam, whose file stays", async () => {
    const { container, onPick } = await open(row({ slot: "icon", state: "set_in_steam", source: "steam" }));
    fireEvent.click(button(container, "460×215"));
    fireEvent.click(button(container, "Use this image"));
    expect(onPick).toHaveBeenCalledWith({ kind: "image", imageId: 1 }, false);
  });

  it("offers no Use this image before a tile is selected", async () => {
    const { container } = await open(row());
    expect(button(container, "Use this image").disabled).toBe(true);
  });

  it("says why a listing failed", async () => {
    vi.mocked(backend.getArtworkSlotCandidates).mockResolvedValue({
      success: false,
      reason: "rate_limited",
      message: "SteamGridDB is rate-limiting requests — try again in a moment",
    });
    const { container } = await open(row());
    expect(container.textContent).toContain("SteamGridDB is rate-limiting requests — try again in a moment");
  });
});

describe("ArtworkPickerModal with Automatic alone (#2381 D19)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(backend.getArtworkSlotCandidates).mockResolvedValue(PAGE);
  });

  it("offers RomM's cover alone, says why, and asks SteamGridDB nothing", async () => {
    const onPick = vi.fn();
    const { container } = render(
      <ArtworkPickerModal
        romId={42}
        row={row({ slot: "cover", source: "romm", image_id: null })}
        gameName="#null"
        onlyAutomatic="Choose a SteamGridDB game to see its covers."
        onPick={onPick}
        closeModal={vi.fn()}
      />,
    );
    await flush();

    expect(container.textContent).toContain("Choose a SteamGridDB game to see its covers.");
    expect(container.textContent).not.toContain("SteamGridDB · ");
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(vi.mocked(backend.getArtworkSlotCandidates)).not.toHaveBeenCalled();
    fireEvent.click(button(container, "Automatic — RomM cover"));
    fireEvent.click(button(container, "Use this image"));
    expect(onPick).toHaveBeenCalledWith({ kind: "automatic" }, false);
  });
});

describe("ArtworkPickerModal's pages (#2381 device test)", () => {
  const many = (from: number, count: number, hasMore: boolean): backend.ArtworkCandidatePage => ({
    success: true,
    candidates: Array.from({ length: count }, (_, i) => ({
      image_id: from + i,
      thumb: `https://cdn.example/t${from + i}.jpg`,
      width: 512,
      height: 512,
    })),
    has_more: hasMore,
    tenders_pick: null,
  });

  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("fills a 4×4 grid: Automatic, 14 candidates and Load more last", async () => {
    vi.mocked(backend.getArtworkSlotCandidates).mockResolvedValue(many(1, 40, false));
    const { container } = await open(row());

    const grid = container.querySelector("img")!.closest("button")!.parentElement!;
    const tiles = Array.from(grid.children).map((tile) => tile.textContent);
    expect(container.querySelectorAll("img")).toHaveLength(14);
    expect(tiles).toHaveLength(16);
    expect(tiles[0]).toContain("Automatic");
    expect(tiles[15]).toBe("Load more");
  });

  it("Load more adds 16, asking SteamGridDB only once the held page runs out, and stays last", async () => {
    vi.mocked(backend.getArtworkSlotCandidates)
      .mockResolvedValueOnce(many(1, 20, true))
      .mockResolvedValueOnce(many(21, 20, true));
    const { container } = await open(row());

    await act(async () => {
      fireEvent.click(button(container, "Load more"));
    });
    await flush();

    expect(container.querySelectorAll("img")).toHaveLength(30);
    expect(vi.mocked(backend.getArtworkSlotCandidates)).toHaveBeenLastCalledWith(42, "grid", 1);
    const grid = container.querySelector("img")!.closest("button")!.parentElement!;
    expect(grid.children).toHaveLength(32);
    expect(grid.lastElementChild?.textContent).toBe("Load more");
  });

  it("puts focus on the first new tile, where Load more stood, also after a page fetch", async () => {
    let page: (answer: backend.ArtworkCandidatePage) => void = () => {};
    vi.mocked(backend.getArtworkSlotCandidates)
      .mockResolvedValueOnce(many(1, 14, true))
      .mockReturnValueOnce(new Promise((resolve) => (page = resolve)));
    const { container } = await open(row());
    const more = button(container, "Load more");

    await act(async () => {
      fireEvent.click(more);
    });
    const grid = container.querySelector("img")!.closest("button")!.parentElement!;
    expect(grid.children).toHaveLength(16);
    await act(async () => {
      page(many(15, 20, false));
    });
    await flush();

    const firstNew = container.querySelector('img[src="https://cdn.example/t15.jpg"]')!.closest("button");
    expect(document.activeElement).toBe(firstNew);
  });

  it("keeps focus on the last tile where Load more brought nothing new", async () => {
    vi.mocked(backend.getArtworkSlotCandidates)
      .mockResolvedValueOnce(many(1, 14, true))
      .mockResolvedValueOnce({ success: true, candidates: [], has_more: false, tenders_pick: null });
    const { container } = await open(row());

    await act(async () => {
      fireEvent.click(button(container, "Load more"));
    });
    await flush();

    const last = container.querySelector('img[src="https://cdn.example/t14.jpg"]')!.closest("button");
    expect(document.activeElement).toBe(last);
  });

  it("shows no more and keeps the grid where the page fetch fails", async () => {
    vi.mocked(backend.getArtworkSlotCandidates)
      .mockResolvedValueOnce(many(1, 20, true))
      .mockResolvedValueOnce({ success: false, reason: "server_unreachable", message: "SteamGridDB is unreachable" });
    const { container } = await open(row());

    await act(async () => {
      fireEvent.click(button(container, "Load more"));
    });
    await flush();

    expect(container.querySelectorAll("img")).toHaveLength(14);
    expect(container.textContent).toContain("SteamGridDB is unreachable");
    const grid = container.querySelector("img")!.closest("button")!.parentElement!;
    expect(grid.lastElementChild?.textContent).toBe("Load more");
  });

  it("tops a short page up from the next one, so Load more stays at the bottom right", async () => {
    vi.mocked(backend.getArtworkSlotCandidates)
      .mockResolvedValueOnce(many(1, 14, true))
      .mockResolvedValueOnce(many(15, 5, true))
      .mockResolvedValueOnce(many(20, 20, true));
    const { container } = await open(row());

    await act(async () => {
      fireEvent.click(button(container, "Load more"));
    });
    await flush();

    expect(container.querySelectorAll("img")).toHaveLength(30);
    const grid = container.querySelector("img")!.closest("button")!.parentElement!;
    expect(grid.children).toHaveLength(32);
    expect(grid.lastElementChild?.textContent).toBe("Load more");
  });

  it("labels no size where SteamGridDB states 0×0", async () => {
    vi.mocked(backend.getArtworkSlotCandidates).mockResolvedValue({
      success: true,
      candidates: [{ image_id: 9, thumb: "https://cdn.example/t9.jpg", width: 0, height: 0 }],
      has_more: false,
      tenders_pick: null,
    });
    const { container } = await open(row({ slot: "icon" }));

    expect(container.textContent).not.toContain("0×0");
  });

  it("keeps Cancel and Use this image out of the scrolling grid", async () => {
    vi.mocked(backend.getArtworkSlotCandidates).mockResolvedValue(many(1, 20, false));
    const { container } = await open(row());

    const scroller = container.querySelector("img")!.closest("div[style*='overflow']");
    expect(scroller).not.toBeNull();
    expect(scroller!.contains(button(container, "Use this image"))).toBe(false);
    expect(scroller!.contains(button(container, "Cancel"))).toBe(false);
  });
});
