import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import * as backend from "../api/backend";
import { _resetArtworkSlotsForTests, loadArtworkSlots, noteArtworkWritten } from "../utils/artworkSlotsStore";
import { GameInfoTab } from "./GameInfoTab";

const PROPS = {
  romName: "Mario Golf",
  regions: [],
  languages: [],
  metadata: null,
  platformName: "N64",
  installed: false,
  installedRom: null,
};

function slotsSigned(coverSignature: string): backend.ArtworkSlots {
  return {
    success: true,
    has_api_key: true,
    game: { sgdb_id: 7, source: "romm", name: "Mario Golf" },
    slots: (["cover", "grid", "hero", "logo", "icon"] as const).map((slot) => ({
      slot,
      state: "automatic",
      source: slot === "cover" ? "romm" : "tender",
      image_id: null,
      has_choice: false,
      signature: slot === "cover" ? coverSignature : `${slot}-sig`,
    })),
  };
}

describe("GameInfoTab's descriptive rows", () => {
  const metadata = {
    summary: "A golf game.",
    genres: ["Sport", "Arcade"],
    companies: ["Camelot"],
    first_release_date: null,
    average_rating: null,
    game_modes: [],
    player_count: "",
    cached_at: 0,
  };

  it("draws the summary ahead of the metadata rows, and a list value as tags", () => {
    const { container } = render(<GameInfoTab {...PROPS} metadata={metadata} coverBase64={null} />);

    const summary = container.querySelector(".romm-panel-summary");
    const rows = [...container.querySelectorAll(".romm-panel-info-row")];
    expect(summary?.textContent).toBe("A golf game.");
    expect(summary?.compareDocumentPosition(rows[0]!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(rows.map((row) => row.querySelector(".romm-panel-label")?.textContent)).toEqual([
      "Platform",
      "Companies",
      "Genres",
    ]);
    expect(rows[1]?.querySelector(".romm-panel-value")?.textContent).toBe("Camelot");
    expect([...rows[2]!.querySelectorAll(".romm-panel-tag")].map((tag) => tag.textContent)).toEqual([
      "Sport",
      "Arcade",
    ]);
  });

  it("draws the platform alone where there is no record", () => {
    const { container } = render(<GameInfoTab {...PROPS} metadata={null} platformName="N64" coverBase64={null} />);

    const rows = [...container.querySelectorAll(".romm-panel-info-row")];
    expect(rows.map((row) => row.querySelector(".romm-panel-label")?.textContent)).toEqual(["Platform"]);
    expect(rows[0]?.querySelector(".romm-panel-value")?.textContent).toBe("N64");
  });
});

describe("GameInfoTab's cover (#2381 D18)", () => {
  beforeEach(() => {
    _resetArtworkSlotsForTests();
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({ appid: 5000 })),
      GetCustomVerticalCapsuleURLs: vi.fn(() => ["/customimages/5000p.jpg?v=9", "/customimages/5000p.png?v=9"]),
      allApps: [],
    });
  });

  it("shows the cover Steam shows, first of Steam's URLs first", () => {
    const { container } = render(<GameInfoTab {...PROPS} appId={5000} coverBase64="ROMM" />);

    expect(container.querySelector("img")?.getAttribute("src")).toMatch(
      /^https:\/\/steamloopback\.host\/customimages\/5000p\.jpg\?v=9&t=/,
    );
  });

  it("falls back to RomM's cover where none of Steam's loads", () => {
    const { container } = render(<GameInfoTab {...PROPS} appId={5000} coverBase64="ROMM" />);

    fireEvent.error(container.querySelector("img")!);
    fireEvent.error(container.querySelector("img")!);

    expect(container.querySelector("img")?.getAttribute("src")).toBe("data:image/png;base64,ROMM");
  });

  it("shows RomM's cover where Steam names none", () => {
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(() => null), allApps: [] });

    const { container } = render(<GameInfoTab {...PROPS} appId={5000} coverBase64="ROMM" />);

    expect(container.querySelector("img")?.getAttribute("src")).toBe("data:image/png;base64,ROMM");
  });

  it("signs the cover by the ARTWORK tab's read of the ROM showing, beside Steam's v=", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue(slotsSigned("c1"));
    await loadArtworkSlots(5000, 42);

    const { container } = render(<GameInfoTab {...PROPS} appId={5000} romId={42} coverBase64="ROMM" />);

    expect(container.querySelector("img")?.getAttribute("src")).toMatch(
      /^https:\/\/steamloopback\.host\/customimages\/5000p\.jpg\?v=9&s=c1&t=/,
    );
  });

  it("loads a cover set through Steam's own menu anew after an ARTWORK visit, with no read to sign it", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue(slotsSigned("c1"));
    await loadArtworkSlots(5000, 42);
    const { container, rerender } = render(<GameInfoTab {...PROPS} appId={5000} romId={42} coverBase64="ROMM" />);
    const before = container.querySelector("img")?.getAttribute("src");

    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({ appid: 5000 })),
      GetCustomVerticalCapsuleURLs: vi.fn(() => ["/customimages/5000p.jpg?v=10", "/customimages/5000p.png?v=10"]),
      allApps: [],
    });
    rerender(<GameInfoTab {...PROPS} appId={5000} romId={42} coverBase64="ROMM" />);

    expect(container.querySelector("img")?.getAttribute("src")).not.toBe(before);
  });

  it("keeps its cover when another slot is written, and loads it anew once the read signs the cover anew", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue(slotsSigned("c1"));
    await loadArtworkSlots(5000, 42);
    const { container } = render(<GameInfoTab {...PROPS} appId={5000} romId={42} coverBase64="ROMM" />);
    const before = container.querySelector("img")?.getAttribute("src");

    act(() => noteArtworkWritten(5000, ["hero"]));
    expect(container.querySelector("img")?.getAttribute("src")).toBe(before);

    vi.mocked(backend.getArtworkSlots).mockResolvedValue(slotsSigned("c2"));
    await act(() => loadArtworkSlots(5000, 42));
    expect(container.querySelector("img")?.getAttribute("src")).not.toBe(before);
  });
});
