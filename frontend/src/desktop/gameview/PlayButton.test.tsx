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
import { setStrandedAnswer } from "../../test-utils/stranded-panel";
import { emitHostEvent } from "../../test-utils/host-event-bus";
import { stubAppStore } from "../../test-utils/steamStubs";
import type { GameDetailState } from "../../utils/gameDetailStore";
import type { DownloadItem, GameLaunch, PreLaunchSyncStartedEvent, SaveStatus, SyncConflict } from "../../types";
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
    getRomRelaunchOptions: vi.fn(),
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
    fsSizeBytes: 2048,
    saveSyncEnabled: false,
    saveStatus: null,
    saveSyncStatus: null,
    saveSyncLabel: "",
    savefilesInContentDir: false,
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
    vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: true, active_slot: "slot1" });
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
    vi.mocked(backend.getRomRelaunchOptions).mockResolvedValue(null);
    vi.mocked(backend.stopRunningGame).mockResolvedValue({ success: true, stopped: 1, force_killed: 0 } as never);
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
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: false, active_slot: null });
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

    it("starts the game where a slot is the user's to choose, asking and toasting nothing", async () => {
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: false, active_slot: null });
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
          emulators: [
            { label: "DuckStation", bakeable: true },
            { label: "Beetle PSX", bakeable: true },
          ] as never,
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

    it("takes the file's refusal over an entry Tender cannot start yet", () => {
      const launch = notALaunchFile();
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({
          emulatorStart: { state: "cannot_start", reason: "not_bakeable", entry: "Snes9x" },
          gameLaunch: launch,
          startCheck: { state: "answered" },
        }),
      );
      render(<PlayButton appId={APP_ID} />);

      const play = screen.getByRole("button", { name: /^PLAY/ });
      expect(play).toBeDisabled();
      expect(play).toHaveAttribute("title", launchRefusal(launch, false));
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

    it("gives way to NOT INSTALLED and to RESOLVE CONFLICT while the page checks", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(
        detailState({ installed: false, startCheck: { state: "checking" } }),
      );
      const { unmount } = render(<PlayButton appId={APP_ID} />);
      expect(screen.getByRole("button", { name: "NOT INSTALLED" })).toBeDisabled();
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
  });

  describe("a game that cannot be played from here yet", () => {
    const placeholder = (name: RegExp) => {
      const button = screen.getByRole("button", { name });
      expect(button).toBeDisabled();
      expect(screen.getAllByRole("button")).toHaveLength(1);
      return button;
    };

    it("says NOT INSTALLED for a game that is not downloaded", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      render(<PlayButton appId={APP_ID} />);
      placeholder(/^NOT INSTALLED$/);
    });

    it.each(["queued", "downloading", "paused", "extracting"] as const)(
      "says DOWNLOADING... while its download is %s",
      (status) => {
        vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
        vi.mocked(downloadStore.useDownloads).mockReturnValue([{ rom_id: 100, status } as DownloadItem]);
        render(<PlayButton appId={APP_ID} />);
        placeholder(/^DOWNLOADING\.\.\.$/);
      },
    );

    it("is not held by another ROM's download, or by one that has ended", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ installed: false }));
      vi.mocked(downloadStore.useDownloads).mockReturnValue([
        { rom_id: 999, status: "downloading" } as DownloadItem,
        { rom_id: 100, status: "failed" } as DownloadItem,
      ]);
      render(<PlayButton appId={APP_ID} />);
      placeholder(/^NOT INSTALLED$/);
    });

    it("says FILE MISSING, with the path as its title, for a download whose file is gone", () => {
      vi.mocked(gameDetailStore.useGameDetail).mockReturnValue(detailState({ fileMissingAt: "/roms/snes/smw.sfc" }));
      render(<PlayButton appId={APP_ID} />);
      expect(placeholder(/^FILE MISSING$/)).toHaveAttribute("title", "/roms/snes/smw.sfc");
    });
  });

  describe("the disc and version selector", () => {
    const selectorProps = () => vi.mocked(DiscSelector).mock.lastCall?.[0];

    it("sits beside PLAY, asking through the button's dialogs", () => {
      render(<PlayButton appId={APP_ID} />);

      const container = screen.getByRole("button", { name: /^PLAY/ }).closest(".tender-desktop-play-btn-container");
      expect(within(container as HTMLElement).getByTestId("disc-selector")).toBeInTheDocument();
      expect(selectorProps()).toEqual({ appId: APP_ID, ask: expect.any(Function), fileMissing: false });
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
