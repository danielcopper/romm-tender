import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { PlayButtonBadges } from "./PlayButtonBadges";
import * as backend from "../../api/backend";
import { BIOS_MISSING_RED } from "../../utils/biosColor";
import * as playtimeReconcile from "../../utils/playtimeReconcile";
import type { GameDetailState } from "../../utils/gameDetailStore";
import type { SaveSetupInfo, SaveStatus, SaveSyncAvailability } from "../../types";

vi.mock("../../api/backend", () => ({
  debugLog: vi.fn(),
  getSaveSetupInfo: vi.fn(),
  getSaveSyncAvailability: vi.fn(),
  isSaveTrackingConfigured: vi.fn(),
}));

vi.mock("../../utils/playtimeReconcile", () => ({
  useGamePlaytime: vi.fn(),
}));

const APP_ID = 123;
const ROM_ID = 100;

function detail(overrides: Partial<GameDetailState> = {}): GameDetailState {
  return {
    romId: ROM_ID,
    installed: true,
    downloadSizeBytes: null,
    saveSyncEnabled: false,
    saveStatus: null,
    saveSyncStatus: null,
    saveSyncLabel: "",
    saveSyncCheckedAt: null,
    biosRequiredMissing: false,
    biosLabel: "",
    ...overrides,
  } as GameDetailState;
}

const SETUP_TO_CHOOSE: SaveSetupInfo = {
  has_local_saves: true,
  local_files: [],
  server_slots: [],
  default_slot: "default",
  slot_confirmed: false,
  active_slot: null,
  recommended_action: "show_wizard",
};

const SYNCS: SaveSyncAvailability = { available: true, installed: true, established: true, emulator_label: null };

const REFUSED: SaveSyncAvailability = {
  ...SYNCS,
  available: false,
  reason: "save_shape_unsupported",
  message: "Save sync is unavailable: this emulator keeps one save card that all games share.",
  short_message: "Shared save",
};

function draw(d: GameDetailState, { saveStatus = null as SaveStatus | null, isOffline = false } = {}) {
  return <PlayButtonBadges appId={APP_ID} detail={d} saveStatus={saveStatus} isOffline={isOffline} />;
}

function dataChanged(romId?: number): void {
  const eventDetail = romId === undefined ? { type: "save_sync" } : { type: "save_sync", rom_id: romId };
  act(() => {
    globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: eventDetail }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(playtimeReconcile.useGamePlaytime).mockReturnValue({
    lastPlayed: "Yesterday",
    playtime: "2h 30m",
    steamPlaytime: "2.5 hours",
  });
  vi.mocked(backend.debugLog).mockResolvedValue(undefined as never);
  vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
    configured: true,
    active_slot: "slot1",
    setup_needed: false,
  });
  vi.mocked(backend.getSaveSetupInfo).mockResolvedValue(SETUP_TO_CHOOSE);
  vi.mocked(backend.getSaveSyncAvailability).mockResolvedValue(SYNCS);
});

describe("PlayButtonBadges", () => {
  describe("playtime", () => {
    it("asks for this app's and this ROM's and draws both", () => {
      render(draw(detail()));

      expect(playtimeReconcile.useGamePlaytime).toHaveBeenCalledWith(APP_ID, ROM_ID, "DesktopPlayButtonBadges");
      expect(screen.getByText("LAST PLAYED")).toBeInTheDocument();
      expect(screen.getByText("Yesterday")).toBeInTheDocument();
      expect(screen.getByText("PLAY TIME")).toBeInTheDocument();
      expect(screen.getByText("2.5 hours")).toBeInTheDocument();
    });

    it("draws no play time at none, whatever Big Picture's words say", () => {
      vi.mocked(playtimeReconcile.useGamePlaytime).mockReturnValue({
        lastPlayed: "Yesterday",
        playtime: "None",
        steamPlaytime: "",
      });
      render(draw(detail()));

      expect(screen.queryByText("PLAY TIME")).not.toBeInTheDocument();
      expect(screen.queryByText("None")).not.toBeInTheDocument();
    });

    it("draws neither where it has no words", () => {
      vi.mocked(playtimeReconcile.useGamePlaytime).mockReturnValue({ lastPlayed: "", playtime: "", steamPlaytime: "" });
      render(draw(detail()));

      expect(screen.queryByText("LAST PLAYED")).not.toBeInTheDocument();
      expect(screen.queryByText("PLAY TIME")).not.toBeInTheDocument();
    });
  });

  describe("space required", () => {
    const SIZE = 1024 * 1024 * 50;

    it("is drawn for a game not on this device", () => {
      render(draw(detail({ installed: false, downloadSizeBytes: SIZE })));

      expect(screen.getByText("SPACE REQUIRED")).toBeInTheDocument();
      expect(screen.getByText("50.0 MB")).toBeInTheDocument();
    });

    it("is not drawn once the game is on this device", () => {
      render(draw(detail({ installed: true, downloadSizeBytes: SIZE })));
      expect(screen.queryByText("SPACE REQUIRED")).not.toBeInTheDocument();
    });

    it.each([null, 0])("is not drawn for a size of %s", (downloadSizeBytes) => {
      render(draw(detail({ installed: false, downloadSizeBytes })));
      expect(screen.queryByText("SPACE REQUIRED")).not.toBeInTheDocument();
    });
  });

  describe("save sync", () => {
    it("draws its label and dot in the helper's tone", async () => {
      render(draw(detail({ saveSyncEnabled: true, saveSyncStatus: "conflict", saveSyncLabel: "Conflict" })));
      await act(async () => {});

      const badge = screen.getByText("SAVE SYNC").closest(".tender-desktop-save-sync")!;
      expect(screen.getByText("Save conflict")).toBeInTheDocument();
      expect(badge.querySelector(".tender-desktop-badge-dot")).toHaveStyle({ backgroundColor: "#d4a72c" });
    });

    it("says it is off with save sync off", () => {
      render(draw(detail()));

      expect(screen.getByText("Disabled")).toBeInTheDocument();
    });

    it("judges by the save status it is handed, never the one left in the detail", async () => {
      const anotherVersions = { rom_id: 99, files: [], conflicts: [{}] } as unknown as SaveStatus;
      render(draw(detail({ saveSyncEnabled: true, saveStatus: anotherVersions })));
      await act(async () => {});

      expect(screen.getByText("Not checked")).toBeInTheDocument();
    });
  });

  describe("whether the game's saves sync", () => {
    const ENABLED = detail({ saveSyncEnabled: true, saveSyncStatus: "none", saveSyncLabel: "No saves" });

    it("says why they do not, in grey", async () => {
      vi.mocked(backend.getSaveSyncAvailability).mockResolvedValue(REFUSED);
      render(draw(ENABLED));

      expect(await screen.findByText("Shared save")).toBeInTheDocument();
      const badge = screen.getByText("SAVE SYNC").closest(".tender-desktop-save-sync")!;
      expect(badge.querySelector(".tender-desktop-badge-dot")).toHaveStyle({ backgroundColor: "#8f98a0" });
    });

    it("leaves the store's verdict where they do", async () => {
      render(draw(ENABLED));
      await waitFor(() => expect(backend.getSaveSyncAvailability).toHaveBeenCalledWith(ROM_ID));
      await act(async () => {});

      expect(screen.getByText("No saves")).toBeInTheDocument();
    });

    it.each([
      ["with save sync off", detail(), false],
      ["for a game not on this device", detail({ saveSyncEnabled: true, installed: false }), false],
      ["while RomM is offline", ENABLED, true],
    ])("is not asked %s", (_name, d, isOffline) => {
      render(draw(d, { isOffline }));

      expect(backend.getSaveSyncAvailability).not.toHaveBeenCalled();
    });

    it.each([
      ["the emulator sources changing", { type: "emulator_sources" }],
      ["a platform's core changing", { type: "core_changed", platform_slug: "psx" }],
      ["this ROM's save sync", { type: "save_sync", rom_id: ROM_ID }],
      ["a save sync that names no ROM", { type: "save_sync" }],
    ])("is asked again on %s", async (_name, eventDetail) => {
      render(draw(ENABLED));
      await waitFor(() => expect(backend.getSaveSyncAvailability).toHaveBeenCalledTimes(1));

      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: eventDetail }));
      });

      await waitFor(() => expect(backend.getSaveSyncAvailability).toHaveBeenCalledTimes(2));
    });

    it("is not asked again on another ROM's save sync, or on another change", async () => {
      render(draw(ENABLED));
      await waitFor(() => expect(backend.getSaveSyncAvailability).toHaveBeenCalledTimes(1));

      dataChanged(ROM_ID + 1);
      act(() => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "metadata", rom_id: ROM_ID } }),
        );
      });
      await act(async () => {});

      expect(backend.getSaveSyncAvailability).toHaveBeenCalledTimes(1);
    });

    it("does not stand for a game since uninstalled", async () => {
      vi.mocked(backend.getSaveSyncAvailability).mockResolvedValue(REFUSED);
      const { rerender } = render(draw(ENABLED));
      await screen.findByText("Shared save");

      rerender(draw({ ...ENABLED, installed: false }));

      expect(screen.queryByText("Shared save")).not.toBeInTheDocument();
    });

    it("logs a read that failed and makes no claim", async () => {
      vi.mocked(backend.getSaveSyncAvailability).mockRejectedValue(new Error("gone"));
      render(draw(ENABLED));

      await waitFor(() =>
        expect(backend.debugLog).toHaveBeenCalledWith(
          "DesktopPlayButtonBadges: save sync availability read failed: Error: gone",
        ),
      );
      expect(screen.getByText("No saves")).toBeInTheDocument();
    });

    it("holds no answer once a re-read fails, rather than the one from before", async () => {
      vi.mocked(backend.getSaveSyncAvailability).mockResolvedValue(REFUSED);
      render(draw(ENABLED));
      await screen.findByText("Shared save");

      vi.mocked(backend.getSaveSyncAvailability).mockRejectedValue(new Error("gone"));
      act(() => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "core_changed", platform_slug: "psx" } }),
        );
      });

      expect(await screen.findByText("No saves")).toBeInTheDocument();
      expect(screen.queryByText("Shared save")).not.toBeInTheDocument();
    });
  });

  describe("how long ago a sync was", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("moves on while the page stays open", async () => {
      vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
      vi.setSystemTime(new Date("2026-11-06T12:00:00Z"));
      render(
        draw(
          detail({
            saveSyncEnabled: true,
            saveSyncStatus: "synced",
            saveSyncLabel: "5m ago",
            saveSyncCheckedAt: "2026-11-06T11:55:00Z",
          }),
        ),
      );
      await act(async () => {});
      expect(screen.getByText("Synced 5m ago")).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(60_000);
      });

      expect(screen.getByText("Synced 6m ago")).toBeInTheDocument();
    });

    it("moves on while RomM is offline, over the save status's own time", () => {
      vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
      vi.setSystemTime(new Date("2026-11-06T12:00:00Z"));
      const saveStatus = {
        rom_id: ROM_ID,
        files: [{ local_path: "/saves/game.srm", last_sync_at: "2026-11-06T11:55:00Z" }],
      } as unknown as SaveStatus;
      render(
        draw(detail({ saveSyncEnabled: true, saveSyncStatus: "none", saveSyncLabel: "Server unreachable" }), {
          saveStatus,
          isOffline: true,
        }),
      );
      expect(screen.getByText("Synced 5m ago")).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(60_000);
      });

      expect(screen.getByText("Synced 6m ago")).toBeInTheDocument();
    });

    it("keeps no timer with save sync off", () => {
      vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
      render(draw(detail({ saveSyncEnabled: false })));

      expect(vi.getTimerCount()).toBe(0);
    });

    it("stops its timer once save sync goes off", async () => {
      vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
      const { rerender } = render(draw(detail({ saveSyncEnabled: true })));
      await act(async () => {});
      expect(vi.getTimerCount()).toBe(1);

      rerender(draw(detail({ saveSyncEnabled: false })));

      expect(vi.getTimerCount()).toBe(0);
    });

    it("stops its timer once the row is gone", async () => {
      vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
      const { unmount } = render(draw(detail({ saveSyncEnabled: true })));
      await act(async () => {});
      expect(vi.getTimerCount()).toBe(1);

      unmount();

      expect(vi.getTimerCount()).toBe(0);
    });

    it("keeps the store's label where its time cannot be read", async () => {
      render(
        draw(
          detail({
            saveSyncEnabled: true,
            saveSyncStatus: "synced",
            saveSyncLabel: "3d ago",
            saveSyncCheckedAt: "not a time",
          }),
        ),
      );
      await act(async () => {});

      expect(screen.getByText("Synced 3d ago")).toBeInTheDocument();
    });

    it("keeps a label the store was told, with no time behind it", async () => {
      render(
        draw(
          detail({
            saveSyncEnabled: true,
            saveSyncStatus: "synced",
            saveSyncLabel: "Just now",
            saveSyncCheckedAt: null,
          }),
        ),
      );
      await act(async () => {});

      expect(screen.getByText("Synced just now")).toBeInTheDocument();
    });
  });

  describe("the slot setup", () => {
    const ENABLED = detail({ saveSyncEnabled: true });

    it("is said once the backend says one is needed", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: null,
        setup_needed: true,
      });
      render(draw(ENABLED));

      expect(await screen.findByText("Slot not confirmed")).toBeInTheDocument();
      expect(backend.getSaveSetupInfo).toHaveBeenCalledWith(ROM_ID);
    });

    it("does not ask RomM where none is needed", async () => {
      render(draw(ENABLED));

      await waitFor(() => expect(backend.isSaveTrackingConfigured).toHaveBeenCalledWith(ROM_ID));
      await act(async () => {});
      expect(backend.getSaveSetupInfo).not.toHaveBeenCalled();
    });

    it.each([
      ["with save sync off", detail(), false],
      ["for a game not on this device", detail({ saveSyncEnabled: true, installed: false }), false],
      ["while RomM is offline", ENABLED, true],
    ])("is not asked %s", (_name, d, isOffline) => {
      render(draw(d, { isOffline }));

      expect(backend.isSaveTrackingConfigured).not.toHaveBeenCalled();
    });

    it("is asked again on this ROM's save sync, and on one that names no ROM", async () => {
      render(draw(ENABLED));
      await waitFor(() => expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(1));

      dataChanged(ROM_ID);
      await waitFor(() => expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(2));

      dataChanged();
      await waitFor(() => expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(3));
    });

    it.each([
      ["the emulator sources changing", { type: "emulator_sources" }],
      ["a platform's core changing", { type: "core_changed", platform_slug: "psx" }],
    ])("is asked again on %s", async (_name, eventDetail) => {
      render(draw(ENABLED));
      await waitFor(() => expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(1));

      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: eventDetail }));
      });

      await waitFor(() => expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(2));
    });

    it("is not asked again on another ROM's save sync, or on another change", async () => {
      render(draw(ENABLED));
      await waitFor(() => expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(1));

      dataChanged(ROM_ID + 1);
      act(() => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "metadata", rom_id: ROM_ID } }),
        );
      });
      await act(async () => {});

      expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(1);
    });

    it("no longer listens once unmounted", async () => {
      const { unmount } = render(draw(ENABLED));
      await waitFor(() => expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(1));
      unmount();

      dataChanged(ROM_ID);

      expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(1);
    });

    const SETUP_NEEDED = { configured: false, active_slot: null, setup_needed: true };

    it("does not stand for the next version once the page has switched", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue(SETUP_NEEDED);
      const { rerender } = render(draw(ENABLED));
      await screen.findByText("Slot not confirmed");

      vi.mocked(backend.getSaveSetupInfo).mockReturnValue(new Promise<SaveSetupInfo>(() => undefined));
      rerender(draw(detail({ saveSyncEnabled: true, romId: ROM_ID + 1 })));
      await act(async () => {});

      expect(screen.getByText("Not checked")).toBeInTheDocument();
    });

    it("drops an answer that lands after the page switched version", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue(SETUP_NEEDED);
      let answerTheOldVersion: (info: SaveSetupInfo) => void = () => undefined;
      vi.mocked(backend.getSaveSetupInfo).mockReturnValueOnce(
        new Promise<SaveSetupInfo>((resolve) => {
          answerTheOldVersion = resolve;
        }),
      );
      const { rerender } = render(draw(ENABLED));
      await waitFor(() => expect(backend.getSaveSetupInfo).toHaveBeenCalledWith(ROM_ID));

      vi.mocked(backend.getSaveSetupInfo).mockResolvedValue({ ...SETUP_TO_CHOOSE, slot_confirmed: true });
      rerender(draw(detail({ saveSyncEnabled: true, romId: ROM_ID + 1 })));
      await waitFor(() => expect(backend.getSaveSetupInfo).toHaveBeenCalledWith(ROM_ID + 1));
      await act(async () => {
        answerTheOldVersion(SETUP_TO_CHOOSE);
      });

      expect(screen.queryByText("Slot not confirmed")).not.toBeInTheDocument();
    });

    it("leaves the next version's answer standing when the old version's lands", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue(SETUP_NEEDED);
      let answerTheOldVersion: (info: SaveSetupInfo) => void = () => undefined;
      vi.mocked(backend.getSaveSetupInfo).mockReturnValueOnce(
        new Promise<SaveSetupInfo>((resolve) => {
          answerTheOldVersion = resolve;
        }),
      );
      const { rerender } = render(draw(ENABLED));
      await waitFor(() => expect(backend.getSaveSetupInfo).toHaveBeenCalledWith(ROM_ID));

      vi.mocked(backend.getSaveSetupInfo).mockResolvedValue(SETUP_TO_CHOOSE);
      rerender(draw(detail({ saveSyncEnabled: true, romId: ROM_ID + 1 })));
      await screen.findByText("Slot not confirmed");
      await act(async () => {
        answerTheOldVersion({ ...SETUP_TO_CHOOSE, slot_confirmed: true });
      });

      expect(screen.getByText("Slot not confirmed")).toBeInTheDocument();
    });

    it("keeps to the newer of two reads for one ROM", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue(SETUP_NEEDED);
      let answerTheFirst: (info: SaveSetupInfo) => void = () => undefined;
      vi.mocked(backend.getSaveSetupInfo)
        .mockReturnValueOnce(
          new Promise<SaveSetupInfo>((resolve) => {
            answerTheFirst = resolve;
          }),
        )
        .mockResolvedValue({ ...SETUP_TO_CHOOSE, slot_confirmed: true });
      render(draw(ENABLED));
      await waitFor(() => expect(backend.getSaveSetupInfo).toHaveBeenCalledTimes(1));

      dataChanged(ROM_ID);
      await waitFor(() => expect(backend.getSaveSetupInfo).toHaveBeenCalledTimes(2));
      await act(async () => {
        answerTheFirst(SETUP_TO_CHOOSE);
      });

      expect(screen.getByText("Not checked")).toBeInTheDocument();
      expect(screen.queryByText("Slot not confirmed")).not.toBeInTheDocument();
    });

    it("logs a read that failed and says nothing about a slot", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockRejectedValue(new Error("gone"));
      render(draw(ENABLED));

      await waitFor(() =>
        expect(backend.debugLog).toHaveBeenCalledWith("DesktopPlayButtonBadges: save setup read failed: Error: gone"),
      );
      expect(screen.getByText("Not checked")).toBeInTheDocument();
    });

    it("holds no answer once a re-read fails, rather than the one from before", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue(SETUP_NEEDED);
      render(draw(ENABLED));
      await screen.findByText("Slot not confirmed");

      vi.mocked(backend.getSaveSetupInfo).mockRejectedValue(new Error("gone"));
      dataChanged(ROM_ID);

      expect(await screen.findByText("Not checked")).toBeInTheDocument();
    });

    it("takes nothing back for a failure that lands after a newer read answered", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue(SETUP_NEEDED);
      let failTheFirst: (reason: Error) => void = () => undefined;
      vi.mocked(backend.getSaveSetupInfo)
        .mockReturnValueOnce(
          new Promise<SaveSetupInfo>((_resolve, reject) => {
            failTheFirst = reject;
          }),
        )
        .mockResolvedValue(SETUP_TO_CHOOSE);
      render(draw(ENABLED));
      await waitFor(() => expect(backend.getSaveSetupInfo).toHaveBeenCalledTimes(1));

      dataChanged(ROM_ID);
      await screen.findByText("Slot not confirmed");
      await act(async () => {
        failTheFirst(new Error("gone"));
      });

      expect(screen.getByText("Slot not confirmed")).toBeInTheDocument();
    });

    it("takes nothing from the next version for a failure from before the switch", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue(SETUP_NEEDED);
      let failTheOldVersion: (reason: Error) => void = () => undefined;
      vi.mocked(backend.getSaveSetupInfo).mockReturnValueOnce(
        new Promise<SaveSetupInfo>((_resolve, reject) => {
          failTheOldVersion = reject;
        }),
      );
      const { rerender } = render(draw(ENABLED));
      await waitFor(() => expect(backend.getSaveSetupInfo).toHaveBeenCalledWith(ROM_ID));

      vi.mocked(backend.getSaveSetupInfo).mockResolvedValue(SETUP_TO_CHOOSE);
      rerender(draw(detail({ saveSyncEnabled: true, romId: ROM_ID + 1 })));
      await screen.findByText("Slot not confirmed");
      await act(async () => {
        failTheOldVersion(new Error("gone"));
      });

      expect(screen.getByText("Slot not confirmed")).toBeInTheDocument();
    });
  });

  describe("BIOS", () => {
    it("draws the label with the red dot where required firmware is missing", () => {
      render(draw(detail({ biosRequiredMissing: true, biosLabel: "1/3 required" })));

      expect(screen.getByText("BIOS")).toBeInTheDocument();
      expect(screen.getByText("1/3 required")).toBeInTheDocument();
      const badge = screen.getByText("BIOS").closest(".tender-desktop-bios")!;
      expect(badge.querySelector(".tender-desktop-badge-dot")).toHaveStyle({ backgroundColor: BIOS_MISSING_RED });
    });

    it("is not drawn otherwise", () => {
      render(draw(detail({ biosRequiredMissing: false, biosLabel: "OK" })));

      expect(screen.queryByText("BIOS")).not.toBeInTheDocument();
    });
  });

  describe("the row", () => {
    function row(container: HTMLElement): HTMLElement {
      return container.querySelector<HTMLElement>(".tender-desktop-badges")!;
    }

    it("stands in one order, whichever badges are drawn", async () => {
      const { container } = render(
        draw(detail({ installed: false, downloadSizeBytes: 1024, biosRequiredMissing: true, biosLabel: "Missing" })),
      );
      await act(async () => {});

      expect(Array.from(row(container).children, (cell) => cell.classList[1])).toEqual([
        "tender-desktop-space-required",
        "tender-desktop-last-played",
        "tender-desktop-playtime",
        "tender-desktop-save-sync",
        "tender-desktop-bios",
      ]);
    });

    it("wraps inside a clipped box, so what does not fit leaves from the end", () => {
      const { container } = render(draw(detail()));

      expect(row(container).style.flexWrap).toBe("wrap");
      expect(row(container).style.overflow).toBe("hidden");
      expect(row(container).style.height).toBe("46px");
      expect(row(container).style.minWidth).toMatch(/^0(px)?$/);
    });

    it("never squeezes a badge: one that does not fit leaves whole", () => {
      const { container } = render(draw(detail()));

      for (const cell of row(container).children) expect((cell as HTMLElement).style.flexShrink).toBe("0");
    });

    it("draws a plain value in Steam's colour, and Save Sync and BIOS in their own", () => {
      render(draw(detail({ biosRequiredMissing: true, biosLabel: "Missing" })));

      expect(screen.getByText("Yesterday")).toHaveStyle({ color: "rgba(255, 255, 255, 0.32)" });
      expect(screen.getByText("Disabled").parentElement).toHaveStyle({ color: "#8f98a0" });
      expect(screen.getByText("Missing").parentElement).toHaveStyle({ color: BIOS_MISSING_RED });
    });
  });

  it("takes no press: no button, link, form control or tab stop in the row, and no React handler", async () => {
    const { container } = render(
      draw(
        detail({
          installed: false,
          downloadSizeBytes: 1024,
          saveSyncEnabled: true,
          biosRequiredMissing: true,
          biosLabel: "Missing",
        }),
      ),
    );
    await act(async () => {});

    expect(container.querySelectorAll(".tender-desktop-badge-item")).toHaveLength(5);
    expect(
      container.querySelector("[tabindex],[role='button'],button,a[href],input,select,textarea,summary"),
    ).toBeNull();
    for (const element of container.querySelectorAll("*")) {
      const propsKey = Object.keys(element).find((key) => key.startsWith("__reactProps"));
      expect(propsKey).toBeDefined();
      const props = (element as unknown as Record<string, object>)[propsKey!]!;
      expect(Object.keys(props).filter((name) => /^on[A-Z]/.test(name))).toEqual([]);
    }
  });
});
