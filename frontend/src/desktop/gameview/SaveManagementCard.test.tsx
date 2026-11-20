import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SaveManagementCard } from "./SaveManagementCard";
import * as backend from "../../api/backend";
import {
  getRommConnectionState,
  reportServerReachable,
  setRommConnectionState,
  setServerRetryProgress,
} from "../../utils/connectionState";
import { capturePruneLeaseAdmission, isPruneLeaseAdmissionCurrent } from "../../utils/pruneLease";
import { setStrandedAnswer } from "../../test-utils/stranded-panel";
import * as toast from "../../utils/toast";
import * as gameDetailStore from "../../utils/gameDetailStore";
import {
  CONTENT_DIR_HINT,
  NOT_DOWNLOADED_NO_SAVE,
  NO_SAVE_ON_DEVICE,
  SAVE_SYNC_OFF,
} from "../../utils/saveSyncStatement";
import { LAST_KNOWN_SLOTS_STALE_NOTE, LEGACY_SLOT_NOTE } from "../../utils/saveSlotWording";
import type { GameDetailState } from "../../utils/gameDetailStore";
import type {
  RollbackStatus,
  SaveSlotSummary,
  SaveStatus,
  SaveVersionEntry,
  SlotSaveFile,
  SyncConflict,
} from "../../types";

vi.mock("../../utils/gameDetailStore", () => ({
  refreshSaveStatus: vi.fn().mockResolvedValue(null),
  noteSaveSyncDisplay: vi.fn(),
}));

vi.mock("../../utils/toast", () => ({
  showToast: vi.fn(),
}));

const flushMacrotask = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

describe("SaveManagementCard", () => {
  const mockSaveStatus: SaveStatus = {
    rom_id: 42,
    device_id: "steamdeck",
    last_sync_check_at: "2026-06-15T21:04:00Z",
    conflicts: [],
    savefiles_in_content_dir: false,
    save_sync_display: {
      status: "synced",
      label: null,
      last_sync_check_at: "2026-06-15T21:04:00Z",
    },
    server_query_failed: false,
    server_query_reason: null,
    server_has_saves: true,
    multi_file: false,
    component_files: ["Mario Golf - Advance Tour.srm"],
    rollback_supported: true,
    playtime: {
      total_seconds: 3600,
      session_count: 5,
      last_session_start: null,
      last_session_duration_sec: null,
      last_played: "2026-06-15T21:04:00Z",
    },
    files: [
      {
        filename: "Mario Golf - Advance Tour.srm",
        local_path: "~/retrodeck/saves/gba/Mario Golf - Advance Tour.srm",
        local_size: 65536,
        local_hash: null,
        local_mtime: null,
        server_save_id: 4812,
        server_file_name: "Mario Golf - Advance Tour.srm",
        server_emulator: "mgba",
        server_size: 65536,
        server_updated_at: "2026-06-15T21:04:00Z",
        last_sync_at: "2026-06-15T21:04:00Z",
        uploaded_by_us: true,
        status: "synced",
        is_current: true,
        device_syncs: [
          {
            device_id: "steamdeck",
            device_name: "steamdeck",
            is_current: true,
            last_synced_at: "2026-06-15T21:04:00Z",
          },
        ],
      },
    ],
  };

  const mockSlots: SaveSlotSummary[] = [
    { slot: "default", source: "server", count: 1, latest_updated_at: "2026-06-15T21:04:00Z" },
    { slot: "speedrun", source: "server", count: 2, latest_updated_at: "2026-06-10T12:00:00Z" },
    { slot: "", source: "server", count: 1, latest_updated_at: "2026-06-01T10:00:00Z" },
  ];

  const baseDetail: GameDetailState = {
    romId: 42,
    romName: "Mario Golf",
    platformSlug: "gba",
    platformFolder: "gba",
    platformName: "Game Boy Advance",
    installed: true,
    fileMissingAt: null,
    downloadSizeBytes: null,
    saveSyncEnabled: true,
    saveStatus: mockSaveStatus,
    saveSyncStatus: "synced",
    saveSyncLabel: "Synced",
    saveSyncCheckedAt: null,
    raId: null,
    achievementEarned: 0,
    achievementTotal: 0,
    biosNeeded: false,
    biosLabel: "",
    biosRequiredMissing: false,
    activeCoreLabel: "mgba",
    activeCoreIsDefault: true,
    emulators: [],
    emulatorDataAvailable: true,
    emulatorDataReason: null,
    emulatorSource: null,
    platformCoreLabel: "mgba",
    hasGameOverride: false,
    emulatorStart: null,
    gameLaunch: null,
    startCheck: { state: "checking" },
    vanishedPick: null,
    emulatorPlatformSystem: null,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    setRommConnectionState("connected", "test setup");
    setServerRetryProgress(null);

    vi.mocked(backend.getSaveSyncAvailability).mockResolvedValue({
      available: true,
      installed: true,
      established: true,
      emulator_label: "mGBA",
    });
    vi.mocked(backend.getLocalSaveFiles).mockResolvedValue({
      available: true,
      installed: true,
      files: [],
    });
    vi.mocked(backend.getSaveSlots).mockResolvedValue({
      success: true,
      slots: mockSlots,
      active_slot: "default",
    });
    vi.mocked(backend.getSlotSaves).mockResolvedValue({
      success: true,
      slot: "speedrun",
      saves: [],
    });
    vi.mocked(backend.getVersionList).mockResolvedValue({
      multi_version: false,
      bound_vanished: false,
      versions: [],
    });
    vi.mocked(backend.checkLocalDrift).mockResolvedValue({
      drifted: false,
      only_here: false,
      stranding: "none",
      rom_id: 42,
    });
    vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
      configured: true,
      active_slot: "default",
      setup_needed: false,
    });
    vi.mocked(backend.confirmSlotChoice).mockResolvedValue({
      success: true,
      needs_conflict_resolution: false,
      message: "Slot confirmed",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe("gates before mounting content", () => {
    it("renders nothing and issues no backend reads before detail.romId is known", async () => {
      render(<SaveManagementCard appId={100} detail={{ ...baseDetail, romId: null }} />);
      await flushMacrotask();

      expect(screen.queryByTestId("desktop-saves-card")).not.toBeInTheDocument();
      expect(backend.getSaveSlots).not.toHaveBeenCalled();
      expect(backend.isSaveTrackingConfigured).not.toHaveBeenCalled();
      expect(backend.getSaveSyncAvailability).not.toHaveBeenCalled();
      expect(backend.getLocalSaveFiles).not.toHaveBeenCalled();
      expect(backend.getVersionList).not.toHaveBeenCalled();
    });

    it("renders nothing and issues no backend reads while the panel is stranded", async () => {
      setStrandedAnswer("reloads");
      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await flushMacrotask();

      expect(screen.queryByTestId("desktop-saves-card")).not.toBeInTheDocument();
      expect(backend.getSaveSlots).not.toHaveBeenCalled();
      expect(backend.isSaveTrackingConfigured).not.toHaveBeenCalled();
      expect(backend.getSaveSyncAvailability).not.toHaveBeenCalled();
      expect(backend.getLocalSaveFiles).not.toHaveBeenCalled();
      expect(backend.getVersionList).not.toHaveBeenCalled();
    });

    it("tears down subtree state when detail.romId changes (version switch 42 -> 99)", async () => {
      const { rerender } = render(<SaveManagementCard appId={100} detail={baseDetail} />);
      const speedrunPanel = await screen.findByTestId("desktop-slot-panel-speedrun");
      fireEvent.click(within(speedrunPanel).getByRole("button", { name: /speedrun/ }));
      await waitFor(() => expect(backend.getSlotSaves).toHaveBeenCalledWith(42, "speedrun"));
      expect(within(speedrunPanel).getByText("No saves in this slot")).toBeInTheDocument();

      // Switch to version 99 on the same GameView mount: key={detail.romId} tears down
      // the expanded inactive slot from ROM 42 and fetches fresh slots for ROM 99.
      rerender(<SaveManagementCard appId={100} detail={{ ...baseDetail, romId: 99 }} />);

      await waitFor(() => {
        expect(backend.getSaveSlots).toHaveBeenCalledWith(99);
        expect(backend.isSaveTrackingConfigured).toHaveBeenCalledWith(99);
        expect(backend.getSaveSyncAvailability).toHaveBeenCalledWith(99);
      });
      const switchedSpeedrun = await screen.findByTestId("desktop-slot-panel-speedrun");
      expect(within(switchedSpeedrun).queryByText("No saves in this slot")).not.toBeInTheDocument();
    });

    it("holds prune lease owner desktop-saves-card:<appId> while mounted and releases it on unmount", () => {
      const { unmount } = render(<SaveManagementCard appId={100} detail={baseDetail} />);

      expect(isPruneLeaseAdmissionCurrent(capturePruneLeaseAdmission("desktop-saves-card:100"))).toBe(true);
      expect(isPruneLeaseAdmissionCurrent(capturePruneLeaseAdmission("desktop-saves-card:999"))).toBe(false);

      unmount();

      expect(isPruneLeaseAdmissionCurrent(capturePruneLeaseAdmission("desktop-saves-card:100"))).toBe(false);
    });
  });

  describe("header, slots accordion, and active file rows", () => {
    it("renders the Saves header with slots count, Sync now, Delete Local Saves, and + New Slot", async () => {
      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      expect(screen.getByText("Saves")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Sync now" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Delete Local Saves" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "+ New Slot" })).toBeInTheDocument();

      await waitFor(() => {
        expect(screen.getByText("3 slots · save sync on")).toBeInTheDocument();
      });
    });

    it("renders slots accordion with ACTIVE badge, SERVER badge, Manual archive note, and pluralized counts", async () => {
      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      await waitFor(() => {
        expect(screen.getByText("default")).toBeInTheDocument();
        expect(screen.getByText("ACTIVE")).toBeInTheDocument();
        expect(screen.getByText("speedrun")).toBeInTheDocument();
        expect(screen.getByText("Manual archive")).toBeInTheDocument();
        expect(screen.getByText("2 saves")).toBeInTheDocument();
        expect(screen.getByText(LEGACY_SLOT_NOTE)).toBeInTheDocument();
      });
    });

    it("renders active slot files details, metadata, green checkmark, and local path", async () => {
      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      await waitFor(() => {
        expect(screen.getAllByText("Mario Golf - Advance Tour.srm")).toHaveLength(2);
        expect(screen.getByText("64.0 KB")).toBeInTheDocument();
        expect(screen.getByText("Synced")).toBeInTheDocument();
        expect(screen.getByText("Last synced:")).toBeInTheDocument();
        expect(screen.getByText("Last updated:")).toBeInTheDocument();
        expect(screen.getByText("Server save:")).toBeInTheDocument();
        expect(screen.getByText("#4812 · mgba (64.0 KB)")).toBeInTheDocument();
        expect(screen.getByText("Local path:")).toBeInTheDocument();
        expect(screen.getByText("~/retrodeck/saves/gba/Mario Golf - Advance Tour.srm")).toBeInTheDocument();
        const checkEl = screen.getByText("✓");
        expect(checkEl).toBeInTheDocument();
        expect(checkEl.parentElement?.textContent).not.toMatch(/✓.*✓/);
      });
      expect(screen.queryByText("Checked:")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Sync now" }).querySelector("svg")).toBeNull();
      expect(screen.getByRole("button", { name: "Delete Local Saves" }).querySelector("svg")).toBeNull();
    });

    it("shows Checked: row and suppresses green checkmark when file has pending upload work (#1334)", async () => {
      const pendingDetail: GameDetailState = {
        ...baseDetail,
        saveStatus: {
          ...mockSaveStatus,
          files: [
            {
              ...mockSaveStatus.files[0]!,
              status: "upload",
            },
          ],
        },
      };

      render(<SaveManagementCard appId={100} detail={pendingDetail} />);

      await waitFor(() => {
        expect(screen.getByText("Checked:")).toBeInTheDocument();
        expect(screen.getAllByText("Local changes").length).toBeGreaterThanOrEqual(1);
        expect(screen.queryByText("✓")).not.toBeInTheDocument();
      });
    });

    it("renders multi-file note once and omits Copy/Versions when saveStatus.multi_file is true (#908)", async () => {
      const multiDetail: GameDetailState = {
        ...baseDetail,
        saveStatus: {
          ...mockSaveStatus,
          multi_file: true,
          component_files: ["game.bkr", "game.bcr"],
        },
      };

      render(<SaveManagementCard appId={100} detail={multiDetail} />);

      await waitFor(() => {
        expect(screen.getByText("Files in this save (2)")).toBeInTheDocument();
        expect(screen.getByText("game.bkr")).toBeInTheDocument();
        expect(screen.getByText("game.bcr")).toBeInTheDocument();
        expect(
          screen.getByText("This save spans 2 files. Per-version rollback isn't available for multi-file saves yet."),
        ).toBeInTheDocument();
      });
      expect(screen.queryByText("Previous Versions")).not.toBeInTheDocument();
      expect(screen.queryByText("Copy to slot...")).not.toBeInTheDocument();
    });

    it("shows NO_SAVE_ON_DEVICE in empty active slot only when server_query_failed is true (#2149 D10)", async () => {
      vi.mocked(backend.getSaveSlots).mockResolvedValue({
        success: true,
        slots: [{ slot: "default", source: "local", count: 0, latest_updated_at: null }],
        active_slot: "default",
      });

      const emptyClean: GameDetailState = {
        ...baseDetail,
        saveStatus: { ...mockSaveStatus, files: [], server_query_failed: false },
      };
      const { rerender } = render(<SaveManagementCard appId={100} detail={emptyClean} />);

      await waitFor(() => {
        expect(screen.getByText("default")).toBeInTheDocument();
        expect(screen.getByText(/No save yet — mGBA's saves for this game sync with RomM/)).toBeInTheDocument();
      });
      expect(screen.queryByText(NO_SAVE_ON_DEVICE)).not.toBeInTheDocument();

      rerender(
        <SaveManagementCard
          appId={100}
          detail={{ ...emptyClean, saveStatus: { ...mockSaveStatus, files: [], server_query_failed: true } }}
        />,
      );

      expect(screen.getByText(NO_SAVE_ON_DEVICE)).toBeInTheDocument();
    });
  });

  describe("save actions (sync, delete local saves, slot create/activate/delete, copy)", () => {
    it("runs manual sync on 'Sync now' click and updates display", async () => {
      let resolveSync!: (v: Awaited<ReturnType<typeof backend.syncRomSaves>>) => void;
      vi.mocked(backend.syncRomSaves).mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveSync = resolve;
          }),
      );

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await waitFor(() => expect(screen.getByText("default")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "Sync now" }));

      expect(screen.getByRole("button", { name: "Syncing…" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Delete Local Saves" })).toBeDisabled();

      await act(async () => {
        resolveSync({
          success: true,
          message: "ok",
          synced: 1,
          uploaded: 1,
          downloaded: 0,
        });
      });

      await waitFor(() => {
        expect(backend.syncRomSaves).toHaveBeenCalledWith(42);
        expect(gameDetailStore.noteSaveSyncDisplay).toHaveBeenCalledWith(
          100,
          42,
          expect.objectContaining({ status: "synced" }),
        );
        expect(toast.showToast).toHaveBeenCalledWith("Saves uploaded to RomM");
        expect(screen.getByRole("button", { name: "Sync now" })).not.toBeDisabled();
      });
    });

    it("confirms and executes Delete Local Saves when installed", async () => {
      let resolveDelete!: (v: Awaited<ReturnType<typeof backend.deleteLocalSaves>>) => void;
      vi.mocked(backend.deleteLocalSaves).mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveDelete = resolve;
          }),
      );

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await waitFor(() => expect(screen.getByText("default")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "Delete Local Saves" }));
      expect(await screen.findByRole("dialog", { name: "Delete Local Saves" })).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Delete" }));

      expect(await screen.findByRole("button", { name: "Deleting…" })).toBeDisabled();

      await act(async () => {
        resolveDelete({
          success: true,
          deleted_count: 2,
          message: "Deleted 2 local saves",
        });
      });

      await waitFor(() => {
        expect(backend.deleteLocalSaves).toHaveBeenCalledWith(42);
        expect(gameDetailStore.noteSaveSyncDisplay).toHaveBeenCalledWith(100, 42, {
          status: "none",
          label: "No saves",
          last_sync_check_at: null,
        });
        expect(toast.showToast).toHaveBeenCalledWith("Deleted 2 local saves");
        expect(screen.getByRole("button", { name: "Delete Local Saves" })).not.toBeDisabled();
      });
    });

    it("lazy-loads inactive slot files on expand and reloads on save_sync change", async () => {
      const inactiveSaves: SlotSaveFile[] = [
        {
          id: 777,
          filename: "speedrun.srm",
          emulator: "mgba",
          size: 65536,
          updated_at: "2026-06-10T12:00:00Z",
        },
      ];
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: true,
        slot: "speedrun",
        saves: inactiveSaves,
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      const speedrunBtn = await screen.findByRole("button", { name: /speedrun/ });
      fireEvent.click(speedrunBtn);

      await waitFor(() => {
        expect(backend.getSlotSaves).toHaveBeenCalledWith(42, "speedrun");
        expect(screen.getByText("speedrun.srm")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Activate Slot" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Delete Slot" })).toBeInTheDocument();
      });

      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      await waitFor(() => expect(backend.getSlotSaves).toHaveBeenCalledTimes(2));
    });

    it("shows Switching... while activating an inactive slot and switches active slot", async () => {
      let resolveSwitch!: (value: backend.SwitchSlotResult) => void;
      vi.mocked(backend.switchSlot).mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveSwitch = resolve;
          }),
      );

      const { container } = render(<SaveManagementCard appId={100} detail={baseDetail} />);

      fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));
      fireEvent.click(await screen.findByRole("button", { name: "Activate Slot" }));

      expect(screen.getByRole("button", { name: "Switching..." })).toBeDisabled();

      vi.mocked(backend.getSaveSlots).mockResolvedValue({
        success: true,
        slots: mockSlots,
        active_slot: "speedrun",
      });
      await act(async () => {
        resolveSwitch({ success: true, save_status: mockSaveStatus });
      });

      await waitFor(() => {
        expect(backend.switchSlot).toHaveBeenCalledWith(42, "speedrun");
        expect(toast.showToast).toHaveBeenCalledWith("Switched to slot 'speedrun'");
        expect(screen.queryByRole("button", { name: "Activate Slot" })).not.toBeInTheDocument();
        expect(container.querySelector('[data-testid^="desktop-slot-panel-"]')?.getAttribute("data-testid")).toBe(
          "desktop-slot-panel-speedrun",
        );
      });
    });

    it("surfaces savefiles_in_content_dir error on Activate Slot failure", async () => {
      vi.mocked(backend.switchSlot).mockResolvedValue({
        success: false,
        reason: "savefiles_in_content_dir",
        message: "",
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));
      fireEvent.click(await screen.findByRole("button", { name: "Activate Slot" }));

      expect(
        await screen.findByText("Can't switch — this game's saves are written beside the game file"),
      ).toBeInTheDocument();
    });

    it("shows Deleting... while confirming slot deletion and deletes inactive slot", async () => {
      vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue({
        success: true,
        slot: "speedrun",
        source: "server",
        is_active: false,
        server_save_count: 2,
        server_save_ids: [1, 2],
        local_file_count: 1,
        local_filenames: ["speedrun.srm"],
      });
      vi.mocked(backend.deleteSlot).mockResolvedValue({
        success: true,
        deleted_server_saves: 2,
        cleaned_files: 1,
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));
      fireEvent.click(await screen.findByRole("button", { name: "Delete Slot" }));

      expect(await screen.findByRole("dialog", { name: "Delete Slot" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Deleting..." })).toBeDisabled();

      const delEvents: unknown[] = [];
      const onDelEvt = (e: Event) => delEvents.push((e as CustomEvent).detail);
      globalThis.addEventListener("romm_data_changed", onDelEvt);
      try {
        fireEvent.click(screen.getByRole("button", { name: "Delete" }));

        await waitFor(() => {
          expect(backend.deleteSlot).toHaveBeenCalledWith(42, "speedrun");
          expect(toast.showToast).toHaveBeenCalledWith("Slot 'speedrun' deleted");
          expect(delEvents).toContainEqual({ type: "save_sync", rom_id: 42 });
        });
      } finally {
        globalThis.removeEventListener("romm_data_changed", onDelEvt);
      }
    });

    it("creates a new slot via '+ New Slot' dialog and keeps dialog open on switch error", async () => {
      vi.mocked(backend.switchSlot)
        .mockResolvedValueOnce({
          success: false,
          reason: "pending_uploads",
          message: "",
        })
        .mockResolvedValueOnce({
          success: true,
          save_status: mockSaveStatus,
        })
        .mockResolvedValueOnce({
          success: false,
          reason: "switch_stopped",
          message: "partial create stop",
          set_aside: ["foo.srm"],
        });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await waitFor(() => expect(screen.getByText("default")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "+ New Slot" }));
      const dialog = screen.getByRole("dialog", { name: "New Save Slot" });
      fireEvent.change(within(dialog).getByPlaceholderText("Slot Name (e.g. speedrun, casual)"), {
        target: { value: "casual" },
      });
      fireEvent.click(within(dialog).getByRole("button", { name: "Create Slot" }));

      expect(
        await within(dialog).findByText("Sync your saves first — local changes haven't been uploaded"),
      ).toBeInTheDocument();

      fireEvent.click(within(dialog).getByRole("button", { name: "Create Slot" }));

      await waitFor(() => {
        expect(backend.switchSlot).toHaveBeenCalledWith(42, "casual");
        expect(toast.showToast).toHaveBeenCalledWith("Switched to slot 'casual'");
        expect(screen.queryByRole("dialog", { name: "New Save Slot" })).not.toBeInTheDocument();
      });

      // Partial outcome closes dialog and toasts stop message
      fireEvent.click(screen.getByRole("button", { name: "+ New Slot" }));
      const partialDialog = await screen.findByRole("dialog", { name: "New Save Slot" });
      fireEvent.change(within(partialDialog).getByPlaceholderText("Slot Name (e.g. speedrun, casual)"), {
        target: { value: "partial_slot" },
      });
      fireEvent.click(within(partialDialog).getByRole("button", { name: "Create Slot" }));
      await waitFor(() => {
        expect(screen.queryByRole("dialog", { name: "New Save Slot" })).not.toBeInTheDocument();
        expect(toast.showToast).toHaveBeenCalledWith(expect.stringContaining("The slot wasn't created:"));
        expect(toast.showToast).toHaveBeenCalledWith(expect.stringContaining("Already moved there: foo.srm"));
      });
    });

    it("copies save via 'Copy to slot...' modal, handles upload_failed refresh and conflict_blocked dialog", async () => {
      const mockConflict: SyncConflict = {
        type: "sync_conflict",
        rom_id: 42,
        filename: "Mario Golf - Advance Tour.srm",
        server_save_id: 999,
        server_updated_at: "2026-06-15T12:30:00Z",
        server_size: 65536,
        local_path: "~/retrodeck/saves/gba/Mario Golf - Advance Tour.srm",
        local_hash: "hash123",
        local_mtime: "2026-06-15T12:00:00Z",
        local_size: 65536,
        created_at: "2026-06-15T12:30:00Z",
      };
      vi.mocked(backend.copySaveToSlot)
        .mockResolvedValueOnce({ status: "upload_failed", message: "timed out" })
        .mockResolvedValueOnce({ status: "conflict_blocked", conflicts: [mockConflict] });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      fireEvent.click(await screen.findByRole("button", { name: "Copy to slot..." }));
      const dialog = screen.getByRole("dialog", { name: "Copy save to slot" });
      fireEvent.click(within(dialog).getByRole("button", { name: "speedrun" }));

      await waitFor(() => {
        expect(backend.copySaveToSlot).toHaveBeenCalledWith(42, 4812, "speedrun");
        expect(gameDetailStore.refreshSaveStatus).toHaveBeenCalledWith(100);
        expect(toast.showToast).toHaveBeenCalledWith(expect.stringContaining("not uploaded to RomM yet"));
      });

      fireEvent.click(screen.getByRole("button", { name: "Copy to slot..." }));
      fireEvent.click(screen.getByRole("button", { name: "speedrun" }));

      await waitFor(() => {
        expect(screen.getByText(/Save conflict for/i)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Keep Local" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Use Server" })).toBeInTheDocument();
      });
    });
  });

  describe("Previous Versions subsection", () => {
    const mockVersions: SaveVersionEntry[] = [
      {
        id: 4809,
        emulator: "mgba",
        file_name: "Mario Golf - Advance Tour.srm",
        file_size_bytes: 65536,
        updated_at: "2026-06-12T10:00:00Z",
        uploaded_by_us: true,
        device_syncs: [
          {
            device_id: "steamdeck",
            device_name: "steamdeck",
            is_current: true,
            last_synced_at: "2026-06-12T10:00:00Z",
          },
        ],
      },
      {
        id: 4805,
        emulator: "mgba",
        file_name: "older.srm",
        file_size_bytes: 32768,
        updated_at: "2026-06-11T10:00:00Z",
        uploaded_by_us: false,
        device_syncs: [],
      },
    ];

    it("surfaces distinct load errors (#1570) and retries on click", async () => {
      vi.mocked(backend.savesListFileVersions)
        .mockResolvedValueOnce({ status: "not_found", message: "404" })
        .mockResolvedValueOnce({ status: "ok", versions: mockVersions });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      fireEvent.click(await screen.findByRole("button", { name: "Previous Versions" }));

      expect(await screen.findByText("RomM couldn't find this game's save data.")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));

      await waitFor(() => {
        expect(screen.getByText("#4809 · mgba")).toBeInTheDocument();
        expect(screen.getByText("Previous Versions (2)")).toBeInTheDocument();
      });
    });

    it("disables all Restore and Copy buttons while a restore is in flight, restores, and collapses", async () => {
      let resolveRestore!: (v: RollbackStatus) => void;
      vi.mocked(backend.savesListFileVersions).mockResolvedValue({ status: "ok", versions: mockVersions });
      vi.mocked(backend.savesRollbackToVersion).mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveRestore = resolve;
          }),
      );

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      fireEvent.click(await screen.findByRole("button", { name: "Previous Versions" }));

      const restoreButtons = await screen.findAllByRole("button", { name: "Restore" });
      expect(restoreButtons).toHaveLength(2);
      fireEvent.click(restoreButtons[0]!);

      expect(screen.getByRole("button", { name: "Restoring..." })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Restore" })).toBeDisabled();
      // Active row + 2 version rows = 3 "Copy to slot..."; the 2 version-row copies disable while restoring.
      const copyDuringRestore = screen.getAllByRole("button", { name: "Copy to slot..." });
      expect(copyDuringRestore[1]).toBeDisabled();
      expect(copyDuringRestore[2]).toBeDisabled();

      await act(async () => {
        resolveRestore({ status: "ok" });
      });

      await waitFor(() => {
        expect(backend.savesRollbackToVersion).toHaveBeenCalledWith(42, "default", 4809);
        expect(toast.showToast).toHaveBeenCalledWith(expect.stringContaining("Save restored"));
        expect(screen.getByRole("button", { name: "Previous Versions" })).toBeInTheDocument();
        expect(backend.savesListFileVersions).toHaveBeenCalledTimes(1);
      });
    });

    it("offers Copy to slot... on version rows, notifies put_failed, and prompts on conflict_blocked", async () => {
      const mockConflict: SyncConflict = {
        type: "sync_conflict",
        rom_id: 42,
        filename: "Mario Golf - Advance Tour.srm",
        server_save_id: 999,
        server_updated_at: "2026-06-15T12:30:00Z",
        server_size: 65536,
        local_path: "~/retrodeck/saves/gba/Mario Golf - Advance Tour.srm",
        local_hash: "hash123",
        local_mtime: "2026-06-15T12:00:00Z",
        local_size: 65536,
        created_at: "2026-06-15T12:30:00Z",
      };
      vi.mocked(backend.savesListFileVersions).mockResolvedValue({ status: "ok", versions: mockVersions });
      vi.mocked(backend.savesRollbackToVersion)
        .mockResolvedValueOnce({ status: "conflict_blocked", conflicts: [mockConflict] })
        .mockResolvedValueOnce({ status: "put_failed", message: "Server update failed" });
      vi.mocked(backend.copySaveToSlot).mockResolvedValue({ status: "ok" });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      fireEvent.click(await screen.findByRole("button", { name: "Previous Versions" }));
      await screen.findByText("#4809 · mgba");

      // Copy from version row
      const copyButtons = screen.getAllByRole("button", { name: "Copy to slot..." });
      fireEvent.click(copyButtons[1]!);
      const copyDialog = screen.getByRole("dialog", { name: "Copy save to slot" });
      expect(within(copyDialog).queryByRole("button", { name: "default" })).not.toBeInTheDocument();
      fireEvent.click(within(copyDialog).getByRole("button", { name: "speedrun" }));
      await waitFor(() => expect(backend.copySaveToSlot).toHaveBeenCalledWith(42, 4809, "speedrun"));

      // Re-expanded list refreshed after copy; test conflict_blocked + put_failed
      const restoreButtons = await screen.findAllByRole("button", { name: "Restore" });
      fireEvent.click(restoreButtons[0]!);
      await waitFor(() => expect(screen.getByText(/Save conflict for/i)).toBeInTheDocument());
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      const refreshedRestoreButtons = await screen.findAllByRole("button", { name: "Restore" });
      await waitFor(() => expect(refreshedRestoreButtons[0]!).not.toBeDisabled());
      fireEvent.click(refreshedRestoreButtons[0]!);
      await waitFor(() => {
        expect(toast.showToast).toHaveBeenCalledWith(
          "Restored locally, but the server didn't update. Other devices will see the previous version until you retry.",
        );
      });
    });

    it("shows offline notice in expanded Previous Versions when RomM goes offline and auto-fetches on reconnect", async () => {
      vi.mocked(backend.savesListFileVersions).mockResolvedValue({ status: "ok", versions: mockVersions });
      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await flushMacrotask();
      const toggle = screen.getByRole("button", { name: "Previous Versions" });

      act(() => {
        reportServerReachable(false);
      });

      fireEvent.click(toggle);
      expect(screen.getByText("Offline — versions unavailable")).toBeInTheDocument();
      expect(backend.savesListFileVersions).not.toHaveBeenCalled();

      act(() => {
        reportServerReachable(true);
      });
      await waitFor(() => {
        expect(backend.savesListFileVersions).toHaveBeenCalledTimes(1);
        expect(screen.getByText("#4809 · mgba")).toBeInTheDocument();
      });
    });
  });

  describe("save sync off, refusal, and not-downloaded states", () => {
    it("reads local save files when saveSyncEnabled is false, shows Delete Local Saves when installed, and hides slot controls", async () => {
      vi.mocked(backend.getLocalSaveFiles).mockResolvedValue({
        available: true,
        installed: true,
        files: [
          {
            filename: "Mario Golf.srm",
            directory: "~/retrodeck/saves/gba",
            size: 32768,
            modified_at: "2026-06-15T12:00:00Z",
          },
        ],
      });

      render(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: false }} />);

      await waitFor(() => {
        expect(backend.getLocalSaveFiles).toHaveBeenCalledWith(42);
        expect(screen.getByText(SAVE_SYNC_OFF)).toBeInTheDocument();
        expect(screen.getByText("Mario Golf.srm")).toBeInTheDocument();
        expect(screen.getByText("Folder: ~/retrodeck/saves/gba")).toBeInTheDocument();
      });

      await flushMacrotask();
      expect(screen.getByRole("button", { name: "Delete Local Saves" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Sync now" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "+ New Slot" })).not.toBeInTheDocument();
      expect(screen.queryByTestId("desktop-unslotted-files")).not.toBeInTheDocument();
      expect(backend.getSaveSlots).not.toHaveBeenCalled();
    });

    it("shows refusal sentence and hint when saveSyncEnabled is false and saves sit beside the game file", async () => {
      vi.mocked(backend.getLocalSaveFiles).mockResolvedValue({
        available: false,
        installed: true,
        files: [],
        reason: "savefiles_in_content_dir",
        message: "Save sync is off for this game: saves are written beside the game file.",
      });

      render(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: false }} />);

      await waitFor(() => {
        expect(screen.getByText(SAVE_SYNC_OFF)).toBeInTheDocument();
        expect(
          screen.getByText("Save sync is off for this game: saves are written beside the game file."),
        ).toBeInTheDocument();
        expect(screen.getByText(CONTENT_DIR_HINT)).toBeInTheDocument();
      });
      expect(screen.queryByRole("button", { name: "Delete Local Saves" })).not.toBeInTheDocument();
    });

    it("shows not-downloaded line under save sync off when game is not downloaded", async () => {
      vi.mocked(backend.getLocalSaveFiles).mockResolvedValue({
        available: true,
        installed: false,
        files: [],
      });

      render(<SaveManagementCard appId={100} detail={{ ...baseDetail, installed: false, saveSyncEnabled: false }} />);

      await waitFor(() => {
        expect(screen.getByText(NOT_DOWNLOADED_NO_SAVE)).toBeInTheDocument();
      });
      expect(screen.queryByRole("button", { name: "Delete Local Saves" })).not.toBeInTheDocument();
    });

    it("shows SaveSyncRefusal and hides header actions and slots when downloaded game save sync is refused", async () => {
      vi.mocked(backend.getSaveSyncAvailability).mockResolvedValue({
        available: false,
        installed: true,
        established: true,
        emulator_label: "PCSX2",
        reason: "savefiles_in_content_dir",
        message: "Save sync is unavailable because saves are in the content folder.",
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      await waitFor(() => {
        expect(
          screen.getByText("Save sync is unavailable because saves are in the content folder."),
        ).toBeInTheDocument();
        expect(screen.getByText(CONTENT_DIR_HINT)).toBeInTheDocument();
      });
      expect(screen.queryByRole("button", { name: "Sync now" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Delete Local Saves" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "+ New Slot" })).not.toBeInTheDocument();
      expect(screen.queryByText("default")).not.toBeInTheDocument();
    });

    it("shows notDownloadedStatement above slots when save sync is on for an uninstalled game", async () => {
      vi.mocked(backend.getSaveSyncAvailability).mockResolvedValue({
        available: true,
        installed: false,
        established: true,
        emulator_label: "mGBA",
      });

      render(<SaveManagementCard appId={100} detail={{ ...baseDetail, installed: false }} />);

      await waitFor(() => {
        expect(
          screen.getByText("Not downloaded. Once it is, mGBA's saves for this game sync with RomM."),
        ).toBeInTheDocument();
        expect(screen.getByText("default")).toBeInTheDocument();
      });
      expect(screen.queryByRole("button", { name: "Delete Local Saves" })).not.toBeInTheDocument();
    });
  });

  describe("connecting indicator (#1345), offline last-known (#1755), and unslotted files (#1747)", () => {
    it("renders ConnectingIndicator with live retry attempt progress while loading and clears on settlement", async () => {
      let resolveSlots!: (v: backend.SaveSlotsResult) => void;
      vi.mocked(backend.getSaveSlots).mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveSlots = resolve;
          }),
      );

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      expect(await screen.findByText("Connecting to RomM…")).toBeInTheDocument();

      act(() => {
        setServerRetryProgress({ attempt: 2, maxAttempts: 3 });
      });
      expect(screen.getByText("Connecting to RomM… (attempt 2/3)")).toBeInTheDocument();

      await act(async () => {
        resolveSlots({ success: true, slots: mockSlots, active_slot: "default" });
      });

      expect(screen.queryByTestId("desktop-saves-connecting")).not.toBeInTheDocument();
      expect(screen.getByText("default")).toBeInTheDocument();
    });

    it("uses known-offline fast path with stale last-known snapshot (#1755), unslotted files (#1747), and reloads on reconnect", async () => {
      setRommConnectionState("offline", "test offline");
      vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
        success: false,
        reason: "server_unreachable",
        message: "Server unreachable",
        last_known: {
          active_slot: "default",
          slots: [
            { slot: "default", source: "server", count: 1, latest_updated_at: "2026-06-15T21:04:00Z" },
            { slot: "speedrun", source: "server", count: 2, latest_updated_at: "2026-06-10T12:00:00Z" },
          ],
        },
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      expect(screen.queryByTestId("desktop-saves-connecting")).not.toBeInTheDocument();
      expect(screen.getByText(/RomM is offline — slot switching is disabled/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Sync now" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "+ New Slot" })).toBeDisabled();

      await waitFor(() => {
        expect(screen.getByText(LAST_KNOWN_SLOTS_STALE_NOTE)).toBeInTheDocument();
        expect(screen.getByTestId("desktop-last-known-slot-default")).toBeInTheDocument();
        expect(screen.getByTestId("desktop-unslotted-files")).toBeInTheDocument();
      });

      // Reconnect automatically loads live slots
      vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
        success: true,
        slots: mockSlots,
        active_slot: "default",
      });
      await act(async () => {
        reportServerReachable(true);
      });

      await waitFor(() => {
        expect(screen.queryByTestId("desktop-last-known-slots")).not.toBeInTheDocument();
        expect(screen.getByTestId("desktop-slot-panel-default")).toBeInTheDocument();
      });
    });

    it("renders unslotted local files and omits legacy panel without legacy banner when active_slot is null", async () => {
      vi.mocked(backend.getSaveSlots).mockResolvedValue({
        success: true,
        slots: mockSlots,
        active_slot: null,
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      await waitFor(() => {
        expect(screen.getByTestId("desktop-unslotted-files")).toBeInTheDocument();
        expect(screen.getByText("default")).toBeInTheDocument();
        expect(screen.getByText("speedrun")).toBeInTheDocument();
        expect(screen.queryByTestId("desktop-slot-panel-legacy")).not.toBeInTheDocument();
        expect(screen.queryByText(/This game uses legacy mode/)).not.toBeInTheDocument();
      });
    });
  });

  describe("stranded versions, slot tracking confirmation, and tab scroll", () => {
    it("renders stranded-version warning when an inactive sibling version drifted (#1298)", async () => {
      vi.mocked(backend.getVersionList).mockResolvedValue({
        multi_version: true,
        bound_vanished: false,
        versions: [
          {
            rom_id: 99,
            name: "EU Version",
            label: "EU Release",
            installed: true,
            active: false,
            vanished: false,
          } as unknown as backend.VersionInfo,
        ],
      });
      vi.mocked(backend.checkLocalDrift).mockResolvedValue({
        drifted: true,
        only_here: false,
        stranding: "unsynced",
        rom_id: 99,
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      await waitFor(() => {
        expect(backend.getVersionList).toHaveBeenCalledWith(100);
        expect(
          screen.getByText(
            'Version "EU Release" has saves that haven\'t been synced to RomM — switch back to sync them.',
          ),
        ).toBeInTheDocument();
      });
    });

    it("renders unconfirmed banner and active slot prompt when tracking is unconfigured and saves exist", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      await waitFor(() => {
        expect(screen.getByTestId("unconfirmed-slot-banner")).toBeInTheDocument();
        expect(screen.getByTestId("active-slot-unconfirmed-box")).toBeInTheDocument();
      });

      expect(screen.getByRole("button", { name: "Confirm 'default'" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Confirm Slot" })).toBeInTheDocument();
    });

    it("renders passive unconfirmed notice without banner when no saves exist", async () => {
      vi.mocked(backend.getSaveSlots).mockResolvedValue({
        success: true,
        slots: [{ slot: "default", source: "local", count: 0, latest_updated_at: null }],
        active_slot: "default",
      });
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });

      render(
        <SaveManagementCard appId={100} detail={{ ...baseDetail, saveStatus: { ...mockSaveStatus, files: [] } }} />,
      );

      await waitFor(() => expect(screen.getByTestId("active-slot-unconfirmed-box")).toBeInTheDocument());
      expect(screen.getByText("This slot is active and will be used when a save is present.")).toBeInTheDocument();
      expect(screen.queryByTestId("unconfirmed-slot-banner")).not.toBeInTheDocument();
    });

    it("auto-confirms slot when activating or creating a slot while unconfigured", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValueOnce({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      vi.mocked(backend.switchSlot).mockResolvedValue({
        success: true,
        save_status: mockSaveStatus,
      });
      const events: CustomEvent<{ type?: string; rom_id?: number }>[] = [];
      const listener = (e: Event) => events.push(e as CustomEvent<{ type?: string; rom_id?: number }>);
      globalThis.addEventListener("romm_data_changed", listener);

      try {
        render(<SaveManagementCard appId={100} detail={baseDetail} />);

        fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));
        fireEvent.click(await screen.findByRole("button", { name: "Activate Slot" }));

        await waitFor(() => {
          expect(backend.switchSlot).toHaveBeenCalledWith(42, "speedrun");
          expect(backend.confirmSlotChoice).toHaveBeenCalledWith(42, "speedrun", false, null, false);
          expect(toast.showToast).toHaveBeenCalledWith("Switched to and confirmed slot 'speedrun'");
          expect(screen.queryByTestId("unconfirmed-slot-banner")).not.toBeInTheDocument();
          expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(2);
          expect(backend.getSaveSlots).toHaveBeenCalledTimes(3);
          expect(events.some((ev) => ev.detail.type === "save_sync" && ev.detail.rom_id === 42)).toBe(true);
        });
        await act(async () => {
          await Promise.resolve();
        });
      } finally {
        globalThis.removeEventListener("romm_data_changed", listener);
      }
    });

    it("auto-confirms slot when creating a new slot while unconfigured", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValueOnce({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      vi.mocked(backend.switchSlot).mockResolvedValue({
        success: true,
        save_status: mockSaveStatus,
      });
      const events: CustomEvent<{ type?: string; rom_id?: number }>[] = [];
      const listener = (e: Event) => events.push(e as CustomEvent<{ type?: string; rom_id?: number }>);
      globalThis.addEventListener("romm_data_changed", listener);

      try {
        render(<SaveManagementCard appId={100} detail={baseDetail} />);
        await waitFor(() => expect(screen.getByTestId("unconfirmed-slot-banner")).toBeInTheDocument());

        fireEvent.click(screen.getByRole("button", { name: "+ New Slot" }));
        const dialog = screen.getByRole("dialog", { name: "New Save Slot" });
        fireEvent.change(within(dialog).getByPlaceholderText(/Slot Name/i), { target: { value: "new_slot" } });
        fireEvent.click(within(dialog).getByRole("button", { name: "Create Slot" }));

        await waitFor(() => {
          expect(backend.switchSlot).toHaveBeenCalledWith(42, "new_slot");
          expect(backend.confirmSlotChoice).toHaveBeenCalledWith(42, "new_slot", false, null, false);
          expect(toast.showToast).toHaveBeenCalledWith("Created and confirmed slot 'new_slot'");
          expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
          expect(screen.queryByTestId("unconfirmed-slot-banner")).not.toBeInTheDocument();
          expect(backend.isSaveTrackingConfigured).toHaveBeenCalledTimes(2);
          expect(backend.getSaveSlots).toHaveBeenCalledTimes(3);
          expect(events.some((ev) => ev.detail.type === "save_sync" && ev.detail.rom_id === 42)).toBe(true);
        });
        await act(async () => {
          await Promise.resolve();
        });
      } finally {
        globalThis.removeEventListener("romm_data_changed", listener);
      }
    });

    it("scrolls into view only when romm_tab_switch dispatches tab='saves'", async () => {
      const scrollSpy = vi.fn();
      const removeListenerSpy = vi.spyOn(globalThis, "removeEventListener");
      try {
        const { unmount } = render(<SaveManagementCard appId={100} detail={baseDetail} />);

        const card = await screen.findByTestId("desktop-saves-card");
        card.scrollIntoView = scrollSpy;

        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "bios" } }));
        });
        expect(scrollSpy).not.toHaveBeenCalled();

        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "saves" } }));
        });
        expect(scrollSpy).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });

        unmount();
        expect(removeListenerSpy).toHaveBeenCalledWith("romm_tab_switch", expect.any(Function));
        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "saves" } }));
        });
        expect(scrollSpy).toHaveBeenCalledTimes(1);
      } finally {
        removeListenerSpy.mockRestore();
      }
    });
  });

  describe("DesktopSlotPanel, DesktopVersionHistory, and failure/cancel edge branches", () => {
    it("confirms active slot via 'Confirm Slot', handles confirm failure and rejection", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      const confirmBtn = await screen.findByRole("button", { name: "Confirm Slot" });
      expect(confirmBtn.style.backgroundColor).toContain("rgba(26, 159, 255, 0.2)");

      let resolveConfirm!: (v: Awaited<ReturnType<typeof backend.confirmSlotChoice>>) => void;
      vi.mocked(backend.confirmSlotChoice).mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveConfirm = resolve;
          }),
      );
      const events: unknown[] = [];
      const onEvt = (e: Event) => events.push((e as CustomEvent).detail);
      globalThis.addEventListener("romm_data_changed", onEvt);
      try {
        fireEvent.click(confirmBtn);
        expect(screen.getAllByRole("button", { name: "Confirming..." })).toHaveLength(2);
        expect(screen.getAllByRole("button", { name: "Confirming..." })[0]).toBeDisabled();

        await act(async () => {
          resolveConfirm({
            success: true,
            needs_conflict_resolution: false,
            message: "",
          });
        });

        await waitFor(() => {
          expect(backend.confirmSlotChoice).toHaveBeenCalledWith(42, "default", false, null, false);
          expect(toast.showToast).toHaveBeenCalledWith("Confirmed save slot 'default'");
          expect(events).toContainEqual({ type: "save_sync", rom_id: 42 });
          expect(gameDetailStore.refreshSaveStatus).toHaveBeenCalledWith(100);
        });
      } finally {
        globalThis.removeEventListener("romm_data_changed", onEvt);
      }

      // Error branch when unconfirmed banner still allows confirm
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      const warnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
      try {
        const second = render(<SaveManagementCard appId={101} detail={{ ...baseDetail, romId: 43 }} />);
        const bannerConfirmBtn = await within(second.container).findByRole("button", { name: "Confirm 'default'" });
        vi.mocked(backend.confirmSlotChoice).mockResolvedValueOnce({
          success: false,
          reason: "unknown",
          message: "",
        });
        fireEvent.click(bannerConfirmBtn);
        await waitFor(() => expect(toast.showToast).toHaveBeenCalledWith("Failed to confirm save slot"));

        vi.mocked(backend.confirmSlotChoice).mockRejectedValueOnce(new Error("confirm boom"));
        fireEvent.click(bannerConfirmBtn);
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            expect.stringContaining("DesktopSaveManagementCard: failed to confirm slot:"),
          );
          expect(toast.showToast).toHaveBeenCalledWith("An error occurred while confirming the slot");
        });
      } finally {
        warnSpy.mockRestore();
      }
    });

    it("renders Never, Conflict banner, missing server/local metadata, and LOCAL badges", async () => {
      vi.mocked(backend.getSaveSlots).mockResolvedValue({
        success: true,
        slots: [{ slot: "localonly", source: "local", count: 1, latest_updated_at: null }],
        active_slot: "localonly",
      });
      const neverConflictDetail: GameDetailState = {
        ...baseDetail,
        saveStatus: {
          ...mockSaveStatus,
          files: [
            {
              filename: "local-only.srm",
              local_path: null,
              local_size: 4096,
              local_hash: null,
              local_mtime: null,
              server_save_id: null,
              server_file_name: null,
              server_emulator: null,
              server_updated_at: null,
              server_size: null,
              last_sync_at: null,
              status: "upload",
              device_syncs: [],
            },
          ],
          conflicts: [
            {
              type: "sync_conflict",
              rom_id: 42,
              filename: "local-only.srm",
              server_save_id: 1,
              server_updated_at: "2026-06-15T10:00:00Z",
              server_size: 4096,
              local_path: "/local/local-only.srm",
              local_hash: "h",
              local_mtime: "2026-06-15T10:00:00Z",
              local_size: 4096,
              created_at: "2026-06-15T10:00:00Z",
            },
          ],
        },
      };

      render(<SaveManagementCard appId={100} detail={neverConflictDetail} />);

      await waitFor(() => {
        expect(screen.getByText("LOCAL")).toBeInTheDocument();
        expect(screen.getByText("Never")).toBeInTheDocument();
        expect(screen.getByText("Conflict detected on this file")).toBeInTheDocument();
      });
      expect(screen.queryByText("Server save:")).not.toBeInTheDocument();
      expect(screen.queryByText("Local path:")).not.toBeInTheDocument();
      expect(screen.queryByText("Copy to slot...")).not.toBeInTheDocument();
    });

    it("cancelling Delete Slot, Copy to slot, and New Slot dialogs performs no action", async () => {
      vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue({
        success: true,
        slot: "speedrun",
        source: "server",
        is_active: false,
        server_save_count: 1,
        server_save_ids: [1],
        local_file_count: 0,
        local_filenames: [],
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));

      // Cancel Delete Slot dialog via default useDialogHost
      fireEvent.click(await screen.findByRole("button", { name: "Delete Slot" }));
      const delDialog = await screen.findByRole("dialog", { name: "Delete Slot" });
      fireEvent.click(within(delDialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "Delete Slot" })).not.toBeInTheDocument());
      await flushMacrotask();
      expect(backend.deleteSlot).not.toHaveBeenCalled();

      // Cancel Copy to slot dialog via default useDialogHost
      fireEvent.click(screen.getByRole("button", { name: "Copy to slot..." }));
      const copyDialog = await screen.findByRole("dialog", { name: "Copy save to slot" });
      fireEvent.click(within(copyDialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "Copy save to slot" })).not.toBeInTheDocument());
      await flushMacrotask();
      expect(backend.copySaveToSlot).not.toHaveBeenCalled();

      // Cancel + New Slot dialog
      fireEvent.click(screen.getByRole("button", { name: "+ New Slot" }));
      const newDialog = await screen.findByRole("dialog", { name: "New Save Slot" });
      fireEvent.click(within(newDialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "New Save Slot" })).not.toBeInTheDocument());
      await flushMacrotask();
      expect(backend.switchSlot).not.toHaveBeenCalled();
    });

    it("handles partial switch on Activate Slot, auto-clears switch error after 5s, and logs inactive save load failure", async () => {
      const warnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
      try {
        vi.mocked(backend.getSlotSaves).mockRejectedValueOnce(new Error("slot fetch down"));
        vi.mocked(backend.switchSlot)
          .mockResolvedValueOnce({
            success: false,
            reason: "switch_stopped",
            message: "partial stop",
            set_aside: ["foo.srm"],
          })
          .mockResolvedValueOnce({
            success: false,
            reason: "pending_uploads",
            message: "",
          });

        render(<SaveManagementCard appId={100} detail={baseDetail} />);
        fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));

        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSlotPanel: failed to load slot saves for speedrun: Error: slot fetch down",
          );
          expect(screen.getByText("No saves in this slot")).toBeInTheDocument();
        });

        // Partial stop shows toast and announces save sync
        fireEvent.click(screen.getByRole("button", { name: "Activate Slot" }));
        await waitFor(() => {
          expect(toast.showToast).toHaveBeenCalledWith(expect.stringContaining("Already moved there: foo.srm"));
        });

        // Error auto-clears after 5s
        vi.useFakeTimers();
        fireEvent.click(screen.getByRole("button", { name: "Activate Slot" }));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1);
        });
        expect(screen.getByText("Sync your saves first — local changes haven't been uploaded")).toBeInTheDocument();

        await act(async () => {
          await vi.advanceTimersByTimeAsync(5000);
        });
        expect(
          screen.queryByText("Sync your saves first — local changes haven't been uploaded"),
        ).not.toBeInTheDocument();
      } finally {
        vi.useRealTimers();
        warnSpy.mockRestore();
      }
    });

    it("renders DesktopVersionHistory attribution/empty variants and respects romm_data_changed filters", async () => {
      const versions: SaveVersionEntry[] = [
        {
          id: 500,
          emulator: "",
          file_name: "a.srm",
          file_size_bytes: null as unknown as number,
          updated_at: "2026-06-12T10:00:00Z",
          uploaded_by_us: undefined as unknown as boolean,
          device_syncs: [],
        },
        {
          id: 501,
          emulator: "mgba",
          file_name: "b.srm",
          file_size_bytes: 2048,
          updated_at: "2026-06-11T10:00:00Z",
          uploaded_by_us: undefined as unknown as boolean,
          device_syncs: [
            {
              device_id: "pc",
              device_name: "desktop-pc",
              is_current: true,
              last_synced_at: "2026-06-11T10:00:00Z",
            },
          ],
        },
      ];
      vi.mocked(backend.savesListFileVersions)
        .mockResolvedValueOnce({ status: "ok", versions: [] })
        .mockResolvedValueOnce({ status: "ok", versions });

      const { unmount } = render(<SaveManagementCard appId={100} detail={baseDetail} />);
      fireEvent.click(await screen.findByRole("button", { name: "Previous Versions" }));
      expect(await screen.findByText("No older versions available")).toBeInTheDocument();

      // Ignores non-save_sync and other-rom events
      act(() => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "core_changed", rom_id: 42 } }),
        );
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 999 } }));
      });
      expect(backend.savesListFileVersions).toHaveBeenCalledTimes(1);

      // Valid save_sync reloads while expanded
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      await waitFor(() => {
        expect(screen.getByText("#500")).toBeInTheDocument();
        expect(screen.getByText("—")).toBeInTheDocument();
        expect(screen.getByText("#501 · mgba")).toBeInTheDocument();
        expect(
          screen.getByText((_c, el) => el?.tagName === "SPAN" && el.textContent.includes("desktop-pc ✓")),
        ).toBeInTheDocument();
      });

      // Failed restore keeps list open and re-enables buttons
      vi.mocked(backend.savesRollbackToVersion).mockResolvedValueOnce({ status: "version_deleted" });
      fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[0]!);
      await waitFor(() => expect(toast.showToast).toHaveBeenCalledWith("This version no longer exists on the server"));
      expect(screen.getByText("#500")).toBeInTheDocument();

      unmount();
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      expect(backend.savesListFileVersions).toHaveBeenCalledTimes(2);
    });

    it("logs failures on offline snapshot, save slots, availability, local saves, and updates connection state on unreachable", async () => {
      const warnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
      try {
        vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
          success: false,
          reason: "server_unreachable",
          message: "down",
        });
        vi.mocked(backend.getSaveSyncAvailability).mockRejectedValueOnce(new Error("avail boom"));
        const first = render(<SaveManagementCard appId={100} detail={baseDetail} />);
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            expect.stringContaining("save sync availability read for romId=42 failed"),
          );
          expect(getRommConnectionState()).toBe("offline");
        });
        first.unmount();

        // Offline snapshot rejection logs warn
        setRommConnectionState("offline", "test offline");
        vi.mocked(backend.getSaveSlots).mockRejectedValueOnce(new Error("snap boom"));
        const second = render(<SaveManagementCard appId={101} detail={{ ...baseDetail, romId: 43 }} />);
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: offline slot snapshot read failed: Error: snap boom",
          );
        });
        second.unmount();

        // Save sync off read failure logs warn
        vi.mocked(backend.getLocalSaveFiles).mockRejectedValueOnce(new Error("local boom"));
        render(<SaveManagementCard appId={102} detail={{ ...baseDetail, romId: 44, saveSyncEnabled: false }} />);
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: local saves read for romId=44 failed: Error: local boom",
          );
        });
      } finally {
        warnSpy.mockRestore();
      }
    });
  });

  describe("branch and interaction pinning for slot panels, version history, and card state", () => {
    it("renders null-saveStatus active slot, conflict file status without conflicts array, Never styling, and active Copy to slot...", async () => {
      const nullStatusFirst = render(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveStatus: null }} />);
      await waitFor(() => expect(screen.getByTestId("desktop-slot-panel-default")).toBeInTheDocument());
      expect(screen.queryByText(NO_SAVE_ON_DEVICE)).not.toBeInTheDocument();
      nullStatusFirst.unmount();

      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      vi.mocked(backend.copySaveToSlot).mockResolvedValue({ status: "ok" });

      const conflictDetail: GameDetailState = {
        ...baseDetail,
        saveStatus: {
          ...mockSaveStatus,
          files: [
            {
              ...mockSaveStatus.files[0]!,
              status: "conflict",
              last_sync_at: null,
              uploaded_by_us: null,
              device_syncs: [],
              server_file_name: null,
              server_size: null,
              server_updated_at: null,
            },
          ],
          conflicts: [],
        },
      };

      render(<SaveManagementCard appId={100} detail={conflictDetail} />);

      const activePanel = await screen.findByTestId("desktop-slot-panel-default");
      const speedrunPanel = screen.getByTestId("desktop-slot-panel-speedrun");
      await waitFor(() => {
        expect(within(activePanel).getByText("Conflict detected on this file")).toBeInTheDocument();
        expect(within(activePanel).getByText("Conflict")).toBeInTheDocument();
        expect(within(activePanel).getByText("#4812 · mgba")).toBeInTheDocument();
      });

      const neverEl = within(activePanel).getByText("Never");
      expect(neverEl).toHaveStyle({ color: "#d4a72c" });
      expect(within(speedrunPanel).queryByText("Conflict")).not.toBeInTheDocument();

      // Confirm buttons (banner + active slot box) disable and dim when offline
      act(() => {
        reportServerReachable(false);
      });
      const bannerConfirmBtn = screen.getByRole("button", { name: "Confirm 'default'" });
      const slotConfirmBtn = screen.getByRole("button", { name: "Confirm Slot" });
      expect(bannerConfirmBtn).toBeDisabled();
      expect(bannerConfirmBtn).toHaveStyle({ opacity: "0.5" });
      expect(slotConfirmBtn).toBeDisabled();
      expect(slotConfirmBtn).toHaveStyle({ opacity: "0.5" });

      act(() => {
        reportServerReachable(true);
      });
      expect(bannerConfirmBtn).not.toBeDisabled();
      expect(slotConfirmBtn).not.toBeDisabled();

      // Click active row's Copy to slot... directly (target excludes 'default')
      fireEvent.click(within(activePanel).getByRole("button", { name: "Copy to slot..." }));
      const copyDialog = await screen.findByRole("dialog", { name: "Copy save to slot" });
      expect(within(copyDialog).queryByRole("button", { name: "default" })).not.toBeInTheDocument();
      fireEvent.click(within(copyDialog).getByRole("button", { name: "speedrun" }));
      await waitFor(() => expect(backend.copySaveToSlot).toHaveBeenCalledWith(42, 4812, "speedrun"));
    });

    it("pins activeHasSaves and fileCount when either saveStatus.files or slot.count is zero", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });

      const emptyFilesFirst = render(
        <SaveManagementCard appId={100} detail={{ ...baseDetail, saveStatus: { ...mockSaveStatus, files: [] } }} />,
      );
      expect(await screen.findByTestId("unconfirmed-slot-banner")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Confirm Slot" })).toBeInTheDocument();
      emptyFilesFirst.unmount();

      vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
        success: true,
        slots: [{ slot: "default", source: "server", count: 0, latest_updated_at: null }],
        active_slot: "default",
      });
      render(
        <SaveManagementCard
          appId={100}
          detail={{
            ...baseDetail,
            saveStatus: {
              ...mockSaveStatus,
              files: [{ ...mockSaveStatus.files[0]!, status: "download", last_sync_at: "2026-06-15T20:00:00Z" }],
            },
          }}
        />,
      );
      expect(await screen.findByTestId("unconfirmed-slot-banner")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Confirm Slot" })).toBeInTheDocument();
      expect(within(screen.getByTestId("desktop-slot-panel-default")).getByText("1 save")).toBeInTheDocument();
      expect(screen.getAllByText("Server newer").length).toBeGreaterThanOrEqual(2);
    });

    it("manages inactive slot loading, cache reuse, event invalidation, failed delete info, and inactive Copy to slot...", async () => {
      let resolveSaves!: (v: Awaited<ReturnType<typeof backend.getSlotSaves>>) => void;
      vi.mocked(backend.getSlotSaves).mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSaves = resolve;
          }),
      );
      vi.mocked(backend.getSlotDeleteInfo).mockResolvedValueOnce({
        success: false,
        reason: "not_found",
        message: "missing",
      });
      vi.mocked(backend.copySaveToSlot).mockResolvedValue({ status: "ok" });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      const speedrunPanel = await screen.findByTestId("desktop-slot-panel-speedrun");
      const speedrunToggle = within(speedrunPanel).getByRole("button", { name: /speedrun/ });

      // Pending expand shows spinner + Loading saves…
      fireEvent.click(speedrunToggle);
      expect(within(speedrunPanel).getByText("Loading saves…")).toBeInTheDocument();
      expect(backend.getSlotSaves).toHaveBeenCalledWith(42, "speedrun");

      await act(async () => {
        resolveSaves({
          success: true,
          slot: "speedrun",
          saves: [
            {
              filename: "alt.srm",
              id: 700,
              size: 1024,
              updated_at: "2026-06-14T10:00:00Z",
              emulator: "mgba",
            },
          ],
        });
      });
      await waitFor(() => expect(within(speedrunPanel).getByText("alt.srm")).toBeInTheDocument());
      expect(within(speedrunPanel).getByText(/#700 · 1\.0 KB/)).toBeInTheDocument();

      // Inactive Copy to slot... dims and disables offline
      act(() => {
        reportServerReachable(false);
      });
      const inactiveCopyBtn = within(speedrunPanel).getByRole("button", { name: "Copy to slot..." });
      expect(inactiveCopyBtn).toBeDisabled();
      expect(inactiveCopyBtn).toHaveStyle({ opacity: "0.5" });
      act(() => {
        reportServerReachable(true);
      });
      expect(inactiveCopyBtn).not.toBeDisabled();
      expect(inactiveCopyBtn).toHaveStyle({ opacity: "1" });

      // Idle Activate / Delete buttons have no throbbers, and failed delete info aborts dialog
      const activateBtn = within(speedrunPanel).getByRole("button", { name: "Activate Slot" });
      const deleteBtn = within(speedrunPanel).getByRole("button", { name: "Delete Slot" });
      expect(activateBtn.querySelector("svg")).toBeNull();
      expect(deleteBtn.querySelector("svg")).toBeNull();

      fireEvent.click(deleteBtn);
      await waitFor(() => expect(backend.getSlotDeleteInfo).toHaveBeenCalledWith(42, "speedrun"));
      expect(screen.queryByRole("dialog", { name: "Delete Slot" })).not.toBeInTheDocument();

      // Re-collapsing and re-expanding uses cache without refetch; mismatched events keep cache
      fireEvent.click(speedrunToggle);
      fireEvent.click(speedrunToggle);
      expect(backend.getSlotSaves).toHaveBeenCalledTimes(1);

      act(() => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "core_changed", rom_id: 42 } }),
        );
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 999 } }));
      });
      fireEvent.click(speedrunToggle);
      fireEvent.click(speedrunToggle);
      expect(backend.getSlotSaves).toHaveBeenCalledTimes(1);

      // Copy from inactive save row passes sourceSlot='speedrun' (announces save_sync while expanded -> reloads)
      fireEvent.click(within(speedrunPanel).getByRole("button", { name: "Copy to slot..." }));
      const copyDialog = await screen.findByRole("dialog", { name: "Copy save to slot" });
      expect(within(copyDialog).queryByRole("button", { name: "speedrun" })).not.toBeInTheDocument();
      fireEvent.click(within(copyDialog).getByRole("button", { name: "default" }));
      await waitFor(() => {
        expect(backend.copySaveToSlot).toHaveBeenCalledWith(42, 700, "default");
        expect(backend.getSlotSaves).toHaveBeenCalledTimes(2);
      });

      // Collapsed save_sync clears cached files without fetching until re-expanded
      fireEvent.click(speedrunToggle);
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      expect(backend.getSlotSaves).toHaveBeenCalledTimes(2);
      fireEvent.click(speedrunToggle);
      await waitFor(() => expect(backend.getSlotSaves).toHaveBeenCalledTimes(3));

      // Activating inactive slot forwards outcome.saveStatus
      const activatedStatus: SaveStatus = {
        ...mockSaveStatus,
        files: [{ ...mockSaveStatus.files[0]!, filename: "activated-slot.srm" }],
      };
      vi.mocked(backend.switchSlot).mockResolvedValueOnce({
        success: true,
        save_status: activatedStatus,
      });
      const activationEvents: CustomEvent<{ type?: string; save_status?: SaveStatus }>[] = [];
      const activationListener = (e: Event) =>
        activationEvents.push(e as CustomEvent<{ type?: string; save_status?: SaveStatus }>);
      globalThis.addEventListener("romm_data_changed", activationListener);
      try {
        fireEvent.click(within(speedrunPanel).getByRole("button", { name: "Activate Slot" }));
        await waitFor(() => {
          expect(
            activationEvents.some(
              (ev) =>
                ev.detail.type === "save_sync" && ev.detail.save_status?.files[0]?.filename === "activated-slot.srm",
            ),
          ).toBe(true);
        });
      } finally {
        globalThis.removeEventListener("romm_data_changed", activationListener);
      }
    });

    it("pins DesktopVersionHistory loading state, attribution variants, retry clear, offline event guard, and post-restore refetch", async () => {
      let resolveList!: (v: Awaited<ReturnType<typeof backend.savesListFileVersions>>) => void;
      const mixedVersions: SaveVersionEntry[] = [
        {
          id: 901,
          emulator: "mgba",
          file_name: "a.srm",
          file_size_bytes: 1024,
          updated_at: "2026-06-12T10:00:00Z",
          uploaded_by_us: true,
          device_syncs: [],
        },
        {
          id: 902,
          emulator: "mgba",
          file_name: "b.srm",
          file_size_bytes: 2048,
          updated_at: "2026-06-11T10:00:00Z",
          uploaded_by_us: undefined as unknown as boolean,
          device_syncs: [
            {
              device_id: "deck-b",
              device_name: "Deck-B",
              is_current: false,
              last_synced_at: "2026-06-11T10:00:00Z",
            },
          ],
        },
        {
          id: 903,
          emulator: "",
          file_name: "c.srm",
          file_size_bytes: 512,
          updated_at: "2026-06-10T10:00:00Z",
          uploaded_by_us: null as unknown as boolean,
          device_syncs: [],
        },
      ];

      vi.mocked(backend.savesListFileVersions)
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              resolveList = resolve;
            }),
        )
        .mockResolvedValueOnce({ status: "romm_refused", message: "boom" })
        .mockResolvedValueOnce({ status: "ok", versions: mixedVersions })
        .mockResolvedValueOnce({ status: "ok", versions: mixedVersions });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      const toggle = await screen.findByRole("button", { name: "Previous Versions" });
      await waitFor(() => {
        expect(backend.isSaveTrackingConfigured).toHaveBeenCalled();
        expect(backend.getSaveSyncAvailability).toHaveBeenCalled();
        expect(backend.getVersionList).toHaveBeenCalled();
      });

      // Collapsed save_sync clears cached versions/error without fetching
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      await flushMacrotask();
      expect(backend.savesListFileVersions).not.toHaveBeenCalled();
      await waitFor(() => expect(backend.getSaveSlots).toHaveBeenCalledTimes(2));

      // Expand shows Loading versions… and exact arguments
      fireEvent.click(toggle);
      expect(screen.getByText("Loading versions…")).toBeInTheDocument();
      expect(backend.savesListFileVersions).toHaveBeenCalledWith(42, "default", "Mario Golf - Advance Tour.srm");

      await act(async () => {
        resolveList({ status: "ok", versions: mixedVersions });
      });

      const verCheck = await screen.findAllByText("✓");
      expect(verCheck.length).toBeGreaterThanOrEqual(3);
      for (const el of verCheck) {
        expect(el).toHaveStyle({ color: "#5ba32b" });
      }
      expect(
        screen.getByText((_c, el) => el?.tagName === "SPAN" && el.textContent.includes("· (this device) ✓")),
      ).toBeInTheDocument();
      expect(
        screen.getByText((_c, el) => el?.tagName === "SPAN" && el.textContent.includes("Deck-B")),
      ).toBeInTheDocument();
      expect(screen.queryByText(/· null/)).not.toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: "Restore" })[0]!.querySelector("svg")).toBeNull();

      // Matching save_sync failure clears stale loaded versions
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync" } }));
      });
      await waitFor(() => {
        expect(screen.queryByText("#901 · mgba")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
      });

      // Retry recovers versions
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      await waitFor(() => expect(screen.getByText("#901 · mgba")).toBeInTheDocument());

      // Offline save_sync invalidates cache without refetching while offline, then reconnect auto-fetches
      vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
        success: false,
        reason: "server_unreachable",
        message: "offline",
      });
      act(() => {
        reportServerReachable(false);
      });
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      await flushMacrotask();
      expect(screen.getByText("Offline — versions unavailable")).toBeInTheDocument();
      expect(backend.savesListFileVersions).toHaveBeenCalledTimes(3);

      // Reconnect auto-fetches without needing collapse/re-expand; restore then collapses & clears cache
      act(() => {
        reportServerReachable(true);
      });
      await waitFor(() => expect(backend.savesListFileVersions).toHaveBeenCalledTimes(4));
      expect(await screen.findByText("#901 · mgba")).toBeInTheDocument();

      vi.mocked(backend.savesRollbackToVersion).mockResolvedValueOnce({ status: "ok" });
      fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[0]!);
      await waitFor(() => {
        expect(screen.queryByText("#901 · mgba")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Previous Versions" })).toBeInTheDocument();
      });
    });

    it("covers card subtitles, save-sync-off event reloads/no-hint refusal, save-sync-on events, LastKnown badges, Unslotted empty/conflict, and catch logWarn calls", async () => {
      const warnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
      try {
        // 1. Save sync off: subtitle, non-hint refusal, event reload filtering
        vi.mocked(backend.getLocalSaveFiles).mockResolvedValueOnce({
          available: false,
          installed: true,
          files: [],
          reason: "saves_root_missing",
          message: "No local save folder yet.",
        });
        const offView = render(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: false }} />);
        await waitFor(() => {
          expect(screen.getByText("save sync off")).toBeInTheDocument();
          expect(screen.getByText("No local save folder yet.")).toBeInTheDocument();
        });
        expect(screen.queryByText(CONTENT_DIR_HINT)).not.toBeInTheDocument();

        vi.mocked(backend.getLocalSaveFiles).mockResolvedValue({
          available: true,
          installed: true,
          files: [],
        });
        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "rom_data", rom_id: 42 } }));
          globalThis.dispatchEvent(
            new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 999 } }),
          );
        });
        await flushMacrotask();
        expect(backend.getLocalSaveFiles).toHaveBeenCalledTimes(1);

        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "core_changed" } }));
        });
        await waitFor(() => expect(backend.getLocalSaveFiles).toHaveBeenCalledTimes(2));

        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "emulator_sources" } }));
        });
        await waitFor(() => expect(backend.getLocalSaveFiles).toHaveBeenCalledTimes(3));

        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
        });
        await waitFor(() => expect(backend.getLocalSaveFiles).toHaveBeenCalledTimes(4));

        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync" } }));
        });
        await waitFor(() => expect(backend.getLocalSaveFiles).toHaveBeenCalledTimes(5));
        await flushMacrotask();
        expect(backend.getVersionList).not.toHaveBeenCalled();
        expect(backend.getSaveSlots).not.toHaveBeenCalled();
        offView.unmount();

        // 2. Refusal subtitle ("save sync unavailable") and ignores save_sync reloads
        vi.mocked(backend.getSaveSyncAvailability).mockResolvedValueOnce({
          available: false,
          installed: true,
          established: true,
          emulator_label: "mGBA",
          reason: "saves_root_missing",
          message: "Unavailable here.",
        });
        const refusedView = render(<SaveManagementCard appId={100} detail={baseDetail} />);
        await waitFor(() => expect(screen.getByText("save sync unavailable")).toBeInTheDocument());
        const refusedSlotCalls = vi.mocked(backend.getSaveSlots).mock.calls.length;
        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
        });
        expect(backend.getSaveSlots).toHaveBeenCalledTimes(refusedSlotCalls);
        refusedView.unmount();

        // 3. LastKnown LOCAL vs SERVER & singular/plural + Unslotted with conflict match & no-server-error empty
        setRommConnectionState("offline", "test offline");
        vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
          success: false,
          reason: "server_unreachable",
          message: "down",
          last_known: {
            active_slot: "default",
            slots: [
              { slot: "default", source: "local", count: 1, latest_updated_at: "2026-06-15T21:04:00Z" },
              { slot: "backup", source: "server", count: 2, latest_updated_at: null },
            ],
          },
        });
        const unslottedView = render(
          <SaveManagementCard
            appId={100}
            detail={{
              ...baseDetail,
              saveStatus: {
                ...mockSaveStatus,
                files: [
                  { ...mockSaveStatus.files[0]!, filename: "a.srm", status: "upload" },
                  { ...mockSaveStatus.files[0]!, filename: "b.srm", status: "synced" },
                ],
                conflicts: [
                  {
                    type: "sync_conflict",
                    rom_id: 42,
                    filename: "a.srm",
                    server_save_id: 1,
                    server_updated_at: "2026-06-15T10:00:00Z",
                    server_size: 1024,
                    local_path: "/tmp/a.srm",
                    local_hash: "h",
                    local_mtime: "2026-06-15T10:00:00Z",
                    local_size: 1024,
                    created_at: "2026-06-15T10:00:00Z",
                  },
                ],
              },
            }}
          />,
        );
        const defaultLastKnown = await screen.findByTestId("desktop-last-known-slot-default");
        const backupLastKnown = screen.getByTestId("desktop-last-known-slot-backup");
        expect(within(defaultLastKnown).getByText("ACTIVE")).toBeInTheDocument();
        expect(within(defaultLastKnown).getByText("LOCAL")).toBeInTheDocument();
        expect(within(defaultLastKnown).getByText("1 save")).toBeInTheDocument();
        expect(within(defaultLastKnown).getByText(/^Newest save: /)).toBeInTheDocument();
        expect(within(backupLastKnown).queryByText("ACTIVE")).not.toBeInTheDocument();
        expect(within(backupLastKnown).getByText("SERVER")).toBeInTheDocument();
        expect(within(backupLastKnown).getByText("2 saves")).toBeInTheDocument();
        expect(within(backupLastKnown).queryByText(/^Newest save: /)).not.toBeInTheDocument();
        expect(screen.getAllByText("Conflict detected on this file")).toHaveLength(1);
        expect(screen.getByText("Conflict detected on this file").parentElement?.textContent).toContain("a.srm");
        expect(screen.getByText("Conflict detected on this file").parentElement?.textContent).not.toContain("b.srm");
        expect(screen.getByText("Checked:")).toBeInTheDocument();
        unslottedView.unmount();

        // 3b. Empty UnslottedFilesSection (server_query_failed: false) renders noSaveLine;
        // notDownloaded renders refusal+hint
        vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
          success: false,
          reason: "not_found",
          message: "missing",
        });
        setRommConnectionState("connected", "test setup");
        const emptyUnslotted = render(
          <SaveManagementCard
            appId={100}
            detail={{
              ...baseDetail,
              installed: false,
              saveStatus: { ...mockSaveStatus, files: [], server_query_failed: false },
            }}
          />,
        );
        await waitFor(() => {
          expect(screen.getByText(/No save yet — mGBA's saves/)).toBeInTheDocument();
        });
        expect(getRommConnectionState()).toBe("connected");
        emptyUnslotted.unmount();

        vi.mocked(backend.getSaveSyncAvailability).mockResolvedValueOnce({
          available: false,
          installed: false,
          established: true,
          emulator_label: "mGBA",
          reason: "savefiles_in_content_dir",
          message: "Saves sit beside game.",
        });
        vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
          success: true,
          slots: mockSlots,
          active_slot: "default",
        });
        const notDlRefused = render(<SaveManagementCard appId={100} detail={{ ...baseDetail, installed: false }} />);
        await waitFor(() => {
          expect(screen.getByText("Saves sit beside game.")).toBeInTheDocument();
          expect(screen.getByText(CONTENT_DIR_HINT)).toBeInTheDocument();
        });
        notDlRefused.unmount();

        // 4. Live events + refreshSlots + readTrackingConfigured confirm/catch + catch logWarn coverage
        vi.mocked(backend.isSaveTrackingConfigured)
          .mockResolvedValueOnce({ configured: false, active_slot: "default", setup_needed: true })
          .mockResolvedValueOnce({ configured: true, active_slot: "default", setup_needed: false });
        vi.mocked(backend.getSaveSlots)
          .mockRejectedValueOnce(new Error("slots boom"))
          .mockResolvedValueOnce({
            success: true,
            slots: [...mockSlots, { slot: "refreshed-slot", source: "server", count: 0, latest_updated_at: null }],
            active_slot: "default",
          })
          .mockResolvedValue({
            success: true,
            slots: mockSlots,
            active_slot: "default",
          });
        vi.mocked(backend.getVersionList).mockRejectedValueOnce(new Error("versions boom"));
        setRommConnectionState("connected", "test setup");

        const liveView = render(<SaveManagementCard appId={100} detail={baseDetail} />);
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: failed to load save slots: Error: slots boom",
          );
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: stranded-version check failed for appId 100: Error: versions boom",
          );
          expect(screen.queryByTestId("desktop-saves-connecting")).not.toBeInTheDocument();
        });

        // Reconnect / Sync triggers tracking false -> true banner clear and slot list refresh
        act(() => {
          globalThis.dispatchEvent(
            new CustomEvent("romm_data_changed", { detail: { type: "core_changed", rom_id: 42 } }),
          );
          globalThis.dispatchEvent(
            new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 999 } }),
          );
        });
        const callsBefore = vi.mocked(backend.getSaveSlots).mock.calls.length;
        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
        });
        await waitFor(() => {
          expect(backend.getSaveSlots).toHaveBeenCalledTimes(callsBefore + 1);
          expect(screen.getByText("refreshed-slot")).toBeInTheDocument();
        });

        // Second save_sync switches unconfirmed false -> true via refreshSlots
        vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValueOnce({
          configured: false,
          active_slot: "default",
          setup_needed: true,
        });
        liveView.unmount();

        const trackView = render(<SaveManagementCard appId={100} detail={baseDetail} />);
        expect(await screen.findByTestId("unconfirmed-slot-banner")).toBeInTheDocument();
        vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValueOnce({
          configured: true,
          active_slot: "default",
          setup_needed: false,
        });
        act(() => {
          reportServerReachable(false);
        });
        expect(getRommConnectionState()).toBe("offline");
        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
        });
        await waitFor(() => {
          expect(screen.queryByTestId("unconfirmed-slot-banner")).not.toBeInTheDocument();
          expect(getRommConnectionState()).toBe("connected");
        });
        trackView.unmount();

        // Failing isSaveTrackingConfigured logs warn and falls back to confirmed=true
        vi.mocked(backend.isSaveTrackingConfigured).mockRejectedValueOnce(new Error("track boom"));
        const trackErrView = render(<SaveManagementCard appId={100} detail={baseDetail} />);
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: failed to check tracking: Error: track boom",
          );
        });
        expect(screen.queryByTestId("unconfirmed-slot-banner")).not.toBeInTheDocument();
        trackErrView.unmount();

        // Failing refreshSlots and failing auto-confirm log warnings
        vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValueOnce({
          configured: false,
          active_slot: "default",
          setup_needed: true,
        });
        vi.mocked(backend.confirmSlotChoice).mockRejectedValueOnce(new Error("auto confirm boom"));

        const autoFailView = render(<SaveManagementCard appId={100} detail={baseDetail} />);
        fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));
        vi.mocked(backend.switchSlot).mockResolvedValueOnce({ success: true, save_status: mockSaveStatus });
        vi.mocked(backend.getSaveSlots).mockRejectedValueOnce(new Error("refresh boom"));
        fireEvent.click(await screen.findByRole("button", { name: "Activate Slot" }));
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: auto-confirm for 'speedrun' failed: Error: auto confirm boom",
          );
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: failed to refresh save slots: Error: refresh boom",
          );
        });
        autoFailView.unmount();
      } finally {
        warnSpy.mockRestore();
      }
    });

    it("promotes connection state from checking to connected when loadDesktopSlots succeeds, synthesizes missing activeSlot, and keeps connected on non-unreachable refresh failure", async () => {
      let resolveSlots!: (v: Awaited<ReturnType<typeof backend.getSaveSlots>>) => void;
      vi.mocked(backend.getSaveSlots).mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSlots = resolve;
          }),
      );
      setRommConnectionState("checking", "test checking");

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      expect(await screen.findByTestId("desktop-saves-connecting")).toBeInTheDocument();
      expect(getRommConnectionState()).toBe("checking");

      await act(async () => {
        resolveSlots({
          success: true,
          slots: mockSlots,
          active_slot: "synthesized",
        });
      });

      await waitFor(() => {
        expect(getRommConnectionState()).toBe("connected");
        expect(screen.getByTestId("desktop-slot-panel-synthesized")).toBeInTheDocument();
        expect(backend.getSaveSlots).toHaveBeenCalledWith(42);
        expect(backend.isSaveTrackingConfigured).toHaveBeenCalledWith(42);
      });

      // Refresh returning non-unreachable failure leaves connection state connected
      vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
        success: false,
        reason: "not_found",
        message: "missing",
      });
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      await waitFor(() => expect(backend.getSaveSlots).toHaveBeenCalledTimes(2));
      expect(backend.isSaveTrackingConfigured).toHaveBeenNthCalledWith(2, 42);
      expect(getRommConnectionState()).toBe("connected");
    });

    it("handles offline slot snapshot recovery, offline unconfigured tracking, and false confirmSlotChoice in auto-confirm", async () => {
      setRommConnectionState("offline", "test offline");
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValueOnce({
        configured: false,
        active_slot: "offline-recovered",
        setup_needed: true,
      });
      vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
        success: true,
        slots: [{ slot: "offline-recovered", source: "server", count: 1, latest_updated_at: null }],
        active_slot: "offline-recovered",
      });

      const offlineView = render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await waitFor(() => {
        expect(screen.getByTestId("desktop-slot-panel-offline-recovered")).toBeInTheDocument();
        expect(screen.getByTestId("unconfirmed-slot-banner")).toBeInTheDocument();
        expect(getRommConnectionState()).toBe("connected");
      });
      expect(backend.getSaveSlots).toHaveBeenCalledTimes(1);
      expect(backend.getSaveSlots).toHaveBeenCalledWith(42);
      expect(backend.isSaveTrackingConfigured).toHaveBeenCalledWith(42);
      offlineView.unmount();

      // Auto-confirm returning success: false toasts Switched to slot rather than Switched to and confirmed
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValueOnce({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      vi.mocked(backend.switchSlot).mockResolvedValueOnce({
        success: true,
        save_status: mockSaveStatus,
      });
      vi.mocked(backend.confirmSlotChoice).mockResolvedValueOnce({
        success: false,
        reason: "unknown",
        message: "no",
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));
      fireEvent.click(await screen.findByRole("button", { name: "Activate Slot" }));
      await waitFor(() => {
        expect(toast.showToast).toHaveBeenCalledWith("Switched to slot 'speedrun'");
      });
    });

    it("orders concurrent same-ROM getSaveSlots / refreshSlots responses by slotLoadSeqRef", async () => {
      let resolveSlow!: (v: Awaited<ReturnType<typeof backend.getSaveSlots>>) => void;
      let resolveFast!: (v: Awaited<ReturnType<typeof backend.getSaveSlots>>) => void;
      vi.mocked(backend.getSaveSlots)
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              resolveSlow = resolve;
            }),
        )
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              resolveFast = resolve;
            }),
        );

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      expect(backend.getSaveSlots).toHaveBeenCalledTimes(1);

      // Fire save_sync while the initial load is still in flight -> starts seq #2
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      expect(backend.getSaveSlots).toHaveBeenCalledTimes(2);

      await act(async () => {
        resolveFast({
          success: true,
          slots: [{ slot: "fresh-slot", source: "server", count: 1, latest_updated_at: null }],
          active_slot: "fresh-slot",
        });
      });
      expect(await screen.findByTestId("desktop-slot-panel-fresh-slot")).toBeInTheDocument();

      // Stale seq #1 resolves afterward and must not overwrite seq #2
      await act(async () => {
        resolveSlow({
          success: true,
          slots: [{ slot: "stale-slot", source: "server", count: 1, latest_updated_at: null }],
          active_slot: "stale-slot",
        });
      });
      await flushMacrotask();
      expect(screen.getByTestId("desktop-slot-panel-fresh-slot")).toBeInTheDocument();
      expect(screen.queryByTestId("desktop-slot-panel-stale-slot")).not.toBeInTheDocument();
    });

    it("rejects stale savesListFileVersions responses by readSeqRef after save_sync invalidation", async () => {
      let resolveSlowVersions!: (v: Awaited<ReturnType<typeof backend.savesListFileVersions>>) => void;
      let resolveFreshVersions!: (v: Awaited<ReturnType<typeof backend.savesListFileVersions>>) => void;
      vi.mocked(backend.savesListFileVersions)
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              resolveSlowVersions = resolve;
            }),
        )
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              resolveFreshVersions = resolve;
            }),
        );

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      fireEvent.click(await screen.findByRole("button", { name: "Previous Versions" }));
      expect(backend.savesListFileVersions).toHaveBeenCalledTimes(1);

      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      expect(backend.savesListFileVersions).toHaveBeenCalledTimes(2);

      await act(async () => {
        resolveFreshVersions({
          status: "ok",
          versions: [
            {
              id: 702,
              emulator: "mgba",
              file_name: "fresh.srm",
              file_size_bytes: 2048,
              updated_at: "2026-06-15T12:00:00Z",
              uploaded_by_us: true,
              device_syncs: [],
            },
          ],
        });
      });
      expect(await screen.findByText("#702 · mgba")).toBeInTheDocument();

      await act(async () => {
        resolveSlowVersions({
          status: "ok",
          versions: [
            {
              id: 701,
              emulator: "mgba",
              file_name: "stale.srm",
              file_size_bytes: 1024,
              updated_at: "2026-06-10T12:00:00Z",
              uploaded_by_us: true,
              device_syncs: [],
            },
          ],
        });
      });
      await flushMacrotask();
      expect(screen.getByText("#702 · mgba")).toBeInTheDocument();
      expect(screen.queryByText("#701 · mgba")).not.toBeInTheDocument();
    });

    it("clears stale answers on live saveSyncEnabled toggles and reloads when re-enabled", async () => {
      vi.mocked(backend.getSaveSlots)
        .mockResolvedValueOnce({
          success: true,
          slots: [{ slot: "before-toggle", source: "server", count: 1, latest_updated_at: null }],
          active_slot: "before-toggle",
        })
        .mockImplementationOnce(() => new Promise(() => undefined));

      const { rerender } = render(<SaveManagementCard appId={100} detail={baseDetail} />);
      expect(await screen.findByTestId("desktop-slot-panel-before-toggle")).toBeInTheDocument();

      // Disable save sync live -> clears slot state & shows local saves off state
      rerender(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: false }} />);
      expect(await screen.findByTestId("desktop-saves-sync-off")).toBeInTheDocument();
      expect(screen.queryByTestId("desktop-slot-panel-before-toggle")).not.toBeInTheDocument();

      // Re-enable save sync live -> does not flash 'before-toggle', shows connecting indicator while fetching
      rerender(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: true }} />);
      await flushMacrotask();
      expect(screen.getByTestId("desktop-saves-connecting")).toBeInTheDocument();
      expect(screen.queryByTestId("desktop-slot-panel-before-toggle")).not.toBeInTheDocument();
    });

    it("logs operational refusals via logWarn and isolates post-confirm refreshSaveStatus rejection", async () => {
      const warnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
      try {
        // 1. Offline snapshot refusal, inactive getSlotSaves refusal, refreshSlots refusal, confirmSlotChoice refusal
        setRommConnectionState("offline", "test offline");
        vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
          success: false,
          reason: "server_unreachable",
          message: "offline snap err",
        });
        const offlineView = render(<SaveManagementCard appId={100} detail={baseDetail} />);
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: offline slot snapshot for romId=42 failed: offline snap err",
          );
        });
        offlineView.unmount();

        setRommConnectionState("connected", "test setup");
        vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
          configured: false,
          active_slot: "default",
          setup_needed: true,
        });
        vi.mocked(backend.getSlotSaves).mockResolvedValueOnce({
          success: false,
          reason: "server_error",
          message: "slot list denied",
        });
        vi.mocked(backend.confirmSlotChoice).mockResolvedValueOnce({
          success: false,
          reason: "server_error",
          message: "confirm refused",
        });

        render(<SaveManagementCard appId={100} detail={baseDetail} />);
        fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSlotPanel: getSlotSaves for romId=42 slot='speedrun' failed (server_error): slot list denied",
          );
        });

        vi.mocked(backend.getSaveSlots).mockResolvedValueOnce({
          success: false,
          reason: "server_error",
          message: "refresh refused",
        });
        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
        });
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: failed to refresh save slots for romId=42: refresh refused",
          );
        });

        vi.mocked(gameDetailStore.refreshSaveStatus).mockClear();
        fireEvent.click(screen.getByRole("button", { name: "Confirm Slot" }));
        await waitFor(() => {
          expect(warnSpy).toHaveBeenCalledWith(
            "DesktopSaveManagementCard: confirmSlotChoice for romId=42 slot='default' refused (server_error): confirm refused",
          );
        });
        expect(gameDetailStore.refreshSaveStatus).not.toHaveBeenCalled();

        // 2. Post-confirm refreshSaveStatus rejection does not misreport slot confirm failure
        warnSpy.mockClear();
        vi.mocked(backend.confirmSlotChoice).mockResolvedValueOnce({
          success: true,
          needs_conflict_resolution: false,
          message: "confirmed",
        });
        vi.mocked(gameDetailStore.refreshSaveStatus).mockRejectedValueOnce(new Error("status down"));
        fireEvent.click(screen.getByRole("button", { name: "Confirm Slot" }));
        await waitFor(() => {
          expect(toast.showToast).toHaveBeenCalledWith("Confirmed save slot 'default'");
        });
        await flushMacrotask();
        expect(toast.showToast).not.toHaveBeenCalledWith("An error occurred while confirming the slot");
        expect(warnSpy).not.toHaveBeenCalledWith(expect.stringContaining("failed to confirm slot"));
        expect(warnSpy).not.toHaveBeenCalledWith(expect.stringContaining("failed to refresh save slots"));
      } finally {
        warnSpy.mockRestore();
      }
    });

    it("clears live gate answers on saveSyncEnabled and installed transitions without stale flashes", async () => {
      vi.mocked(backend.getSaveSyncAvailability).mockResolvedValueOnce({
        available: false,
        installed: true,
        established: true,
        emulator_label: "PCSX2",
        reason: "savefiles_in_content_dir",
        message: "Refused",
      });

      const first = render(<SaveManagementCard appId={100} detail={baseDetail} />);
      expect(await screen.findByTestId("desktop-saves-refusal")).toHaveTextContent("Refused");

      vi.mocked(backend.getLocalSaveFiles).mockResolvedValueOnce({
        available: false,
        installed: true,
        files: [],
        reason: "savefiles_in_content_dir",
        message: "Beside file",
      });
      first.rerender(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: false }} />);
      expect(await screen.findByText("Beside file")).toBeInTheDocument();

      vi.mocked(backend.getSaveSyncAvailability).mockImplementationOnce(() => new Promise(() => undefined));
      vi.mocked(backend.getSaveSlots).mockImplementationOnce(() => new Promise(() => undefined));
      vi.mocked(backend.isSaveTrackingConfigured).mockImplementationOnce(() => new Promise(() => undefined));
      vi.mocked(backend.getVersionList).mockImplementationOnce(() => new Promise(() => undefined));
      first.rerender(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: true }} />);
      await flushMacrotask();
      expect(screen.queryByTestId("desktop-saves-refusal")).not.toBeInTheDocument();

      vi.mocked(backend.getLocalSaveFiles).mockImplementationOnce(() => new Promise(() => undefined));
      first.rerender(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: false }} />);
      await flushMacrotask();
      expect(screen.queryByText("Beside file")).not.toBeInTheDocument();
      first.unmount();

      vi.mocked(backend.getSaveSyncAvailability).mockResolvedValueOnce({
        available: true,
        installed: false,
        established: true,
        emulator_label: "mGBA",
      });
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValueOnce({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      vi.mocked(backend.getVersionList).mockResolvedValueOnce({
        multi_version: true,
        bound_vanished: false,
        versions: [
          {
            rom_id: 99,
            name: "EU Version",
            label: "EU Release",
            installed: true,
            active: false,
            vanished: false,
          } as unknown as backend.VersionInfo,
        ],
      });
      vi.mocked(backend.checkLocalDrift).mockResolvedValueOnce({
        drifted: true,
        only_here: false,
        stranding: "unsynced",
        rom_id: 99,
      });

      const second = render(<SaveManagementCard appId={100} detail={{ ...baseDetail, installed: false }} />);
      expect(await screen.findByTestId("desktop-saves-not-downloaded")).toBeInTheDocument();
      expect(await screen.findByTestId("unconfirmed-slot-banner")).toBeInTheDocument();
      expect(await screen.findByText(/EU Release/)).toBeInTheDocument();

      vi.mocked(backend.getSaveSyncAvailability).mockImplementationOnce(() => new Promise(() => undefined));
      second.rerender(<SaveManagementCard appId={100} detail={{ ...baseDetail, installed: true }} />);
      expect(screen.queryByTestId("desktop-saves-not-downloaded")).not.toBeInTheDocument();

      second.rerender(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: false }} />);
      expect(await screen.findByTestId("desktop-saves-sync-off")).toBeInTheDocument();

      vi.mocked(backend.getSaveSyncAvailability).mockImplementationOnce(() => new Promise(() => undefined));
      vi.mocked(backend.isSaveTrackingConfigured).mockImplementationOnce(() => new Promise(() => undefined));
      vi.mocked(backend.getVersionList).mockImplementationOnce(() => new Promise(() => undefined));
      vi.mocked(backend.getSaveSlots).mockImplementationOnce(() => new Promise(() => undefined));
      second.rerender(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveSyncEnabled: true }} />);
      await flushMacrotask();
      expect(screen.queryByTestId("unconfirmed-slot-banner")).not.toBeInTheDocument();
      expect(screen.queryByText(/EU Release/)).not.toBeInTheDocument();
    });

    it("updates inactive slot fileCount after load and uses last_sync_at fallback statusLabel", async () => {
      const customStatus: SaveStatus = {
        ...mockSaveStatus,
        files: [
          {
            ...mockSaveStatus.files[0]!,
            status: "none" as unknown as "synced",
            last_sync_at: "2026-06-15T21:04:00Z",
          },
        ],
      };
      vi.mocked(backend.getSaveSlots).mockResolvedValue({
        success: true,
        slots: [
          { slot: "default", source: "server", count: 1, latest_updated_at: null },
          { slot: "speedrun", source: "server", count: 1, latest_updated_at: null },
        ],
        active_slot: "default",
      });
      vi.mocked(backend.getSlotSaves).mockResolvedValueOnce({
        success: true,
        slot: "speedrun",
        saves: [
          { id: 91, filename: "fresh-1.srm", size: 128, emulator: "mgba", updated_at: "2026-06-15T12:00:00Z" },
          { id: 92, filename: "fresh-2.srm", size: 256, emulator: "mgba", updated_at: "2026-06-15T12:00:00Z" },
        ],
      });

      render(<SaveManagementCard appId={100} detail={{ ...baseDetail, saveStatus: customStatus }} />);
      const defaultPanel = await screen.findByTestId("desktop-slot-panel-default");
      expect(within(defaultPanel).getByText("Synced")).toBeInTheDocument();

      const speedrunPanel = screen.getByTestId("desktop-slot-panel-speedrun");
      expect(within(speedrunPanel).getByText("1 save")).toBeInTheDocument();

      fireEvent.click(within(speedrunPanel).getByRole("button", { name: /speedrun/ }));
      await flushMacrotask();
      expect(within(speedrunPanel).getByText("fresh-1.srm")).toBeInTheDocument();
      expect(within(speedrunPanel).getByText("2 saves")).toBeInTheDocument();
    });

    it("guards inactive slotSavesSeqRef against late resolve/reject and clears switchError on retry", async () => {
      const warnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
      try {
        vi.mocked(backend.getSaveSlots).mockResolvedValue({
          success: true,
          slots: [
            { slot: "default", source: "server", count: 1, latest_updated_at: null },
            { slot: "speedrun", source: "server", count: 1, latest_updated_at: null },
          ],
          active_slot: "default",
        });

        let resolveSlowSlot!: (v: Awaited<ReturnType<typeof backend.getSlotSaves>>) => void;
        let resolveFreshSlot!: (v: Awaited<ReturnType<typeof backend.getSlotSaves>>) => void;
        let rejectSlowSlot!: (err: Error) => void;
        vi.mocked(backend.getSlotSaves)
          .mockImplementationOnce(
            () =>
              new Promise((resolve) => {
                resolveSlowSlot = resolve;
              }),
          )
          .mockImplementationOnce(
            () =>
              new Promise((resolve) => {
                resolveFreshSlot = resolve;
              }),
          )
          .mockImplementationOnce(
            () =>
              new Promise((_, reject) => {
                rejectSlowSlot = reject;
              }),
          )
          .mockResolvedValueOnce({
            success: true,
            slot: "speedrun",
            saves: [
              { id: 93, filename: "fresh-3.srm", size: 512, emulator: "mgba", updated_at: "2026-06-15T12:00:00Z" },
            ],
          });

        render(<SaveManagementCard appId={100} detail={baseDetail} />);
        const speedrunPanel = await screen.findByTestId("desktop-slot-panel-speedrun");
        await flushMacrotask();

        fireEvent.click(within(speedrunPanel).getByRole("button", { name: /speedrun/ }));
        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
        });
        expect(within(speedrunPanel).getByText("Loading saves…")).toBeInTheDocument();

        // Stale seq=1 resolves while seq=2 is still pending -> Loading saves… stays visible and stale.srm is ignored
        await act(async () => {
          resolveSlowSlot({
            success: true,
            slot: "speedrun",
            saves: [{ id: 88, filename: "stale.srm", size: 64, emulator: "mgba", updated_at: "2026-06-10T12:00:00Z" }],
          });
        });
        await flushMacrotask();
        expect(within(speedrunPanel).getByText("Loading saves…")).toBeInTheDocument();
        expect(within(speedrunPanel).queryByText("stale.srm")).not.toBeInTheDocument();

        await act(async () => {
          resolveFreshSlot({
            success: true,
            slot: "speedrun",
            saves: [
              { id: 91, filename: "fresh-1.srm", size: 128, emulator: "mgba", updated_at: "2026-06-15T12:00:00Z" },
            ],
          });
        });
        await flushMacrotask();
        expect(within(speedrunPanel).getByText("fresh-1.srm")).toBeInTheDocument();
        expect(within(speedrunPanel).queryByText("Loading saves…")).not.toBeInTheDocument();

        act(() => {
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
          globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
        });
        await flushMacrotask();
        expect(within(speedrunPanel).getByText("fresh-3.srm")).toBeInTheDocument();

        await act(async () => {
          rejectSlowSlot(new Error("late boom"));
        });
        await flushMacrotask();
        expect(within(speedrunPanel).getByText("fresh-3.srm")).toBeInTheDocument();
        expect(within(speedrunPanel).queryByText("No saves in this slot")).not.toBeInTheDocument();

        vi.mocked(backend.switchSlot).mockRejectedValueOnce(new Error("boom"));
        fireEvent.click(within(speedrunPanel).getByRole("button", { name: "Activate Slot" }));
        await flushMacrotask();
        expect(within(speedrunPanel).getByText("An error occurred while switching slots")).toBeInTheDocument();

        vi.mocked(backend.switchSlot).mockImplementationOnce(() => new Promise(() => undefined));
        fireEvent.click(within(speedrunPanel).getByRole("button", { name: "Activate Slot" }));
        expect(within(speedrunPanel).queryByText("An error occurred while switching slots")).not.toBeInTheDocument();
        expect(within(speedrunPanel).getByRole("button", { name: "Switching..." })).toBeDisabled();
      } finally {
        warnSpy.mockRestore();
      }
    });

    it("clears Retry error while fetching, ignores collapse toggle, holds Confirming until refresh settles, and sets reachable on New Slot", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      vi.mocked(backend.savesListFileVersions)
        .mockResolvedValueOnce({ status: "romm_refused", message: "denied" })
        .mockResolvedValueOnce({ status: "romm_refused", message: "denied" })
        .mockImplementationOnce(() => new Promise(() => undefined));

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await flushMacrotask();
      const versionToggle = screen.getByRole("button", { name: "Previous Versions" });

      await act(async () => {
        fireEvent.click(versionToggle);
        await new Promise((r) => setTimeout(r, 0));
      });
      expect(screen.getByText(/denied/)).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(screen.queryByText(/denied/)).not.toBeInTheDocument();
      expect(screen.getByText("Loading versions…")).toBeInTheDocument();
      await flushMacrotask();
      expect(screen.getByText(/denied/)).toBeInTheDocument();

      // Collapsing after an error, receiving save_sync, then re-expanding clears loadError and refetches
      fireEvent.click(versionToggle);
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: 42 } }));
      });
      fireEvent.click(versionToggle);
      expect(screen.queryByText(/denied/)).not.toBeInTheDocument();
      expect(screen.getByText("Loading versions…")).toBeInTheDocument();
      expect(backend.savesListFileVersions).toHaveBeenCalledTimes(3);

      await act(async () => {
        fireEvent.click(versionToggle);
        await new Promise((r) => setTimeout(r, 0));
      });
      expect(backend.savesListFileVersions).toHaveBeenCalledTimes(3);

      const confirmResolvers: Array<() => void> = [];
      vi.mocked(gameDetailStore.refreshSaveStatus).mockImplementation(
        () =>
          new Promise((resolve) => {
            confirmResolvers.push(() => resolve(null));
          }),
      );
      vi.mocked(backend.confirmSlotChoice).mockResolvedValueOnce({
        success: true,
        needs_conflict_resolution: false,
        message: "ok",
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Confirm Slot" }));
        await new Promise((r) => setTimeout(r, 0));
      });
      expect(screen.getAllByRole("button", { name: "Confirming..." })[0]).toBeDisabled();
      await act(async () => {
        for (const done of confirmResolvers) done();
        await new Promise((r) => setTimeout(r, 0));
      });
      await flushMacrotask();
      vi.mocked(gameDetailStore.refreshSaveStatus).mockResolvedValue(null);

      act(() => {
        setRommConnectionState("checking", "transient");
      });
      vi.mocked(backend.switchSlot).mockResolvedValueOnce({ success: true, save_status: mockSaveStatus });
      fireEvent.click(screen.getByRole("button", { name: "+ New Slot" }));
      fireEvent.change(screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)"), {
        target: { value: "hardcore" },
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Create Slot" }));
        await new Promise((r) => setTimeout(r, 0));
      });
      await flushMacrotask();
      await flushMacrotask();
      expect(getRommConnectionState()).toBe("connected");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
