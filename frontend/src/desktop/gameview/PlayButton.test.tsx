import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { PlayButton } from "./PlayButton";
import { DiscSelector } from "./DiscSelector";
import * as gameDetailStore from "../../utils/gameDetailStore";
import * as downloadStore from "../../utils/downloadStore";
import * as sessionManager from "../../utils/sessionManager";
import * as backend from "../../api/backend";
import * as toast from "../../utils/toast";
import * as steamShortcuts from "../../utils/steamShortcuts";
import { setRommConnectionState } from "../../utils/connectionState";
import { resetBoundAvailability, setBoundAvailability } from "../../utils/vanishedBinding";
import { RESUME_TARGET_OCCUPIED_TOAST } from "../../utils/adoptWording";
import { setStrandedAnswer } from "../../test-utils/stranded-panel";
import { emitHostEvent } from "../../test-utils/host-event-bus";
import { throbberIn } from "../../test-utils/throbber";
import { stubAppStore } from "../../test-utils/steamStubs";
import type { GameDetailState } from "../../utils/gameDetailStore";
import type {
  AdoptionCandidate,
  AdoptResult,
  DownloadCompleteEvent,
  DownloadFailedEvent,
  DownloadItem,
  DownloadProgressEvent,
  GameLaunch,
  PreLaunchSyncStartedEvent,
  SaveStatus,
  SyncConflict,
  TargetOccupiedResult,
  UninstallProgressEvent,
} from "../../types";
import { launchRefusal } from "../../utils/gameLaunch";

vi.mock("../../utils/gameDetailStore", () => ({
  useGameDetail: vi.fn(),
  refreshSaveStatus: vi.fn(),
  recheckStart: vi.fn(),
}));

/** How many times the selector stand-in below has mounted. */
const selectorMounts = vi.hoisted(() => ({ count: 0 }));

vi.mock("./DiscSelector", async () => {
  const { useEffect } = await import("react");
  return {
    DiscSelector: vi.fn(() => {
      useEffect(() => {
        selectorMounts.count += 1;
      }, []);
      return <div data-testid="disc-selector" />;
    }),
  };
});

vi.mock("../../utils/downloadStore", () => ({
  useDownloads: vi.fn(),
}));

/** Whether Steam lists the app as running, as the `readGameRunning` stand-in below reads it. */
const steamListsApp = vi.hoisted(() => vi.fn((_appId: number) => false));

vi.mock("../../utils/sessionManager", () => {
  const isSessionActive = vi.fn((_romId: number) => false);
  return {
    isSessionActive,
    noteAppRom: vi.fn(),
    readGameRunning: vi.fn((appId: number, romId: number | null | undefined) => {
      const running = (romId != null && isSessionActive(romId)) || steamListsApp(appId);
      return { running, decidedBy: running ? "store" : "none", diagnostics: "" };
    }),
  };
});

vi.mock("../../api/backend", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/backend")>();
  return {
    isEndpointFailure: actual.isEndpointFailure,
    // The adoption flow and the resume press read these off the backend's answers.
    isTargetOccupied: actual.isTargetOccupied,
    isCandidatesFound: actual.isCandidatesFound,
    isRenameCollisions: actual.isRenameCollisions,
    isUnusableNamesake: actual.isUnusableNamesake,
    isCandidateVanished: actual.isCandidateVanished,
    checkStartSource: vi.fn(),
    getSaveStatus: vi.fn(),
    resolveSyncConflict: vi.fn(),
    preLaunchSync: vi.fn(),
    stopRunningGame: vi.fn(),
    debugLog: vi.fn(),
    logError: vi.fn(),
    getSaveSetupInfo: vi.fn(),
    confirmSlotChoice: vi.fn(),
    probeReachability: vi.fn(),
    getInstalledRom: vi.fn(),
    isSaveTrackingConfigured: vi.fn(),
    checkCoreChange: vi.fn(),
    checkLocalDrift: vi.fn(),
    prepareGameStart: vi.fn(),
    releasePruneConflictLease: vi.fn(),
    getCachedGameDetail: vi.fn(),
    startDownload: vi.fn(),
    adoptExistingRom: vi.fn(),
    cancelDownload: vi.fn(),
    pauseDownload: vi.fn(),
    resumeDownload: vi.fn(),
    removeRom: vi.fn(),
    verifyExistingContent: vi.fn(),
  };
});

vi.mock("../../utils/steamShortcuts", () => ({
  setLaunchOptionsConfirmed: vi.fn().mockResolvedValue(true),
}));

vi.mock("../../utils/toast", () => ({
  showToast: vi.fn(),
}));

const APP_ID = 123;

const CONFLICT: SyncConflict = {
  type: "sync_conflict",
  rom_id: 100,
  filename: "smw.srm",
  server_save_id: 7,
  server_updated_at: "2026-01-02T00:00:00Z",
  server_size: 2048,
  local_path: "/saves/smw.srm",
  local_hash: "abc",
  local_mtime: "2026-01-01T00:00:00Z",
  local_size: 1024,
  created_at: "2026-01-01T00:00:00Z",
};

function detailState(overrides: Partial<GameDetailState> = {}): GameDetailState {
  return {
    romId: 100,
    romName: "Super Mario World",
    platformSlug: "snes",
    platformName: "",
    installed: true,
    fileMissingAt: null,
    downloadSizeBytes: null,
    saveSyncEnabled: false,
    saveStatus: null,
    saveSyncStatus: null,
    saveSyncLabel: "",
    raId: null,
    achievementEarned: 0,
    achievementTotal: 0,
    biosNeeded: false,
    biosLabel: "",
    biosRequiredMissing: false,
    activeCoreLabel: null,
    activeCoreIsDefault: true,
    emulators: [],
    emulatorDataAvailable: true,
    emulatorDataReason: null,
    emulatorSource: null,
    emulatorPlatformSystem: null,
    platformCoreLabel: null,
    hasGameOverride: false,
    emulatorStart: null,
    vanishedPick: null,
    ...overrides,
  };
}

/** A verdict that the game's file is not one its system starts from, unless the overrides say otherwise. */
const notALaunchFile = (overrides: Partial<GameLaunch> = {}): GameLaunch => ({
  state: "not_a_launch_file",
  entry: "Snes9x",
  system: "snes",
  platform_name: "Super Nintendo",
  file: "smw.zip",
  extension: ".zip",
  starts_from: [".sfc", ".smc"],
  other_entries: [],
  install_in: null,
  reason: null,
  contents_unread: false,
  format_unestablished: false,
  ...overrides,
});

const mockRunGame = vi.fn();

const sessionChanged = (running: boolean, romId: number) =>
  act(() => {
    globalThis.dispatchEvent(new CustomEvent("romm_session_changed", { detail: { running, appId: APP_ID, romId } }));
  });

const pressPlay = () => fireEvent.click(screen.getByRole("button", { name: /^PLAY/ }));

describe("PlayButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("SteamClient", { Apps: { RunGame: mockRunGame } });
    stubAppStore({});
    vi.mocked(sessionManager.isSessionActive).mockReturnValue(false);
    steamListsApp.mockReturnValue(false);
    vi.mocked(downloadStore.useDownloads).mockReturnValue([]);
    vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState());
    vi.mocked(backend.checkStartSource).mockResolvedValue({ switched_off: null });
    vi.mocked(backend.getInstalledRom).mockResolvedValue({ launchable: true } as never);
    vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
      configured: true,
      active_slot: "slot1",
      setup_needed: false,
    });
    vi.mocked(backend.checkCoreChange).mockResolvedValue({ changed: false });
    vi.mocked(backend.probeReachability).mockResolvedValue({ online: true });
    vi.mocked(backend.checkLocalDrift).mockResolvedValue({
      drifted: false,
      only_here: false,
      stranding: "none",
      rom_id: 100,
    });
    vi.mocked(backend.preLaunchSync).mockResolvedValue({
      success: true,
      synced: 0,
      uploaded: 0,
      downloaded: 0,
    } as never);
    vi.mocked(backend.prepareGameStart).mockResolvedValue({
      success: true,
      app_id: APP_ID,
      launch_options: String(APP_ID),
      prune_lease_token: "launch-lease",
    });
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
    vi.mocked(backend.stopRunningGame).mockResolvedValue({ success: true, stopped: 1, force_killed: 0 } as never);
    vi.mocked(backend.getCachedGameDetail).mockResolvedValue({ found: false });
    vi.mocked(backend.startDownload).mockResolvedValue({ success: true } as never);
    vi.mocked(backend.cancelDownload).mockResolvedValue({ success: true } as never);
    vi.mocked(backend.pauseDownload).mockResolvedValue({ success: true } as never);
    vi.mocked(backend.resumeDownload).mockResolvedValue({ success: true } as never);
    vi.mocked(backend.removeRom).mockResolvedValue({ success: true, message: "", prune_lease_token: null } as never);
    setRommConnectionState("connected");
    resetBoundAvailability();
  });

  it("draws nothing until the page's detail names the game's ROM", () => {
    vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ romId: null, installed: false }));
    const { container } = render(<PlayButton appId={APP_ID} />);
    expect(container).toBeEmptyDOMElement();
  });

  describe("PLAY", () => {
    it("starts the game through the pre-launch sync", async () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ saveSyncEnabled: true }));
      render(<PlayButton appId={APP_ID} />);

      pressPlay();

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledWith("123", "", -1, 100));
      expect(backend.preLaunchSync).toHaveBeenCalledWith(100);
      expect(sessionManager.noteAppRom).toHaveBeenCalledWith(APP_ID, 100);
    });

    it("reads PREPARING... until the backend reports the sync began, SYNCING SAVES... after, and takes no second press", async () => {
      let finishSync!: (value: never) => void;
      vi.mocked(backend.preLaunchSync).mockReturnValue(new Promise((resolve) => (finishSync = resolve)));
      let answerProbe!: (value: { online: boolean }) => void;
      vi.mocked(backend.probeReachability).mockReturnValue(new Promise((resolve) => (answerProbe = resolve)));
      render(<PlayButton appId={APP_ID} />);

      pressPlay();
      const preparing = await screen.findByRole("button", { name: /PREPARING\.\.\./ });
      expect(preparing).toBeDisabled();
      fireEvent.click(preparing);
      await act(async () => answerProbe({ online: true }));
      await waitFor(() => expect(backend.preLaunchSync).toHaveBeenCalled());
      expect(screen.getByRole("button", { name: /PREPARING\.\.\./ })).toBeDisabled();

      act(() => emitHostEvent<PreLaunchSyncStartedEvent>("pre_launch_sync_started", { rom_id: 100 }));
      expect(screen.getByRole("button", { name: /SYNCING SAVES\.\.\./ })).toBeDisabled();
      await act(async () => finishSync({ success: true, synced: 0, uploaded: 0, downloaded: 0 } as never));

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledTimes(1));
      expect(backend.probeReachability).toHaveBeenCalledTimes(1);
    });

    it("reads LAUNCHING... once started, STOP when the session begins, and PLAY again when it ends", async () => {
      render(<PlayButton appId={APP_ID} />);

      pressPlay();
      expect(await screen.findByRole("button", { name: /LAUNCHING\.\.\./ })).toBeDisabled();
      await waitFor(() => expect(mockRunGame).toHaveBeenCalled());

      sessionChanged(true, 100);
      expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["STOP"]);
      sessionChanged(false, 100);
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeEnabled();
    });

    it("comes down to PLAY from a STOP whose session ended unseen, without asking and without a start", async () => {
      render(<PlayButton appId={APP_ID} />);
      pressPlay();
      await waitFor(() => expect(mockRunGame).toHaveBeenCalledTimes(1));
      sessionChanged(true, 100);

      fireEvent.click(screen.getByRole("button", { name: /^STOP/ }));

      expect(await screen.findByRole("button", { name: /^PLAY/ })).toBeEnabled();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(backend.stopRunningGame).not.toHaveBeenCalled();
      expect(mockRunGame).toHaveBeenCalledTimes(1);
    });

    it("keeps PREPARING... through a STOP whose session ended unseen, and starts no second check", async () => {
      vi.mocked(backend.probeReachability).mockReturnValue(new Promise(() => {}));
      render(<PlayButton appId={APP_ID} />);
      pressPlay();
      await screen.findByRole("button", { name: /PREPARING\.\.\./ });
      sessionChanged(true, 100);

      fireEvent.click(screen.getByRole("button", { name: /^STOP/ }));

      const preparing = await screen.findByRole("button", { name: /PREPARING\.\.\./ });
      expect(preparing).toBeDisabled();
      fireEvent.click(preparing);
      expect(backend.probeReachability).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("button", { name: /^PLAY/ })).not.toBeInTheDocument();
    });

    it("adopts the default slot without asking where that is safe, and starts the game", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: null,
        setup_needed: true,
      });
      vi.mocked(backend.getSaveSetupInfo).mockResolvedValue({
        recommended_action: "auto_confirm_default",
        default_slot: "default",
        server_slots: [],
      } as never);
      vi.mocked(backend.confirmSlotChoice).mockResolvedValue({ success: true } as never);
      render(<PlayButton appId={APP_ID} />);

      pressPlay();

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledWith("123", "", -1, 100));
      expect(backend.confirmSlotChoice).toHaveBeenCalledWith(100, "default", false, null, false);
      expect(toast.showToast).not.toHaveBeenCalled();
    });

    it("with save sync off, starts the game without the slot setup", async () => {
      // What RomM would answer is set up, so a start that asked would adopt a slot.
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: null,
        setup_needed: false,
      });
      vi.mocked(backend.getSaveSetupInfo).mockResolvedValue({
        recommended_action: "auto_confirm_default",
        default_slot: "default",
        server_slots: [],
      } as never);
      render(<PlayButton appId={APP_ID} />);

      pressPlay();

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledWith("123", "", -1, 100));
      expect(backend.getSaveSetupInfo).not.toHaveBeenCalled();
      expect(backend.confirmSlotChoice).not.toHaveBeenCalled();
      expect(toast.showToast).not.toHaveBeenCalled();
    });

    it("starts the game where a slot is the user's to choose, asking and toasting nothing", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({
        configured: false,
        active_slot: null,
        setup_needed: true,
      });
      vi.mocked(backend.getSaveSetupInfo).mockResolvedValue({
        recommended_action: "show_wizard",
        default_slot: "default",
        server_slots: [{ slot: "default" }],
      } as never);
      render(<PlayButton appId={APP_ID} />);

      pressPlay();

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledWith("123", "", -1, 100));
      expect(backend.getSaveSetupInfo).toHaveBeenCalledWith(100);
      expect(backend.confirmSlotChoice).not.toHaveBeenCalled();
      expect(toast.showToast).not.toHaveBeenCalled();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("is disabled with the emulator's refusal as its title, pointing at no emulator menu", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          emulatorStart: { state: "cannot_start", reason: "not_installed", entry: "DuckStation" },
          emulators: [{ label: "DuckStation" }, { label: "Beetle PSX" }] as never,
          activeCoreLabel: "DuckStation",
        }),
      );
      render(<PlayButton appId={APP_ID} />);

      const play = screen.getByRole("button", { name: /^PLAY/ });
      expect(play).toBeDisabled();
      expect(play).toHaveAttribute("title", "DuckStation is not installed, so this game cannot start.");
      expect(play).toHaveStyle({ opacity: "0.5" });
    });

    it("is disabled with the file's refusal as its title where the file does not start with its emulator", () => {
      const launch = notALaunchFile();
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          emulatorStart: { state: "starts", reason: null, entry: "Snes9x" },
          gameLaunch: launch,
          startCheck: { state: "answered" },
        }),
      );
      render(<PlayButton appId={APP_ID} />);

      const play = screen.getByRole("button", { name: /^PLAY/ });
      expect(play).toBeDisabled();
      expect(play).toHaveAttribute("title", launchRefusal(launch, false));
      expect(play.getAttribute("title")).toContain("smw.zip is not the file Super Nintendo starts from");
    });

    it("says the file's refusal first, then that Tender cannot start the entry yet (#2221 D20)", () => {
      const launch = notALaunchFile();
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          emulatorStart: { state: "cannot_start", reason: "desktop_file", entry: "Snes9x" },
          gameLaunch: launch,
          startCheck: { state: "answered" },
        }),
      );
      render(<PlayButton appId={APP_ID} />);

      const play = screen.getByRole("button", { name: /^PLAY/ });
      expect(play).toBeDisabled();
      expect(play).toHaveAttribute(
        "title",
        `${launchRefusal(launch, false)} Tender cannot start Snes9x yet: it starts through a .desktop file.`,
      );
    });

    it("words the file's refusal for a page with no emulator menu to point at", () => {
      const launch = notALaunchFile({ state: "not_this_emulator", extension: ".sfc", other_entries: ["bsnes"] });
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          emulatorStart: { state: "starts", reason: null, entry: "Snes9x" },
          gameLaunch: launch,
          startCheck: { state: "answered" },
        }),
      );
      render(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: /^PLAY/ })).toHaveAttribute(
        "title",
        "Snes9x does not start .sfc; other emulators of this system do.",
      );
    });

    it("titles PLAY with the emulator's refusal where the file's would refuse it too", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          emulatorStart: { state: "cannot_start", reason: "not_installed", entry: "DuckStation" },
          gameLaunch: notALaunchFile(),
          startCheck: { state: "answered" },
        }),
      );
      render(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: /^PLAY/ })).toHaveAttribute(
        "title",
        "DuckStation is not installed, so this game cannot start.",
      );
    });

    it("reads CHECKING... and takes no press while the page checks whether the game can start", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ startCheck: { state: "checking" } }));
      render(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: "CHECKING..." })).toBeDisabled();
      expect(screen.queryByRole("button", { name: /^PLAY/ })).not.toBeInTheDocument();
    });

    it("gives way to DOWNLOAD and to RESOLVE CONFLICT while the page checks", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({ installed: false, startCheck: { state: "checking" } }),
      );
      const { unmount } = render(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "CHECKING..." })).not.toBeInTheDocument();
      unmount();

      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          saveSyncEnabled: true,
          saveStatus: { rom_id: 100, files: [], conflicts: [CONFLICT] } as unknown as SaveStatus,
          startCheck: { state: "checking" },
        }),
      );
      render(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: /RESOLVE CONFLICT/ })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "CHECKING..." })).not.toBeInTheDocument();
    });

    it("is grey and pressable after the page's check went wrong, and a press asks again before it starts", async () => {
      vi.mocked(gameDetailStore.recheckStart).mockResolvedValueOnce(null);
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          emulatorStart: { state: "starts", reason: null, entry: "Snes9x" },
          // A refusing answer beside the failed check, which refuses nothing.
          gameLaunch: notALaunchFile(),
          startCheck: { state: "failed", why: "Tender could not check whether this game can start: x." },
        }),
      );
      render(<PlayButton appId={APP_ID} />);

      const play = screen.getByRole("button", { name: /^PLAY/ });
      expect(play).toBeEnabled();
      expect(play).not.toHaveAttribute("title");
      expect(play).toHaveStyle({ background: "linear-gradient(to right, #6b7b6b 0%, #5a6a5a 60%)" });
      pressPlay();

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledWith("123", "", -1, 100));
      expect(gameDetailStore.recheckStart).toHaveBeenCalledWith(APP_ID, 100, false);
    });

    it("is enabled, without a title or the refused look, when nothing stands in the start's way", () => {
      render(<PlayButton appId={APP_ID} />);

      const play = screen.getByRole("button", { name: /^PLAY/ });
      expect(play).toBeEnabled();
      expect(play).not.toHaveAttribute("title");
      expect(play).not.toHaveStyle({ opacity: "0.5" });
      expect(play).toHaveStyle({ background: "linear-gradient(to right, #70d61d 0%, #01a75b 60%)" });
    });

    it("shows PLAY for a save status that holds no conflict", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          saveSyncEnabled: true,
          saveStatus: { rom_id: 100, files: [], conflicts: [] } as unknown as SaveStatus,
        }),
      );
      render(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /RESOLVE CONFLICT/ })).not.toBeInTheDocument();
    });

    it("resolves a pre-launch conflict in the dialog as a conflict before the start, then launches", async () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ saveSyncEnabled: true }));
      vi.mocked(backend.preLaunchSync).mockResolvedValueOnce({
        success: true,
        synced: 0,
        uploaded: 0,
        downloaded: 0,
        conflicts: [CONFLICT],
      } as never);
      vi.mocked(backend.resolveSyncConflict).mockResolvedValueOnce({ success: true } as never);
      render(<PlayButton appId={APP_ID} />);

      pressPlay();
      await screen.findByRole("dialog", { name: "Save conflict for smw.srm" });
      expect(mockRunGame).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Use Server" }));

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledWith("123", "", -1, 100));
      expect(backend.resolveSyncConflict).toHaveBeenCalledWith(100, "smw.srm", 7, "use_server", true);
    });

    it("does not launch when the pre-launch conflict is cancelled, and shows RESOLVE CONFLICT", async () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ saveSyncEnabled: true }));
      vi.mocked(backend.preLaunchSync).mockResolvedValueOnce({
        success: true,
        synced: 0,
        uploaded: 0,
        downloaded: 0,
        conflicts: [CONFLICT],
      } as never);
      render(<PlayButton appId={APP_ID} />);

      pressPlay();
      await screen.findByRole("dialog");
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      expect(await screen.findByRole("button", { name: /RESOLVE CONFLICT/ })).toBeInTheDocument();
      expect(mockRunGame).not.toHaveBeenCalled();
      expect(backend.resolveSyncConflict).not.toHaveBeenCalled();
      expect(gameDetailStore.refreshSaveStatus).toHaveBeenCalledWith(APP_ID);
    });

    it("offline with local drift asks in the offline-drift dialog and launches on Start Anyway", async () => {
      vi.mocked(backend.probeReachability).mockResolvedValue({ online: false });
      vi.mocked(backend.checkLocalDrift).mockResolvedValue({
        drifted: true,
        only_here: false,
        stranding: "unsynced",
        rom_id: 100,
      });
      render(<PlayButton appId={APP_ID} />);

      pressPlay();
      const dialog = await screen.findByRole("dialog", { name: "RomM Unreachable" });
      expect(dialog).toHaveTextContent("Your local save has unsynced changes.");
      expect(mockRunGame).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Start Anyway" }));

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledWith("123", "", -1, 100));
    });

    it("offline with local drift starts nothing when the dialog is cancelled, and returns to PLAY", async () => {
      vi.mocked(backend.probeReachability).mockResolvedValue({ online: false });
      vi.mocked(backend.checkLocalDrift).mockResolvedValue({
        drifted: true,
        only_here: false,
        stranding: "unsynced",
        rom_id: 100,
      });
      render(<PlayButton appId={APP_ID} />);

      pressPlay();
      await screen.findByRole("dialog", { name: "RomM Unreachable" });
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(mockRunGame).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeEnabled();
    });

    it("offline with local drift retries, probes again, and launches once RomM answers", async () => {
      vi.mocked(backend.probeReachability)
        .mockResolvedValueOnce({ online: false })
        .mockResolvedValueOnce({ online: true });
      vi.mocked(backend.checkLocalDrift).mockResolvedValue({
        drifted: true,
        only_here: false,
        stranding: "unsynced",
        rom_id: 100,
      });
      render(<PlayButton appId={APP_ID} />);

      pressPlay();
      await screen.findByRole("dialog", { name: "RomM Unreachable" });
      fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledWith("123", "", -1, 100));
      expect(backend.preLaunchSync).toHaveBeenCalledWith(100);
    });

    it("a failed pre-launch sync asks in the fallback dialog and launches on Launch Anyway", async () => {
      vi.mocked(backend.preLaunchSync).mockResolvedValueOnce({
        success: false,
        message: "Server returned 500",
      } as never);
      render(<PlayButton appId={APP_ID} />);

      pressPlay();
      const dialog = await screen.findByRole("dialog", { name: "Save Sync Unavailable" });
      expect(dialog).toHaveTextContent("Server returned 500 — launch with local saves?");
      fireEvent.click(screen.getByRole("button", { name: "Launch Anyway" }));

      await waitFor(() => expect(mockRunGame).toHaveBeenCalledWith("123", "", -1, 100));
    });
  });

  describe("STOP", () => {
    it("is the one button while the game runs, and stops it once the dialog is confirmed", async () => {
      vi.mocked(sessionManager.isSessionActive).mockReturnValue(true);
      render(<PlayButton appId={APP_ID} />);
      expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["STOP"]);

      fireEvent.click(screen.getByRole("button", { name: /^STOP/ }));
      const confirm = await screen.findByRole("dialog", { name: "Stop Game?" });
      expect(backend.stopRunningGame).not.toHaveBeenCalled();
      fireEvent.click(within(confirm).getByRole("button", { name: "Stop Game" }));

      await waitFor(() => expect(backend.stopRunningGame).toHaveBeenCalledWith(100));
      expect(mockRunGame).not.toHaveBeenCalled();
    });

    it("stops nothing when the Stop dialog is cancelled", async () => {
      vi.mocked(sessionManager.isSessionActive).mockReturnValue(true);
      render(<PlayButton appId={APP_ID} />);

      fireEvent.click(screen.getByRole("button", { name: /^STOP/ }));
      const confirm = await screen.findByRole("dialog", { name: "Stop Game?" });
      fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(backend.stopRunningGame).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: /^STOP/ })).toBeEnabled();
    });

    it("reads STOPPING... and takes no press while a stop runs", async () => {
      vi.mocked(sessionManager.isSessionActive).mockReturnValue(true);
      vi.mocked(backend.stopRunningGame).mockReturnValue(new Promise(() => {}));
      render(<PlayButton appId={APP_ID} />);

      fireEvent.click(screen.getByRole("button", { name: /^STOP/ }));
      fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop Game" }));

      expect(await screen.findByRole("button", { name: /STOPPING\.\.\./ })).toBeDisabled();
    });
  });

  describe("a session started somewhere other than this button", () => {
    it("turns PLAY into STOP when it starts, and back when it ends", () => {
      render(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();

      sessionChanged(true, 100);
      expect(screen.getByRole("button", { name: /^STOP/ })).toBeInTheDocument();

      sessionChanged(false, 100);
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^STOP/ })).not.toBeInTheDocument();
    });

    it("ignores a session of another ROM", () => {
      render(<PlayButton appId={APP_ID} />);

      sessionChanged(true, 999);

      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^STOP/ })).not.toBeInTheDocument();
    });

    it("stops listening once unmounted", () => {
      const addSpy = vi.spyOn(globalThis, "addEventListener");
      const removeSpy = vi.spyOn(globalThis, "removeEventListener");
      try {
        const { unmount } = render(<PlayButton appId={APP_ID} />);
        const added = addSpy.mock.calls.find(([type]) => type === "romm_session_changed")?.[1];

        unmount();

        expect(added).toBeDefined();
        expect(removeSpy).toHaveBeenCalledWith("romm_session_changed", added);
      } finally {
        addSpy.mockRestore();
        removeSpy.mockRestore();
      }
    });

    it("holds a session it saw for its ROM only, not for the version the page switches to", () => {
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      sessionChanged(true, 100);
      expect(screen.getByRole("button", { name: /^STOP/ })).toBeInTheDocument();

      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ romId: 200 }));
      rerender(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
    });
  });

  describe("RESOLVE CONFLICT", () => {
    const inConflict = () =>
      detailState({
        saveSyncEnabled: true,
        saveStatus: { rom_id: 100, files: [], conflicts: [CONFLICT] } as unknown as SaveStatus,
      });

    beforeEach(() => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(inConflict());
      vi.mocked(backend.getSaveStatus).mockResolvedValue({
        rom_id: 100,
        files: [],
        conflicts: [CONFLICT],
      } as unknown as SaveStatus);
    });

    it("takes Play's place while a conflict is known, and opens the dialog without a sync", async () => {
      render(<PlayButton appId={APP_ID} />);

      expect(screen.queryByRole("button", { name: /^PLAY/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /RESOLVE CONFLICT/ }));

      expect(await screen.findByRole("dialog", { name: "Save conflict for smw.srm" })).toBeInTheDocument();
      expect(backend.getSaveStatus).toHaveBeenCalledWith(100);
      expect(backend.preLaunchSync).not.toHaveBeenCalled();
    });

    it("reads SYNCING SAVES... while it reads the conflict", async () => {
      vi.mocked(backend.getSaveStatus).mockReturnValue(new Promise(() => {}));
      render(<PlayButton appId={APP_ID} />);

      fireEvent.click(screen.getByRole("button", { name: /RESOLVE CONFLICT/ }));

      expect(await screen.findByRole("button", { name: /SYNCING SAVES\.\.\./ })).toBeDisabled();
    });

    it("stays on RESOLVE CONFLICT when the dialog is cancelled", async () => {
      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: /RESOLVE CONFLICT/ }));
      fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(screen.getByRole("button", { name: /RESOLVE CONFLICT/ })).toBeInTheDocument();
      expect(backend.resolveSyncConflict).not.toHaveBeenCalled();
    });

    it("resolves outside a start and switches the button to PLAY", async () => {
      vi.mocked(backend.resolveSyncConflict).mockResolvedValueOnce({ success: true } as never);
      render(<PlayButton appId={APP_ID} />);

      fireEvent.click(screen.getByRole("button", { name: /RESOLVE CONFLICT/ }));
      fireEvent.click(await screen.findByRole("button", { name: "Keep Local" }));

      expect(await screen.findByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
      expect(backend.resolveSyncConflict).toHaveBeenCalledWith(100, "smw.srm", 7, "keep_local", false);
      expect(mockRunGame).not.toHaveBeenCalled();
    });

    it("gives a resolved verdict up to the next save status the page reads", async () => {
      vi.mocked(backend.resolveSyncConflict).mockResolvedValueOnce({ success: true } as never);
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: /RESOLVE CONFLICT/ }));
      fireEvent.click(await screen.findByRole("button", { name: "Keep Local" }));
      await screen.findByRole("button", { name: /^PLAY/ });

      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(inConflict());
      rerender(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: /RESOLVE CONFLICT/ })).toBeInTheDocument();
    });

    it("gives an unresolved verdict up to the next save status the page reads", async () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ saveSyncEnabled: true }));
      vi.mocked(backend.preLaunchSync).mockResolvedValueOnce({
        success: true,
        synced: 0,
        uploaded: 0,
        downloaded: 0,
        conflicts: [CONFLICT],
      } as never);
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      pressPlay();
      fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
      await screen.findByRole("button", { name: /RESOLVE CONFLICT/ });

      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          saveSyncEnabled: true,
          saveStatus: { rom_id: 100, files: [], conflicts: [] } as unknown as SaveStatus,
        }),
      );
      rerender(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
    });

    it("holds a verdict for its ROM only, not for the version the page switches to", async () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ saveSyncEnabled: true }));
      vi.mocked(backend.preLaunchSync).mockResolvedValueOnce({
        success: true,
        synced: 0,
        uploaded: 0,
        downloaded: 0,
        conflicts: [CONFLICT],
      } as never);
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      pressPlay();
      fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
      await screen.findByRole("button", { name: /RESOLVE CONFLICT/ });

      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ romId: 200, saveSyncEnabled: true }));
      rerender(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
    });

    it("shows no conflict of the version the page switched from while the new version's save status is read", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue({ ...inConflict(), romId: 200 });
      render(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /RESOLVE CONFLICT/ })).not.toBeInTheDocument();
    });

    it("holds a conflict cancelled before the new version's save status has landed", async () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          romId: 200,
          saveSyncEnabled: true,
          saveStatus: { rom_id: 100, files: [], conflicts: [] } as unknown as SaveStatus,
        }),
      );
      vi.mocked(backend.preLaunchSync).mockResolvedValueOnce({
        success: true,
        synced: 0,
        uploaded: 0,
        downloaded: 0,
        conflicts: [{ ...CONFLICT, rom_id: 200 }],
      } as never);
      render(<PlayButton appId={APP_ID} />);

      pressPlay();
      fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

      expect(await screen.findByRole("button", { name: /RESOLVE CONFLICT/ })).toBeInTheDocument();
      expect(mockRunGame).not.toHaveBeenCalled();
    });
  });

  describe("a game not on this device", () => {
    const notInstalled = (overrides: Partial<GameDetailState> = {}) =>
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false, ...overrides }));

    it("offers DOWNLOAD, which starts the download and draws its bar until the transfer is under way", async () => {
      notInstalled();
      render(<PlayButton appId={APP_ID} />);

      const download = screen.getByRole("button", { name: "DOWNLOAD" });
      expect(download).toHaveStyle({ background: "linear-gradient(90deg, #1a9fff 0%, #0078d4 100%)" });
      expect(download.style.opacity).toBe("");

      fireEvent.click(download);

      await waitFor(() => expect(backend.startDownload).toHaveBeenCalledWith(100, false, null, null, false));
      expect(await screen.findByRole("progressbar")).toHaveTextContent("0%");
      expect(screen.queryByRole("button", { name: "DOWNLOAD" })).not.toBeInTheDocument();
    });

    it("reads STARTING... and takes no second press while the press's request is in flight", async () => {
      let answer!: (value: unknown) => void;
      vi.mocked(backend.startDownload).mockReturnValueOnce(
        new Promise((resolve) => {
          answer = resolve;
        }) as never,
      );
      notInstalled();
      render(<PlayButton appId={APP_ID} />);

      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      const starting = await screen.findByRole("button", { name: "STARTING..." });
      expect(starting).toBeDisabled();
      fireEvent.click(starting);
      expect(backend.startDownload).toHaveBeenCalledTimes(1);

      await act(async () => answer({ success: true }));
      expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    });

    it("reads OFFLINE and takes no press while RomM cannot be reached", () => {
      setRommConnectionState("offline");
      notInstalled();
      render(<PlayButton appId={APP_ID} />);

      fireEvent.click(screen.getByRole("button", { name: "OFFLINE" }));

      expect(screen.getByRole("button", { name: "OFFLINE" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "OFFLINE" })).toHaveStyle({
        background: "linear-gradient(90deg, #4a5968 0%, #3a4754 100%)",
      });
      expect(backend.startDownload).not.toHaveBeenCalled();
    });

    it("reads No file on RomM and takes no press where RomM holds no file for the game", async () => {
      vi.mocked(backend.getCachedGameDetail).mockResolvedValue({
        found: true,
        rom_id: 100,
        installed: false,
        has_file_on_disk: false,
      });
      notInstalled();
      render(<PlayButton appId={APP_ID} />);

      const button = await screen.findByRole("button", { name: "NO FILE ON ROMM" });
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(backend.startDownload).not.toHaveBeenCalled();
    });

    it("takes no press while the bound version is gone from RomM, and takes one again once it is back", () => {
      setBoundAvailability(APP_ID, "version_gone");
      notInstalled();
      render(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toHaveStyle({ opacity: "0.7" });
      act(() => setBoundAvailability(APP_ID, "live"));
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
    });

    it("asks the cached detail nothing for an installed game, and logs a read that fails", async () => {
      const { unmount } = render(<PlayButton appId={APP_ID} />);
      await act(async () => {});
      expect(backend.getCachedGameDetail).not.toHaveBeenCalled();
      unmount();

      vi.mocked(backend.getCachedGameDetail).mockRejectedValueOnce(new Error("socket closed"));
      notInstalled();
      render(<PlayButton appId={APP_ID} />);
      await waitFor(() =>
        expect(backend.debugLog).toHaveBeenCalledWith("DesktopPlayButton: on-disk read failed: Error: socket closed"),
      );
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
    });

    it("is not held by another ROM's download, or by one that has ended", () => {
      notInstalled();
      vi.mocked(downloadStore.useDownloads).mockReturnValue([
        { rom_id: 999, status: "downloading" } as DownloadItem,
        { rom_id: 100, status: "failed" } as DownloadItem,
      ]);
      render(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    });

    it("says FILE MISSING, with the path as its title, for a download whose file is gone", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ fileMissingAt: "/roms/snes/smw.sfc" }));
      render(<PlayButton appId={APP_ID} />);
      const button = screen.getByRole("button", { name: /^FILE MISSING$/ });
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("title", "/roms/snes/smw.sfc");
      expect(screen.getAllByRole("button")).toHaveLength(1);
    });
  });

  describe("a download in flight", () => {
    const transfer = (overrides: Partial<DownloadItem> = {}): DownloadItem => ({
      rom_id: 100,
      rom_name: "Super Mario World",
      platform_name: "snes",
      file_name: "smw.sfc",
      status: "downloading",
      progress: 0.5,
      bytes_downloaded: 1024,
      total_bytes: 2048,
      resumable: true,
      ...overrides,
    });

    beforeEach(() => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
    });

    it.each(["queued", "downloading", "paused", "extracting"] as const)(
      "draws the transfer's bar while its download is %s",
      (status) => {
        vi.mocked(downloadStore.useDownloads).mockReturnValue([transfer({ status })]);
        render(<PlayButton appId={APP_ID} />);
        expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
        expect(screen.queryByRole("button", { name: "DOWNLOAD" })).not.toBeInTheDocument();
      },
    );

    it("cancels the download and offers DOWNLOAD again", async () => {
      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      fireEvent.click(await screen.findByRole("button", { name: "Cancel download" }));

      expect(backend.cancelDownload).toHaveBeenCalledWith(100);
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
    });

    it.each([
      ["Cancel download", "cancel", "cancelDownload"],
      ["Pause download", "pause", "pauseDownload"],
    ] as const)("logs a %s press the backend fails", async (label, what, call) => {
      vi.mocked(downloadStore.useDownloads).mockReturnValue([transfer()]);
      vi.mocked(backend[call]).mockRejectedValueOnce(new Error("socket closed"));
      render(<PlayButton appId={APP_ID} />);

      fireEvent.click(screen.getByRole("button", { name: label }));

      await waitFor(() =>
        expect(backend.debugLog).toHaveBeenCalledWith(`DesktopPlayButton: ${what} failed: Error: socket closed`),
      );
    });

    it("pauses a running download, and resumes a paused one", () => {
      vi.mocked(downloadStore.useDownloads).mockReturnValue([transfer()]);
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "Pause download" }));
      expect(backend.pauseDownload).toHaveBeenCalledWith(100);

      vi.mocked(downloadStore.useDownloads).mockReturnValue([transfer({ status: "paused" })]);
      rerender(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "Resume download" }));
      expect(backend.resumeDownload).toHaveBeenCalledWith(100);
    });

    it("says why a resume was refused when something now sits at the game's location", async () => {
      vi.mocked(downloadStore.useDownloads).mockReturnValue([transfer({ status: "paused" })]);
      vi.mocked(backend.resumeDownload).mockResolvedValueOnce({ success: false, reason: "target_occupied" } as never);
      render(<PlayButton appId={APP_ID} />);

      fireEvent.click(screen.getByRole("button", { name: "Resume download" }));

      await waitFor(() => expect(toast.showToast).toHaveBeenCalledWith(RESUME_TARGET_OCCUPIED_TOAST));
    });

    it.each(["cancelled", "failed"] as const)(
      "offers DOWNLOAD again when a download the button started is %s elsewhere",
      async (status) => {
        render(<PlayButton appId={APP_ID} />);
        fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
        await screen.findByRole("progressbar");

        act(() => emitHostEvent<Partial<DownloadProgressEvent>>("download_progress", { rom_id: 100, status }));

        expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
      },
    );

    it("offers DOWNLOAD again when the download fails, leaving the toast to the panel's own listener", async () => {
      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("progressbar");

      act(() =>
        emitHostEvent<Partial<DownloadFailedEvent>>("download_failed", { rom_id: 100, error_message: "disk full" }),
      );

      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
      expect(toast.showToast).not.toHaveBeenCalled();
    });

    it("hands the bar over to the download store once the transfer's frames arrive", async () => {
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("progressbar");

      act(() =>
        emitHostEvent<Partial<DownloadProgressEvent>>("download_progress", { rom_id: 100, status: "downloading" }),
      );
      vi.mocked(downloadStore.useDownloads).mockReturnValue([]);
      rerender(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
    });

    it("changes nothing for another ROM's frames", async () => {
      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("progressbar");

      act(() => {
        emitHostEvent<Partial<DownloadProgressEvent>>("download_progress", { rom_id: 999, status: "cancelled" });
        emitHostEvent<Partial<DownloadFailedEvent>>("download_failed", { rom_id: 999 });
        emitHostEvent<Partial<DownloadCompleteEvent>>("download_complete", { rom_id: 999 });
      });

      expect(screen.getByRole("progressbar")).toBeInTheDocument();
    });

    it("reads READY! once the download completes, then holds PLAY, writing no launch command and toasting nothing", () => {
      vi.useFakeTimers();
      try {
        vi.mocked(downloadStore.useDownloads).mockReturnValue([transfer()]);
        const { rerender } = render(<PlayButton appId={APP_ID} />);

        act(() => emitHostEvent<Partial<DownloadCompleteEvent>>("download_complete", { rom_id: 100 }));
        vi.mocked(downloadStore.useDownloads).mockReturnValue([]);
        rerender(<PlayButton appId={APP_ID} />);
        expect(screen.getByRole("button", { name: "READY!" })).toBeDisabled();

        act(() => {
          vi.advanceTimersByTime(1099);
        });
        expect(screen.getByRole("button", { name: "READY!" })).toBeInTheDocument();
        act(() => {
          vi.advanceTimersByTime(1);
        });
        expect(screen.getByRole("button", { name: /^PLAY/ })).toBeEnabled();
        expect(steamShortcuts.setLaunchOptionsConfirmed).not.toHaveBeenCalled();
        expect(toast.showToast).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    });

    it("stands READY! from the latest completion when a second one lands", () => {
      vi.useFakeTimers();
      try {
        render(<PlayButton appId={APP_ID} />);
        act(() => emitHostEvent<Partial<DownloadCompleteEvent>>("download_complete", { rom_id: 100 }));
        act(() => {
          vi.advanceTimersByTime(600);
        });
        act(() => emitHostEvent<Partial<DownloadCompleteEvent>>("download_complete", { rom_id: 100 }));
        act(() => {
          vi.advanceTimersByTime(600);
        });
        expect(screen.getByRole("button", { name: "READY!" })).toBeInTheDocument();
        act(() => {
          vi.advanceTimersByTime(500);
        });
        expect(screen.queryByRole("button", { name: "READY!" })).not.toBeInTheDocument();
      } finally {
        vi.useRealTimers();
      }
    });

    it("leaves no READY! timer behind once unmounted", () => {
      vi.useFakeTimers();
      try {
        const { unmount } = render(<PlayButton appId={APP_ID} />);
        act(() => emitHostEvent<Partial<DownloadCompleteEvent>>("download_complete", { rom_id: 100 }));
        expect(vi.getTimerCount()).toBeGreaterThan(0);

        unmount();

        expect(vi.getTimerCount()).toBe(0);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("Uninstall", () => {
    const openMenu = () => fireEvent.click(screen.getByRole("button", { name: "Game Options" }));

    it("removes the game from the chevron's menu, reading UNINSTALLING... until it is gone", async () => {
      let removed!: (value: unknown) => void;
      vi.mocked(backend.removeRom).mockReturnValueOnce(
        new Promise((resolve) => {
          removed = resolve;
        }) as never,
      );
      render(<PlayButton appId={APP_ID} />);

      openMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Uninstall" }));

      expect(await screen.findByRole("button", { name: "UNINSTALLING..." })).toBeDisabled();
      expect(backend.removeRom).toHaveBeenCalledWith(100);
      await act(async () => removed({ success: true, message: "", prune_lease_token: null }));
      expect(await screen.findByRole("button", { name: "DOWNLOAD" })).toBeInTheDocument();
    });

    it("counts the files a multi-file removal has taken, as Big Picture's does", async () => {
      vi.mocked(backend.removeRom).mockReturnValueOnce(new Promise(() => {}) as never);
      render(<PlayButton appId={APP_ID} />);
      openMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Uninstall" }));
      await screen.findByRole("button", { name: "UNINSTALLING..." });

      act(() => {
        emitHostEvent<UninstallProgressEvent>("uninstall_progress", { rom_id: 100, files_removed: 3, files_total: 10 });
        emitHostEvent<UninstallProgressEvent>("uninstall_progress", { rom_id: 999, files_removed: 9, files_total: 9 });
      });

      expect(screen.getByRole("button", { name: "UNINSTALLING 3/10" })).toBeDisabled();
    });

    it("holds DOWNLOAD only until the page's detail moves on", async () => {
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      openMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Uninstall" }));
      await screen.findByRole("button", { name: "DOWNLOAD" });

      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      rerender(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeInTheDocument();

      // Downloaded again from elsewhere: the page's detail now says installed.
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState());
      rerender(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
    });

    it("starts a later removal's count afresh", async () => {
      let refuse!: (value: unknown) => void;
      vi.mocked(backend.removeRom)
        .mockReturnValueOnce(
          new Promise((resolve) => {
            refuse = resolve;
          }) as never,
        )
        .mockReturnValueOnce(new Promise(() => {}) as never);
      render(<PlayButton appId={APP_ID} />);
      openMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Uninstall" }));
      act(() =>
        emitHostEvent<UninstallProgressEvent>("uninstall_progress", { rom_id: 100, files_removed: 3, files_total: 10 }),
      );
      await act(async () => refuse({ success: false, message: "In use" }));

      openMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Uninstall" }));

      expect(await screen.findByRole("button", { name: "UNINSTALLING..." })).toBeInTheDocument();
    });

    it("comes back to PLAY when the removal is refused", async () => {
      vi.mocked(backend.removeRom).mockResolvedValueOnce({ success: false, message: "In use" } as never);
      render(<PlayButton appId={APP_ID} />);

      openMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Uninstall" }));

      expect(await screen.findByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
      expect(toast.showToast).toHaveBeenCalledWith("In use");
    });
  });

  describe("a version switch while a press is under way", () => {
    const otherVersion = () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ romId: 101 }));
    };

    it("leaves an uninstall's state with the version it was pressed for", async () => {
      let removed!: (value: unknown) => void;
      vi.mocked(backend.removeRom).mockReturnValueOnce(
        new Promise((resolve) => {
          removed = resolve;
        }) as never,
      );
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "Game Options" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Uninstall" }));
      act(() =>
        emitHostEvent<UninstallProgressEvent>("uninstall_progress", { rom_id: 100, files_removed: 3, files_total: 10 }),
      );
      await screen.findByRole("button", { name: "UNINSTALLING 3/10" });

      otherVersion();
      rerender(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeEnabled();

      await act(async () => removed({ success: true, message: "", prune_lease_token: null }));
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "DOWNLOAD" })).not.toBeInTheDocument();
    });

    it("leaves a download press's state with the version it was pressed for", async () => {
      let started!: (value: unknown) => void;
      vi.mocked(backend.startDownload).mockReturnValueOnce(
        new Promise((resolve) => {
          started = resolve;
        }) as never,
      );
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("button", { name: "STARTING..." });

      // Another version that is not downloaded either: its own DOWNLOAD, not the press's STARTING....
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ romId: 101, installed: false }));
      rerender(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();

      await act(async () => started({ success: true }));
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

      // Back on the version it was pressed for, a transfer that ended meanwhile leaves no bar behind.
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      rerender(<PlayButton appId={APP_ID} />);
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();

      // One the download store still carries is drawn from the store.
      vi.mocked(downloadStore.useDownloads).mockReturnValue([{ rom_id: 100, status: "downloading" } as DownloadItem]);
      rerender(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
    });

    it("holds no PLAY for an adoption the page has switched away from", async () => {
      const occupied = {
        success: false,
        reason: "target_occupied",
        message: "",
        existing: { name: "smw.sfc", path: "/roms/snes/smw.sfc", kind: "file", size_bytes: 2048, modified_at: 0 },
        incoming: { name: "smw.sfc", size_bytes: 2048 },
        sizes_match: true,
        adoptable: true,
      };
      let adopted!: (value: unknown) => void;
      vi.mocked(backend.startDownload).mockResolvedValueOnce(occupied as never);
      vi.mocked(backend.adoptExistingRom).mockReturnValueOnce(
        new Promise((resolve) => {
          adopted = resolve;
        }) as never,
      );
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      fireEvent.click(await screen.findByRole("button", { name: "Use These Files" }));

      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ romId: 101, installed: false }));
      rerender(<PlayButton appId={APP_ID} />);
      await act(async () => adopted({ success: true, message: "" }));

      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeEnabled();
    });
  });

  describe("content already on the device", () => {
    const OCCUPIED: TargetOccupiedResult = {
      success: false,
      reason: "target_occupied",
      message: "'smw.sfc' is already on this device",
      existing: { name: "smw.sfc", path: "/roms/snes/smw.sfc", kind: "file", size_bytes: 2048, modified_at: 0 },
      incoming: { name: "smw.sfc", size_bytes: 2048 },
      sizes_match: true,
      adoptable: true,
    };

    const CANDIDATE_A: AdoptionCandidate = {
      name: "Super Mario World (USA).sfc",
      path: "/roms/snes/Super Mario World (USA).sfc",
      is_dir: false,
      size_bytes: 2048,
      modified_at: 0,
      evidence: "crc32",
      detail: "Same checksum as the server's copy",
    };
    const CANDIDATE_B: AdoptionCandidate = {
      ...CANDIDATE_A,
      name: "smw-hack.sfc",
      path: "/roms/snes/smw-hack.sfc",
      evidence: "name",
      detail: "Carries this game's name",
    };

    const ADOPTED: AdoptResult = { success: true, message: "" };

    beforeEach(() => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
    });

    it("opens the comparison on target_occupied, and adopting writes no launch command and holds PLAY", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED as never);
      vi.mocked(backend.adoptExistingRom).mockResolvedValueOnce(ADOPTED);

      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));

      const dialog = await screen.findByRole("dialog", { name: "This Game Is Already on Your Device" });
      expect(dialog).toHaveTextContent("Both are the same size.");
      fireEvent.click(screen.getByRole("button", { name: "Use These Files" }));

      expect(await screen.findByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
      expect(backend.adoptExistingRom).toHaveBeenCalledWith(100, "/roms/snes/smw.sfc", null);
      expect(steamShortcuts.setLaunchOptionsConfirmed).not.toHaveBeenCalled();
      expect(toast.showToast).toHaveBeenCalledWith("Super Mario World is ready to play");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("Download Instead asks a second time, and Delete and Download re-sends with replace", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED as never);

      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      fireEvent.click(await screen.findByRole("button", { name: "Download Instead" }));

      expect(screen.getByText(/Downloading deletes the file that is here now — smw\.sfc/)).toBeInTheDocument();
      expect(backend.startDownload).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "Delete and Download" }));

      await waitFor(() =>
        expect(backend.startDownload).toHaveBeenLastCalledWith(100, true, null, null, false, ["/roms/snes/smw.sfc"]),
      );
      expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    });

    it("dismissing the comparison with Escape leaves the button offering the files it found", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED as never);

      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("dialog");
      fireEvent.keyDown(window, { key: "Escape" });

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(screen.getByRole("button", { name: "USE EXISTING FILES" })).toBeEnabled();
      expect(backend.adoptExistingRom).not.toHaveBeenCalled();
      expect(backend.startDownload).toHaveBeenCalledTimes(1);
    });

    it("keeps the files it found through a frame that is neither a cancel nor a failure", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED as never);
      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("dialog");
      fireEvent.keyDown(window, { key: "Escape" });
      const existing = await screen.findByRole("button", { name: "USE EXISTING FILES" });
      expect(existing).toHaveStyle({ fontSize: "13px" });

      act(() => emitHostEvent<Partial<DownloadProgressEvent>>("download_progress", { rom_id: 100, status: "queued" }));

      expect(screen.getByRole("button", { name: "USE EXISTING FILES" })).toBeInTheDocument();
    });

    it("forgets the files it found once a download fails", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED as never);
      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("dialog");
      fireEvent.keyDown(window, { key: "Escape" });
      await screen.findByRole("button", { name: "USE EXISTING FILES" });

      act(() => emitHostEvent<Partial<DownloadFailedEvent>>("download_failed", { rom_id: 100 }));

      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toHaveStyle({ fontSize: "15px" });
    });

    it("takes a second press once the comparison was dismissed", async () => {
      vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED as never);
      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("dialog");
      fireEvent.keyDown(window, { key: "Escape" });

      fireEvent.click(await screen.findByRole("button", { name: "USE EXISTING FILES" }));

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(backend.startDownload).toHaveBeenCalledTimes(2);
    });

    it("forgets the files it found when the replacing download is cancelled from its bar", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED as never);
      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      fireEvent.click(await screen.findByRole("button", { name: "Download Instead" }));
      fireEvent.click(screen.getByRole("button", { name: "Delete and Download" }));
      fireEvent.click(await screen.findByRole("button", { name: "Cancel download" }));

      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeInTheDocument();
    });

    it("forgets the files it found once a cancelled download may have removed them", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED as never);
      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("dialog");
      fireEvent.keyDown(window, { key: "Escape" });
      await screen.findByRole("button", { name: "USE EXISTING FILES" });

      act(() =>
        emitHostEvent<Partial<DownloadProgressEvent>>("download_progress", { rom_id: 100, status: "cancelled" }),
      );

      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeInTheDocument();
    });

    it("lists two candidates, compares the one picked, and adopts it by its path", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce({
        success: false,
        reason: "adoption_candidates",
        message: "",
        incoming: { name: "smw.sfc", size_bytes: 2048 },
        candidates: [CANDIDATE_A, CANDIDATE_B],
        truncated: false,
      } as never);
      vi.mocked(backend.adoptExistingRom).mockResolvedValueOnce(ADOPTED);

      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));

      await screen.findByRole("dialog", { name: "This Game May Already Be on Your Device" });
      fireEvent.click(screen.getByRole("button", { name: /smw-hack\.sfc/ }));

      await screen.findByRole("dialog", { name: "This Game Is Already on Your Device" });
      expect(screen.getByText(/Using it renames it to smw\.sfc/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Use These Files" }));

      await waitFor(() => expect(backend.adoptExistingRom).toHaveBeenCalledWith(100, "/roms/snes/smw-hack.sfc", null));
    });

    it("asks once about taken names and re-sends the adopt with the answer", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED as never);
      vi.mocked(backend.adoptExistingRom)
        .mockResolvedValueOnce({
          success: false,
          reason: "rename_collisions",
          message: "",
          collisions: [{ name: "smw.srm", path: "/saves/smw.srm", kind: "save" }],
        } as never)
        .mockResolvedValueOnce(ADOPTED);

      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      fireEvent.click(await screen.findByRole("button", { name: "Use These Files" }));

      const collisions = await screen.findByRole("dialog", { name: "Some of These Names Are Taken" });
      expect(collisions).toHaveTextContent("smw.srm (save)");
      fireEvent.click(screen.getByRole("button", { name: "Replace Them" }));

      await waitFor(() =>
        expect(backend.adoptExistingRom).toHaveBeenLastCalledWith(100, "/roms/snes/smw.sfc", "overwrite"),
      );
      expect(await screen.findByRole("button", { name: /^PLAY/ })).toBeInTheDocument();
    });

    it("labels the button USE EXISTING FILES from the cached detail, and reports a seen candidate on the press", async () => {
      vi.mocked(backend.getCachedGameDetail).mockResolvedValue({
        found: true,
        rom_id: 100,
        installed: false,
        adoption_candidate_present: true,
      });

      render(<PlayButton appId={APP_ID} />);
      fireEvent.click(await screen.findByRole("button", { name: "USE EXISTING FILES" }));

      await waitFor(() => expect(backend.startDownload).toHaveBeenCalledWith(100, false, null, null, true));
    });

    it("keeps DOWNLOAD when the cached detail found nothing", async () => {
      vi.mocked(backend.getCachedGameDetail).mockResolvedValueOnce({
        found: true,
        rom_id: 100,
        installed: false,
        target_path_occupied: false,
        adoption_candidate_present: false,
        has_file_on_disk: true,
      });

      render(<PlayButton appId={APP_ID} />);
      await waitFor(() => expect(backend.getCachedGameDetail).toHaveBeenCalledWith(APP_ID));
      await act(async () => {});
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toHaveTextContent(/^DOWNLOAD$/);
    });

    it("ignores a cached answer read before the game was installed that lands after a newer one", async () => {
      const answers: ((value: unknown) => void)[] = [];
      vi.mocked(backend.getCachedGameDetail).mockImplementation(
        () =>
          new Promise((resolve) => {
            answers.push(resolve);
          }) as never,
      );
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState());
      rerender(<PlayButton appId={APP_ID} />);
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      rerender(<PlayButton appId={APP_ID} />);
      expect(answers).toHaveLength(2);

      await act(async () => answers[1]?.({ found: true, rom_id: 100, installed: false }));
      await act(async () => answers[0]?.({ found: true, rom_id: 100, installed: false, target_path_occupied: true }));

      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeInTheDocument();
    });

    it("reads no other ROM's cached answer", async () => {
      vi.mocked(backend.getCachedGameDetail).mockResolvedValueOnce({
        found: true,
        rom_id: 999,
        installed: false,
        target_path_occupied: true,
      });

      render(<PlayButton appId={APP_ID} />);
      await waitFor(() => expect(backend.getCachedGameDetail).toHaveBeenCalledWith(APP_ID));
      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toHaveTextContent(/^DOWNLOAD$/);
    });

    it("retires what it found once the page switches to another version", async () => {
      vi.mocked(backend.getCachedGameDetail).mockResolvedValueOnce({
        found: true,
        rom_id: 100,
        installed: false,
        target_path_occupied: true,
      });
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      await screen.findByRole("button", { name: "USE EXISTING FILES" });

      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ romId: 101, installed: false }));
      rerender(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: "DOWNLOAD" })).toBeInTheDocument();
    });

    it("tells the selector a press is under way through the request and the question, until the flow ends", async () => {
      const pressUnderWay = () => vi.mocked(DiscSelector).mock.lastCall?.[0].pressUnderWay;
      let answer!: (value: never) => void;
      vi.mocked(backend.startDownload).mockReturnValueOnce(
        new Promise<never>((resolve) => {
          answer = resolve;
        }),
      );
      render(<PlayButton appId={APP_ID} />);
      expect(pressUnderWay()).toBe(false);

      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("button", { name: "STARTING..." });
      expect(pressUnderWay()).toBe(true);

      await act(async () => answer(OCCUPIED as never));
      await screen.findByRole("dialog");
      expect(pressUnderWay()).toBe(true);

      fireEvent.keyDown(window, { key: "Escape" });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(pressUnderWay()).toBe(false);
    });

    it("unmounting with the comparison open ends the flow at its cancel exit", async () => {
      vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED as never);

      const { unmount } = render(<PlayButton appId={APP_ID} />);
      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
      await screen.findByRole("dialog");
      unmount();

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await act(async () => {});
      expect(backend.startDownload).toHaveBeenCalledTimes(1);
      expect(backend.adoptExistingRom).not.toHaveBeenCalled();
    });
  });

  describe("the disc and version selector", () => {
    const selectorProps = () => vi.mocked(DiscSelector).mock.lastCall?.[0];

    it("sits beside PLAY, asking through the button's dialogs", () => {
      render(<PlayButton appId={APP_ID} />);

      const container = screen.getByRole("button", { name: /^PLAY/ }).closest(".tender-desktop-play-btn-container");
      expect(within(container as HTMLElement).getByTestId("disc-selector")).toBeInTheDocument();
      expect(selectorProps()).toEqual({
        appId: APP_ID,
        ask: expect.any(Function),
        fileMissing: false,
        pressUnderWay: false,
        onSwitchingChange: expect.any(Function),
      });
    });

    it("sits beside STOP while the game runs", () => {
      vi.mocked(sessionManager.isSessionActive).mockReturnValue(true);
      render(<PlayButton appId={APP_ID} />);

      expect(screen.getByRole("button", { name: /^STOP/ })).toBeInTheDocument();
      expect(screen.getByTestId("disc-selector")).toBeInTheDocument();
    });

    it("asks its questions in the button's dialog host", () => {
      render(<PlayButton appId={APP_ID} />);
      const ask = selectorProps()!.ask;

      act(() => {
        void ask("dismissed", () => <div role="dialog" aria-label="from the selector" />);
      });

      expect(screen.getByRole("dialog", { name: "from the selector" })).toBeInTheDocument();
    });

    it("stays mounted while the button moves from PLAY to STOP", () => {
      selectorMounts.count = 0;
      render(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeInTheDocument();

      sessionChanged(true, 100);

      expect(screen.getByRole("button", { name: /^STOP/ })).toBeInTheDocument();
      expect(selectorMounts.count).toBe(1);
    });

    it("is told a press is under way while its check runs", async () => {
      vi.mocked(backend.probeReachability).mockReturnValue(new Promise(() => {}));
      render(<PlayButton appId={APP_ID} />);

      pressPlay();

      await screen.findByRole("button", { name: /PREPARING\.\.\./ });
      expect(selectorProps()?.pressUnderWay).toBe(true);
    });

    it("is told the press is over once the start is handed to Steam", async () => {
      render(<PlayButton appId={APP_ID} />);
      pressPlay();
      await waitFor(() => expect(mockRunGame).toHaveBeenCalled());

      expect(screen.getByRole("button", { name: /LAUNCHING\.\.\./ })).toBeDisabled();
      expect(selectorProps()?.pressUnderWay).toBe(false);
    });

    it("disables PLAY and RESOLVE CONFLICT while the selector is switching", () => {
      const { rerender } = render(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeEnabled();

      act(() => selectorProps()!.onSwitchingChange(true));
      expect(screen.getByRole("button", { name: /^PLAY/ })).toBeDisabled();

      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          saveSyncEnabled: true,
          saveStatus: { rom_id: 100, files: [], conflicts: [CONFLICT] } as unknown as SaveStatus,
        }),
      );
      rerender(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: /RESOLVE CONFLICT/ })).toBeDisabled();

      act(() => selectorProps()!.onSwitchingChange(false));
      expect(screen.getByRole("button", { name: /RESOLVE CONFLICT/ })).toBeEnabled();
    });

    it("draws the throbber in PLAY's triangle's place, with no pointer, while the selector is switching", () => {
      render(<PlayButton appId={APP_ID} />);
      const play = () => screen.getByRole("button", { name: /^PLAY/ });
      expect(throbberIn(play())).toBeNull();
      expect(play().querySelectorAll("svg")).toHaveLength(1);
      expect(play()).toHaveStyle({ cursor: "pointer" });

      act(() => selectorProps()!.onSwitchingChange(true));
      expect(throbberIn(play())).not.toBeNull();
      expect(play().querySelectorAll("svg")).toHaveLength(1);
      expect(play()).toHaveStyle({ cursor: "default" });

      act(() => selectorProps()!.onSwitchingChange(false));
      expect(throbberIn(play())).toBeNull();
      expect(play()).toHaveStyle({ cursor: "pointer" });
    });

    it("draws the throbber beside RESOLVE CONFLICT, on one line and with no pointer, while the selector is switching", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          saveSyncEnabled: true,
          saveStatus: { rom_id: 100, files: [], conflicts: [CONFLICT] } as unknown as SaveStatus,
        }),
      );
      render(<PlayButton appId={APP_ID} />);
      const resolve = () => screen.getByRole("button", { name: /RESOLVE CONFLICT/ });
      expect(throbberIn(resolve())).toBeNull();
      expect(resolve()).toHaveStyle({ cursor: "pointer" });

      act(() => selectorProps()!.onSwitchingChange(true));
      expect(throbberIn(resolve())).not.toBeNull();
      expect(resolve()).toHaveStyle({ cursor: "default", whiteSpace: "nowrap" });

      act(() => selectorProps()!.onSwitchingChange(false));
      expect(throbberIn(resolve())).toBeNull();
      expect(resolve()).toHaveStyle({ cursor: "pointer" });
    });

    it("keeps a refused PLAY's cursor while the selector is switching, and its throbber", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          emulatorStart: { state: "cannot_start", reason: "not_installed", entry: "DuckStation" },
          emulators: [{ label: "DuckStation" }] as never,
          activeCoreLabel: "DuckStation",
        }),
      );
      render(<PlayButton appId={APP_ID} />);

      act(() => selectorProps()!.onSwitchingChange(true));

      const play = screen.getByRole("button", { name: /^PLAY/ });
      expect(throbberIn(play)).not.toBeNull();
      expect(play).toHaveStyle({ cursor: "not-allowed" });
    });

    it("disables DOWNLOAD while the selector is switching, with the throbber in its arrow's place and no pointer", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      render(<PlayButton appId={APP_ID} />);
      const download = () => screen.getByRole("button", { name: "DOWNLOAD" });
      expect(download()).toBeEnabled();
      expect(throbberIn(download())).toBeNull();
      expect(download()).toHaveStyle({ cursor: "pointer" });

      act(() => selectorProps()!.onSwitchingChange(true));
      expect(download()).toBeDisabled();
      expect(throbberIn(download())).not.toBeNull();
      expect(download().querySelectorAll("svg")).toHaveLength(1);
      expect(download()).toHaveStyle({ cursor: "default" });

      act(() => selectorProps()!.onSwitchingChange(false));
      expect(download()).toBeEnabled();
      expect(throbberIn(download())).toBeNull();
      expect(download()).toHaveStyle({ cursor: "pointer" });
    });

    it("keeps an OFFLINE DOWNLOAD's cursor while the selector is switching, and its throbber", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      setRommConnectionState("offline");
      render(<PlayButton appId={APP_ID} />);

      act(() => selectorProps()!.onSwitchingChange(true));

      const download = screen.getByRole("button", { name: "OFFLINE" });
      expect(throbberIn(download)).not.toBeNull();
      expect(download).toHaveStyle({ cursor: "not-allowed" });
    });

    it("is told the press is over once the download has started", async () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      render(<PlayButton appId={APP_ID} />);

      fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));

      await waitFor(() => expect(backend.startDownload).toHaveBeenCalled());
      await waitFor(() => expect(selectorProps()?.pressUnderWay).toBe(false));
    });

    it("is told the file is missing", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ fileMissingAt: "/roms/snes/smw.sfc" }));
      render(<PlayButton appId={APP_ID} />);

      expect(selectorProps()?.fileMissing).toBe(true);
    });
  });

  describe("on a stranded panel", () => {
    it("takes STOP away while the game runs", () => {
      vi.mocked(sessionManager.isSessionActive).mockReturnValue(true);
      const { container } = render(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: /^STOP/ })).toBeInTheDocument();

      act(() => setStrandedAnswer("reloads"));

      expect(container).toBeEmptyDOMElement();
    });

    it.each([
      ["installed", detailState()],
      ["not downloaded", detailState({ installed: false })],
    ])("offers no button for a game that does not run (%s)", (_label, detail) => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detail);
      const { container } = render(<PlayButton appId={APP_ID} />);

      act(() => setStrandedAnswer("restart_steam"));

      expect(container).toBeEmptyDOMElement();
    });
  });
});
