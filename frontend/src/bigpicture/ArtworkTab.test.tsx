import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, act, fireEvent } from "@testing-library/react";
import type { ReactElement } from "react";
import { showModal } from "@decky/ui";
import * as backend from "../api/backend";
import { toaster } from "../api/host";
import * as artwork from "../utils/artwork";
import { _resetArtworkSlotsForTests } from "../utils/artworkSlotsStore";
import { ArtworkTab } from "./ArtworkTab";
import type { ArtworkPickerModalProps } from "./ArtworkPickerModal";
import type { SgdbGamePickerModalProps } from "./SgdbGamePickerModal";

vi.mock("../utils/artwork", () => ({
  applyTabSlotImage: vi.fn(),
  applyAutomaticArtwork: vi.fn(),
}));

const APP_ID = 5000;

function answer(
  game: backend.ArtworkSlots["game"],
  rows: Partial<Record<backend.ArtworkTabSlot, Pick<backend.ArtworkSlotRow, "state" | "source">>> = {},
): backend.ArtworkSlots {
  return {
    success: true,
    has_api_key: true,
    game,
    superseded: null,
    slots: (["cover", "grid", "hero", "logo", "icon"] as const).map((slot) => ({
      slot,
      state: rows[slot]?.state ?? "automatic",
      source: rows[slot]?.source ?? (slot === "cover" ? "romm" : "tender"),
      image_id: null,
      has_choice: rows[slot]?.state === "chosen",
    })),
  };
}

const FROM_ROMM = { sgdb_id: 7, source: "romm" as const, name: "Mario Golf" };
const CHOSEN = { sgdb_id: 8, source: "chosen" as const, name: "Mario Golf 64" };
const NONE = { sgdb_id: null, source: null, name: null };

const flush = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

async function renderTab(slots: backend.ArtworkSlots) {
  vi.mocked(backend.getArtworkSlots).mockResolvedValue(slots);
  const view = render(<ArtworkTab appId={APP_ID} romId={42} romName="Mario Golf" />);
  await flush();
  return view;
}

function rowButton(container: HTMLElement, name: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button.romm-artwork-row")).find((b) =>
    b.textContent.startsWith(name),
  );
  if (!found) throw new Error(`row ${name} not found`);
  return found as HTMLButtonElement;
}

function lastModal<T>(): T {
  const calls = vi.mocked(showModal).mock.calls;
  return (calls[calls.length - 1]![0] as ReactElement<T>).props;
}

describe("ArtworkTab", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    _resetArtworkSlotsForTests();
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({
        appid: APP_ID,
        display_name: "",
        strDisplayName: "",
        rt_custom_image_mtime: 9,
      })),
      GetCustomVerticalCapsuleURLs: vi.fn(() => ["/customimages/5000p.jpg?v=9", "/customimages/5000p.png?v=9"]),
      GetCustomLandcapeImageURLs: vi.fn(() => ["/customimages/5000.png?v=9"]),
      allApps: [],
    });
  });

  describe("the game line", () => {
    it("names RomM's game and offers no change", async () => {
      const { container } = await renderTab(answer(FROM_ROMM));
      expect(container.textContent).toContain("SteamGridDB game: Mario Golf (from RomM)");
      expect(container.textContent).not.toContain("Change game…");
      expect(container.textContent).not.toContain("Choose game…");
    });

    it("names the user's game, says why, and offers Change game…", async () => {
      const { container } = await renderTab(answer(CHOSEN));
      expect(container.textContent).toContain("SteamGridDB game: Mario Golf 64 (chosen by you)");
      expect(container.textContent).toContain(
        "RomM has no SteamGridDB entry for this game, so you chose this one. If RomM gets one later, RomM's entry is used.",
      );
      expect(container.textContent).toContain("Change game…");
    });

    it("without a game says so and offers Choose game…, and the slots that need one say so", async () => {
      const { container } = await renderTab(
        answer(NONE, {
          grid: { state: "automatic", source: "needs_game" },
          hero: { state: "automatic", source: "needs_game" },
          logo: { state: "automatic", source: "needs_game" },
          icon: { state: "automatic", source: "needs_game" },
        }),
      );
      expect(container.textContent).toContain("No SteamGridDB game yet");
      expect(container.textContent).toContain(
        "RomM has no SteamGridDB entry for this game. Choose the game to see its artwork.",
      );
      expect(container.textContent).toContain("Choose game…");
      expect(rowButton(container, "Wide grid").textContent).toContain("Needs a SteamGridDB game");
      expect(rowButton(container, "Cover").textContent).toContain("RomM cover — kept current when RomM changes it");
    });
  });

  describe("the rows", () => {
    it("lists the five slots, each with where its image comes from and its state", async () => {
      const { container } = await renderTab(
        answer(FROM_ROMM, {
          logo: { state: "chosen", source: "chosen" },
          hero: { state: "set_in_steam", source: "steam" },
        }),
      );
      const rows = Array.from(container.querySelectorAll("button.romm-artwork-row")).map((b) => b.textContent);
      expect(rows).toHaveLength(5);
      expect(rows[0]).toContain("Cover");
      expect(rows[0]).toContain("Automatic");
      expect(rowButton(container, "Wide grid").textContent).toContain("SteamGridDB — Tender's pick");
      expect(rowButton(container, "Logo").textContent).toContain("SteamGridDB — chosen by you");
      expect(rowButton(container, "Logo").textContent).toContain("Chosen by you");
      expect(rowButton(container, "Hero").textContent).toContain("Set through Steam's “Set custom artwork”");
      expect(rowButton(container, "Hero").textContent).toContain("Set in Steam");
    });

    it("shows Steam's own copy of the image, first URL first", async () => {
      const { container } = await renderTab(answer(FROM_ROMM));
      const cover = rowButton(container, "Cover").querySelector("img");
      expect(cover?.getAttribute("src")).toBe("https://steamloopback.host/customimages/5000p.jpg?v=9");
      fireEvent.error(cover!);
      expect(rowButton(container, "Cover").querySelector("img")?.getAttribute("src")).toBe(
        "https://steamloopback.host/customimages/5000p.png?v=9",
      );
    });

    it("pressing a row opens its picker", async () => {
      const { container } = await renderTab(answer(FROM_ROMM));
      fireEvent.click(rowButton(container, "Logo"));
      const props = lastModal<ArtworkPickerModalProps>();
      expect(props.row.slot).toBe("logo");
      expect(props.gameName).toBe("Mario Golf");
    });

    it("without a game a row opens nothing", async () => {
      const { container } = await renderTab(answer(NONE));
      fireEvent.click(rowButton(container, "Logo"));
      expect(vi.mocked(showModal)).not.toHaveBeenCalled();
    });

    it("a pick is chosen, set and the tab read again", async () => {
      const image: backend.ArtworkSlotImage = {
        status: "image",
        base64: "IMG=",
        image_id: 12,
        sha256: "sha",
        sgdb_id: 7,
        prune_lease_token: "lease",
      };
      vi.mocked(backend.chooseArtworkSlotImage).mockResolvedValue(image);
      vi.mocked(artwork.applyTabSlotImage).mockResolvedValue("set");
      const { container } = await renderTab(answer(FROM_ROMM, { hero: { state: "set_in_steam", source: "steam" } }));
      fireEvent.click(rowButton(container, "Hero"));
      const reads = vi.mocked(backend.getArtworkSlots).mock.calls.length;

      await act(async () => {
        lastModal<ArtworkPickerModalProps>().onPick({ kind: "image", imageId: 12 }, true);
      });
      await flush();

      expect(vi.mocked(backend.chooseArtworkSlotImage)).toHaveBeenCalledWith(42, "hero", 12);
      expect(vi.mocked(artwork.applyTabSlotImage)).toHaveBeenCalledWith(42, APP_ID, "hero", image, true);
      expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(expect.objectContaining({ body: "Artwork updated" }));
      expect(vi.mocked(backend.getArtworkSlots).mock.calls.length).toBeGreaterThan(reads);
    });

    it("Automatic sets the slot back through the backend", async () => {
      vi.mocked(backend.setArtworkSlotAutomatic).mockResolvedValue({ status: "cover", prune_lease_token: "l" });
      vi.mocked(artwork.applyTabSlotImage).mockResolvedValue("set");
      const { container } = await renderTab(answer(FROM_ROMM, { cover: { state: "chosen", source: "chosen" } }));
      fireEvent.click(rowButton(container, "Cover"));

      await act(async () => {
        lastModal<ArtworkPickerModalProps>().onPick({ kind: "automatic" }, false);
      });
      await flush();

      expect(vi.mocked(backend.setArtworkSlotAutomatic)).toHaveBeenCalledWith(42, "cover");
      expect(vi.mocked(artwork.applyTabSlotImage)).toHaveBeenCalledWith(
        42,
        APP_ID,
        "cover",
        { status: "cover", prune_lease_token: "l" },
        false,
      );
    });

    it("a refused pick toasts its message and sets nothing", async () => {
      vi.mocked(backend.chooseArtworkSlotImage).mockResolvedValue({
        success: false,
        reason: "image_not_found",
        message: "SteamGridDB no longer lists this image for the game",
      });
      const { container } = await renderTab(answer(FROM_ROMM));
      fireEvent.click(rowButton(container, "Logo"));

      await act(async () => {
        lastModal<ArtworkPickerModalProps>().onPick({ kind: "image", imageId: 99 }, false);
      });
      await flush();

      expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
        expect.objectContaining({ body: "SteamGridDB no longer lists this image for the game" }),
      );
      expect(vi.mocked(artwork.applyTabSlotImage)).not.toHaveBeenCalled();
    });
  });

  describe("the fix round's rows (#2381)", () => {
    it("words an icon set in Steam as the shortcut's properties", async () => {
      const { container } = await renderTab(answer(FROM_ROMM, { icon: { state: "set_in_steam", source: "steam" } }));
      expect(rowButton(container, "Icon").textContent).toContain("Set in the shortcut's properties");
    });

    it("says a key is needed where none is configured", async () => {
      const { container } = await renderTab({ ...answer(NONE), has_api_key: false });
      expect(container.textContent).toContain("Add a SteamGridDB API key in Settings to choose images.");
    });

    it("warns of discarded choices also where the only chosen slot now reads set in Steam", async () => {
      const slots = answer(CHOSEN, { logo: { state: "set_in_steam", source: "steam" } });
      slots.slots = slots.slots.map((row) => (row.slot === "logo" ? { ...row, has_choice: true } : row));
      const { container } = await renderTab(slots);
      fireEvent.click(Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Change game…")!);
      expect(lastModal<SgdbGamePickerModalProps>().discardsChoices).toBe(true);
    });

    it("on a read that finds RomM's game replaced the chosen one, says so and applies automatic", async () => {
      vi.mocked(artwork.applyAutomaticArtwork).mockResolvedValue(1);
      await renderTab({ ...answer(FROM_ROMM), superseded: { name: "Mario Golf", slots: ["logo"] } });
      expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
        expect.objectContaining({
          body: "RomM now maps this game to Mario Golf; your chosen images went back to automatic.",
        }),
      );
      expect(vi.mocked(artwork.applyAutomaticArtwork)).toHaveBeenCalledWith(42, APP_ID);
    });
  });

  describe("the game picker", () => {
    it("says the change discards the chosen images where there are some", async () => {
      const { container } = await renderTab(answer(CHOSEN, { logo: { state: "chosen", source: "chosen" } }));
      fireEvent.click(Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Change game…")!);
      expect(lastModal<SgdbGamePickerModalProps>().discardsChoices).toBe(true);
    });

    it("says nothing of the kind where none was chosen", async () => {
      const { container } = await renderTab(answer(NONE));
      fireEvent.click(Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Choose game…")!);
      expect(lastModal<SgdbGamePickerModalProps>().discardsChoices).toBe(false);
    });

    it("after a game change sets a chosen cover back to RomM's in Steam", async () => {
      vi.mocked(backend.setArtworkSlotAutomatic).mockResolvedValue({ status: "cover", prune_lease_token: "l" });
      vi.mocked(artwork.applyTabSlotImage).mockResolvedValue("set");
      const { container } = await renderTab(answer(CHOSEN, { cover: { state: "chosen", source: "chosen" } }));
      fireEvent.click(Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Change game…")!);

      await act(async () => {
        lastModal<SgdbGamePickerModalProps>().onApplied(2);
      });
      await flush();

      expect(vi.mocked(backend.setArtworkSlotAutomatic)).toHaveBeenCalledWith(42, "cover");
    });
  });
});
