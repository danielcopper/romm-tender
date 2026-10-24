import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { _resetArtworkSlotsForTests, loadArtworkSlots, noteArtworkWritten } from "../../utils/artworkSlotsStore";
import { GameView } from "./GameView";
import * as backend from "../../api/backend";
import * as gameDetailStore from "../../utils/gameDetailStore";
import * as sharedReads from "../../api/sharedReads";
import * as artwork from "../../utils/artwork";
import * as autoArtwork from "../../utils/autoArtwork";
import * as connectionHeartbeat from "../../utils/connectionHeartbeat";
import { clearMigration, setMigrationStatus } from "../../utils/migrationStore";
import type { RomMetadata } from "../../types";

vi.mock("../../utils/gameDetailStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../utils/gameDetailStore")>();
  return { ...actual, useGameDetail: vi.fn() };
});

vi.mock("../../api/sharedReads", () => ({
  getRomMetadataShared: vi.fn(),
}));

vi.mock("../../utils/artwork", () => ({
  cancelArtworkApply: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("../../utils/autoArtwork", () => ({
  useAutoArtwork: vi.fn(),
}));

vi.mock("../../utils/connectionHeartbeat", () => ({
  registerConnectionHeartbeat: vi.fn(),
}));

const APP_ID = 12345;

const METADATA: RomMetadata = {
  summary: "A golf game.",
  genres: ["Sport"],
  companies: ["Camelot"],
  first_release_date: null,
  average_rating: null,
  game_modes: [],
  player_count: "",
  cached_at: 1000,
};

function detail(overrides: Partial<gameDetailStore.GameDetailState> = {}): gameDetailStore.GameDetailState {
  return {
    ...gameDetailStore.getGameDetail(-1),
    romId: 42,
    romName: "Mario Golf (USA)",
    platformSlug: "gba",
    platformName: "Game Boy Advance",
    ...overrides,
  };
}

describe("GameView", () => {
  const releaseHeartbeat = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    _resetArtworkSlotsForTests();
    vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detail());
    vi.mocked(sharedReads.getRomMetadataShared).mockReturnValue(new Promise(() => {}));
    vi.mocked(connectionHeartbeat.registerConnectionHeartbeat).mockReturnValue(releaseHeartbeat);
    clearMigration();
    vi.mocked(backend.refreshMigrationState).mockReturnValue(new Promise(() => {}));
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({ appid: APP_ID, display_name: "Mario Golf: Advance Tour" })),
      GetCustomVerticalCapsuleURLs: vi.fn(() => [`/customimages/${APP_ID}p.jpg?v=9`]),
      allApps: [],
    });
  });

  it("draws the About card for the page's game: Steam's title, the cover Steam shows, the ROM's record", async () => {
    vi.mocked(sharedReads.getRomMetadataShared).mockResolvedValue(METADATA);

    render(<GameView appId={APP_ID} />);

    expect(screen.getByRole("heading", { name: "Mario Golf: Advance Tour" })).toBeInTheDocument();
    expect(screen.getByText("Game Boy Advance")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      expect.stringMatching(/^https:\/\/steamloopback\.host\/customimages\/12345p\.jpg\?v=9&t=/),
    );
    await waitFor(() => expect(screen.getByText("A golf game.")).toBeInTheDocument());
    expect(sharedReads.getRomMetadataShared).toHaveBeenCalledWith(42);
  });

  it("signs the cover by the ARTWORK read held for the page's ROM", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue({
      success: true,
      has_api_key: true,
      game: { sgdb_id: 7, source: "romm", name: "Mario Golf" },
      slots: [
        {
          slot: "cover",
          state: "automatic",
          source: "romm",
          image_id: null,
          has_choice: false,
          signature: "c42",
        },
      ],
    });
    await loadArtworkSlots(APP_ID, 42);

    render(<GameView appId={APP_ID} />);

    expect(screen.getByRole("img")).toHaveAttribute("src", expect.stringContaining("&s=c42&"));
  });

  it("loads the cover anew once Tender writes it", () => {
    render(<GameView appId={APP_ID} />);
    const before = screen.getByRole("img").getAttribute("src");

    act(() => noteArtworkWritten(APP_ID, ["cover"]));

    expect(screen.getByRole("img").getAttribute("src")).not.toBe(before);
  });

  it("titles the card with the ROM's name where Steam has no name for the shortcut", () => {
    vi.mocked(appStore.GetAppOverviewByAppID).mockReturnValue(null);

    render(<GameView appId={APP_ID} />);

    expect(screen.getByRole("heading", { name: "Mario Golf (USA)" })).toBeInTheDocument();
  });

  it("reads no record before the page's ROM is known", () => {
    vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detail({ romId: null }));

    render(<GameView appId={APP_ID} />);

    expect(sharedReads.getRomMetadataShared).not.toHaveBeenCalled();
  });

  it("holds the connection heartbeat while it is drawn", () => {
    const { unmount } = render(<GameView appId={APP_ID} />);
    expect(connectionHeartbeat.registerConnectionHeartbeat).toHaveBeenCalledTimes(1);
    expect(releaseHeartbeat).not.toHaveBeenCalled();

    unmount();
    expect(releaseHeartbeat).toHaveBeenCalledTimes(1);
  });

  it("runs the automatic artwork for the page's shortcut and ROM, and cancels an apply in flight on leaving", () => {
    const { unmount } = render(<GameView appId={APP_ID} />);
    expect(autoArtwork.useAutoArtwork).toHaveBeenCalledWith(APP_ID, 42, "Desktop auto-artwork error");
    expect(artwork.cancelArtworkApply).not.toHaveBeenCalled();

    unmount();
    expect(artwork.cancelArtworkApply).toHaveBeenCalledExactlyOnceWith(APP_ID);
  });

  describe("while a RetroDECK migration is pending", () => {
    const CARD_TITLE = "RetroDECK Migration Required";

    it("asks the backend on opening, and draws the migration card in place of the cards", async () => {
      vi.mocked(backend.refreshMigrationState).mockResolvedValue({ retrodeck: { pending: true } });

      render(<GameView appId={APP_ID} />);

      expect(await screen.findByText(CARD_TITLE)).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Mario Golf: Advance Tour" })).not.toBeInTheDocument();
      expect(backend.refreshMigrationState).toHaveBeenCalledTimes(1);
    });

    it("draws the cards again once the migration is no longer pending", async () => {
      setMigrationStatus({ pending: true });
      vi.mocked(backend.refreshMigrationState).mockResolvedValue({ retrodeck: { pending: false } });

      render(<GameView appId={APP_ID} />);

      expect(await screen.findByRole("heading", { name: "Mario Golf: Advance Tour" })).toBeInTheDocument();
      expect(screen.queryByText(CARD_TITLE)).not.toBeInTheDocument();
    });

    it("keeps the connection heartbeat and the automatic artwork behind the card", () => {
      setMigrationStatus({ pending: true });

      const { unmount } = render(<GameView appId={APP_ID} />);

      expect(screen.getByText(CARD_TITLE)).toBeInTheDocument();
      expect(connectionHeartbeat.registerConnectionHeartbeat).toHaveBeenCalledTimes(1);
      expect(releaseHeartbeat).not.toHaveBeenCalled();
      expect(autoArtwork.useAutoArtwork).toHaveBeenCalledWith(APP_ID, 42, "Desktop auto-artwork error");
      expect(artwork.cancelArtworkApply).not.toHaveBeenCalled();

      unmount();
      expect(releaseHeartbeat).toHaveBeenCalledTimes(1);
      expect(artwork.cancelArtworkApply).toHaveBeenCalledExactlyOnceWith(APP_ID);
    });
  });
});
