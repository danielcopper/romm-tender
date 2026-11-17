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

describe("SaveManagementCard", () => {
  const mockSaveStatus: SaveStatus = {
    rom_id: 42,
    device_id: "steamdeck",
    last_sync_check_at: "2026-06-15T21:04:00Z",
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
    it("renders nothing and issues no backend reads before detail.romId is known", () => {
      render(<SaveManagementCard appId={100} detail={{ ...baseDetail, romId: null }} />);

      expect(screen.queryByTestId("desktop-saves-card")).not.toBeInTheDocument();
      expect(backend.getSaveSlots).not.toHaveBeenCalled();
      expect(backend.getSaveSyncAvailability).not.toHaveBeenCalled();
      expect(backend.getLocalSaveFiles).not.toHaveBeenCalled();
    });

    it("renders nothing and issues no backend reads while the panel is stranded", () => {
      setStrandedAnswer("reloads");
      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      expect(screen.queryByTestId("desktop-saves-card")).not.toBeInTheDocument();
      expect(backend.getSaveSlots).not.toHaveBeenCalled();
      expect(backend.getSaveSyncAvailability).not.toHaveBeenCalled();
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
        expect(screen.getByText("~/retrodeck/saves/gba/Mario Golf - Advance Tour.srm")).toBeInTheDocument();
        expect(screen.getByText("✓")).toBeInTheDocument();
      });
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

      await waitFor(() => expect(screen.getByText("default")).toBeInTheDocument());
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
      vi.mocked(backend.syncRomSaves).mockResolvedValue({
        success: true,
        message: "ok",
        synced: 1,
        uploaded: 1,
        downloaded: 0,
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await waitFor(() => expect(screen.getByText("default")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "Sync now" }));

      await waitFor(() => {
        expect(backend.syncRomSaves).toHaveBeenCalledWith(42);
        expect(toast.showToast).toHaveBeenCalledWith("Saves uploaded to RomM");
      });
    });

    it("confirms and executes Delete Local Saves when installed", async () => {
      vi.mocked(backend.deleteLocalSaves).mockResolvedValue({
        success: true,
        deleted_count: 2,
        message: "Deleted 2 local saves",
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await waitFor(() => expect(screen.getByText("default")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "Delete Local Saves" }));
      expect(await screen.findByRole("dialog", { name: "Delete Local Saves" })).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Delete" }));

      await waitFor(() => {
        expect(backend.deleteLocalSaves).toHaveBeenCalledWith(42);
        expect(gameDetailStore.noteSaveSyncDisplay).toHaveBeenCalledWith(100, 42, {
          status: "none",
          label: "No saves",
          last_sync_check_at: null,
        });
        expect(toast.showToast).toHaveBeenCalledWith("Deleted 2 local saves");
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

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

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

      fireEvent.click(screen.getByRole("button", { name: "Delete" }));

      await waitFor(() => {
        expect(backend.deleteSlot).toHaveBeenCalledWith(42, "speedrun");
        expect(toast.showToast).toHaveBeenCalledWith("Slot 'speedrun' deleted");
      });
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

      await act(async () => {
        resolveRestore({ status: "ok" });
      });

      await waitFor(() => {
        expect(backend.savesRollbackToVersion).toHaveBeenCalledWith(42, "default", 4809);
        expect(toast.showToast).toHaveBeenCalledWith(expect.stringContaining("Save restored"));
        expect(screen.getByRole("button", { name: "Previous Versions" })).toBeInTheDocument();
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

    it("shows offline notice in expanded Previous Versions when RomM goes offline", async () => {
      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      const toggle = screen.getByRole("button", { name: "Previous Versions" });

      act(() => {
        reportServerReachable(false);
      });

      fireEvent.click(toggle);
      expect(screen.getByText("Offline — versions unavailable")).toBeInTheDocument();
      expect(backend.savesListFileVersions).not.toHaveBeenCalled();
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

      expect(screen.getByRole("button", { name: "Delete Local Saves" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Sync now" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "+ New Slot" })).not.toBeInTheDocument();
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
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      vi.mocked(backend.switchSlot).mockResolvedValue({
        success: true,
        save_status: mockSaveStatus,
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);

      fireEvent.click(await screen.findByRole("button", { name: /speedrun/ }));
      fireEvent.click(await screen.findByRole("button", { name: "Activate Slot" }));

      await waitFor(() => {
        expect(backend.switchSlot).toHaveBeenCalledWith(42, "speedrun");
        expect(backend.confirmSlotChoice).toHaveBeenCalledWith(42, "speedrun", false, null, false);
        expect(toast.showToast).toHaveBeenCalledWith("Switched to and confirmed slot 'speedrun'");
      });
    });

    it("auto-confirms slot when creating a new slot while unconfigured", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: "default",
        setup_needed: true,
      });
      vi.mocked(backend.switchSlot).mockResolvedValue({
        success: true,
        save_status: mockSaveStatus,
      });

      render(<SaveManagementCard appId={100} detail={baseDetail} />);
      await waitFor(() => expect(screen.getByText("default")).toBeInTheDocument());

      fireEvent.click(screen.getByRole("button", { name: "+ New Slot" }));
      const dialog = screen.getByRole("dialog", { name: "New Save Slot" });
      fireEvent.change(within(dialog).getByPlaceholderText(/Slot Name/i), { target: { value: "new_slot" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Create Slot" }));

      await waitFor(() => {
        expect(backend.switchSlot).toHaveBeenCalledWith(42, "new_slot");
        expect(backend.confirmSlotChoice).toHaveBeenCalledWith(42, "new_slot", false, null, false);
        expect(toast.showToast).toHaveBeenCalledWith("Created and confirmed slot 'new_slot'");
      });
    });

    it("scrolls into view only when romm_tab_switch dispatches tab='saves'", async () => {
      const scrollSpy = vi.fn();
      render(<SaveManagementCard appId={100} detail={baseDetail} />);

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

      vi.mocked(backend.confirmSlotChoice).mockResolvedValueOnce({
        success: true,
        needs_conflict_resolution: false,
        message: "",
      });
      const events: unknown[] = [];
      const onEvt = (e: Event) => events.push((e as CustomEvent).detail);
      globalThis.addEventListener("romm_data_changed", onEvt);
      try {
        fireEvent.click(confirmBtn);
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
          expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining("failed to confirm slot"));
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
      expect(backend.deleteSlot).not.toHaveBeenCalled();

      // Cancel Copy to slot dialog via default useDialogHost
      fireEvent.click(screen.getByRole("button", { name: "Copy to slot..." }));
      const copyDialog = await screen.findByRole("dialog", { name: "Copy save to slot" });
      fireEvent.click(within(copyDialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "Copy save to slot" })).not.toBeInTheDocument());
      expect(backend.copySaveToSlot).not.toHaveBeenCalled();

      // Cancel + New Slot dialog
      fireEvent.click(screen.getByRole("button", { name: "+ New Slot" }));
      const newDialog = await screen.findByRole("dialog", { name: "New Save Slot" });
      fireEvent.click(within(newDialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "New Save Slot" })).not.toBeInTheDocument());
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
});
