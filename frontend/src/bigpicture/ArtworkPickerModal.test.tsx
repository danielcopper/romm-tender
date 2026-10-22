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
  return { slot: "grid", state: "automatic", source: "tender", image_id: 2, has_choice: false, ...overrides };
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
