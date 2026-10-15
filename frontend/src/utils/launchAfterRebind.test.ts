import { describe, it, expect, vi, beforeEach } from "vitest";
import * as backend from "../api/backend";
import { emitHostEvent } from "../test-utils/host-event-bus";
import * as rommAppIds from "./rommAppIds";
import * as launchGate from "./launchGate";
import * as steamShortcuts from "./steamShortcuts";
import { ADOPTION_POLL_MAX_MS, initSessionManager } from "./sessionManager";
import { registerLaunchInterceptor, type LaunchPrompts } from "./launchInterceptor";
import { applyCommittedVersionSwitch } from "./versionSwitchApplication";

// The real interceptor over the real session manager and the real launch gate:
// only the backend's answers and Steam's shortcut write are stubbed, so the ROM
// the start is gated for is the one the session manager's map names.
vi.mock("../api/backend", () => ({
  getAppIdRomIdMap: vi.fn(),
  recordSessionStart: vi.fn(),
  finalizeGameSession: vi.fn(),
  isEndpointFailure: vi.fn(() => false),
  refreshMigrationState: vi.fn(),
  getInstalledRom: vi.fn(),
  checkStartSource: vi.fn(),
  getCachedGameDetail: vi.fn(),
  isSaveTrackingConfigured: vi.fn(),
  getSaveSetupInfo: vi.fn(),
  confirmSlotChoice: vi.fn(),
  checkCoreChange: vi.fn(),
  probeReachability: vi.fn(),
  preLaunchSync: vi.fn(),
  checkLocalDrift: vi.fn(),
  getRomRelaunchOptions: vi.fn(),
  getVersionList: vi.fn(),
  fetchCoverBase64: vi.fn(),
  invalidateCachedGameDetail: vi.fn(),
  releaseOrphanedPruneLeases: vi.fn(() => Promise.resolve({ success: true, released: 0 })),
  releasePruneConflictLease: vi.fn(),
  renewPruneConflictLease: vi.fn(),
  logInfo: vi.fn(),
  logWarn: vi.fn(),
  logError: vi.fn(),
  debugLog: vi.fn(),
}));

vi.mock("./steamShortcuts", () => ({
  setLaunchOptionsConfirmed: vi.fn().mockResolvedValue(true),
}));

vi.mock("./migrationStore", () => ({
  getMigrationState: vi.fn(() => ({ pending: false })),
  setMigrationStatus: vi.fn(),
}));

vi.mock("./metadataPatches", () => ({ updatePlaytimeDisplay: vi.fn() }));

const prompts: LaunchPrompts = {
  confirmCoreChange: vi.fn(),
  resolveConflicts: vi.fn(),
  askOfflineDrift: vi.fn(),
  confirmFallbackLaunch: vi.fn(),
  confirmUncheckedSource: vi.fn(),
};

const APP_ID = 3000000001;
const GAME_ID = "12884901892328521728";
/** The version the shortcut was bound to when the panel loaded. */
const OLD_ROM = 41;
/** The version the binding moved onto in the same session. */
const NEW_ROM = 42;

type GameActionHandler = (gameActionId: number, gameId: string, action: string, launchSource: number) => void;

const installRecord = (romId: number) => ({
  rom_id: romId,
  file_name: "g.rom",
  file_path: `/p/${romId}.rom`,
  system: "snes",
  platform_slug: "snes",
  installed_at: "2026-01-01T00:00:00Z",
  launchable: true,
});

const flush = () => new Promise<void>((r) => setTimeout(r, 0));

/** A library sync run's terminal frame, which every run ends with. */
const syncRunEnded = (stage: "done" | "cancelled" | "error") => async () => {
  emitHostEvent("sync_progress", { running: false, stage, current: 0, total: 0, message: "" });
};

/** The ways a binding moves in the running session (D10), each as the panel learns of it. */
const BINDING_MOVES = {
  "a library sync that completed": syncRunEnded("done"),
  "a library sync that was cancelled or paused": syncRunEnded("cancelled"),
  "a library sync that stopped with an error": syncRunEnded("error"),
  "a version switch": async () => {
    await applyCommittedVersionSwitch({
      success: true,
      app_id: APP_ID,
      rom_id: NEW_ROM,
      target_installed: true,
      launch_options: "new-command",
      prune_lease_token: "",
    });
  },
} as const;

/**
 * The panel loads the map with the shortcut bound to OLD_ROM; the binding then
 * moves onto NEW_ROM, so the backend's map answers NEW_ROM from then on, and the
 * panel hears of the move the way it does in production. No app starts in
 * between, which is the other occasion the map is read.
 */
async function loadMapThenMove(move: () => Promise<void>): Promise<GameActionHandler> {
  vi.mocked(backend.getAppIdRomIdMap).mockResolvedValueOnce({ [String(APP_ID)]: OLD_ROM });
  vi.useFakeTimers();
  const init = initSessionManager();
  await vi.advanceTimersByTimeAsync(ADOPTION_POLL_MAX_MS);
  await init;
  vi.useRealTimers();
  vi.mocked(backend.getAppIdRomIdMap).mockResolvedValue({ [String(APP_ID)]: NEW_ROM });

  await move();
  await flush();

  registerLaunchInterceptor(prompts);
  const calls = vi.mocked(SteamClient.Apps.RegisterForGameActionStart).mock.calls;
  return calls[calls.length - 1]?.[0] as GameActionHandler;
}

describe.each(Object.entries(BINDING_MOVES))(
  "Steam's Play after %s moved a shortcut onto another version",
  (_label, move) => {
    beforeEach(() => {
      vi.clearAllMocks();
      launchGate.consumeLaunchSkip(APP_ID);
      vi.stubGlobal("SteamClient", {
        Apps: {
          RegisterForGameActionStart: vi.fn(() => ({ unregister: vi.fn() })),
          CancelGameAction: vi.fn(),
          RunGame: vi.fn(),
          SetCustomArtworkForApp: vi.fn(),
        },
        GameSessions: { RegisterForAppLifetimeNotifications: vi.fn(() => ({ unregister: vi.fn() })) },
      });
      vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(() => ({ GetGameID: () => GAME_ID })) });
      vi.stubGlobal("SteamUIStore", { RunningApps: [] });
      rommAppIds.registerRomMAppId(APP_ID);

      vi.mocked(backend.refreshMigrationState).mockResolvedValue({
        retrodeck: { pending: false },
      } as unknown as Awaited<ReturnType<typeof backend.refreshMigrationState>>);
      vi.mocked(backend.checkStartSource).mockResolvedValue({ switched_off: null });
      vi.mocked(backend.isSaveTrackingConfigured).mockResolvedValue({ configured: true } as never);
      vi.mocked(backend.checkCoreChange).mockResolvedValue({ changed: false });
      vi.mocked(backend.probeReachability).mockResolvedValue({ online: false } as never);
      vi.mocked(backend.checkLocalDrift).mockResolvedValue({ drifted: false } as never);
      vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "" } as never);
      vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
      vi.mocked(backend.renewPruneConflictLease).mockResolvedValue({ success: true, message: "renewed" });
      // The old row is unbound after the move, so the backend has no launch
      // command for it (`RelaunchOptionsResolver._bound_install` answers None).
      vi.mocked(backend.getRomRelaunchOptions).mockImplementation(async (romId: number) =>
        romId === NEW_ROM
          ? { success: true, app_id: APP_ID, launch_options: "new-command", prune_lease_token: "lease" }
          : null,
      );
    });

    it("re-confirms the new version's launch command, never the old one's, when both are installed", async () => {
      vi.mocked(backend.getInstalledRom).mockImplementation(async (romId: number) => installRecord(romId));
      const handler = await loadMapThenMove(move);
      vi.mocked(steamShortcuts.setLaunchOptionsConfirmed).mockClear();

      handler(7, GAME_ID, "LaunchApp", 100);
      await flush();

      expect(steamShortcuts.setLaunchOptionsConfirmed).toHaveBeenCalledWith(APP_ID, "new-command");
      expect(backend.getRomRelaunchOptions).not.toHaveBeenCalledWith(OLD_ROM);
      expect(SteamClient.Apps.RunGame).toHaveBeenCalledTimes(1);
    });

    it("does not start the game when only the old version is installed", async () => {
      vi.mocked(backend.getInstalledRom).mockImplementation(async (romId: number) =>
        romId === OLD_ROM ? installRecord(romId) : null,
      );
      const handler = await loadMapThenMove(move);

      handler(7, GAME_ID, "LaunchApp", 100);
      await flush();

      expect(SteamClient.Apps.RunGame).not.toHaveBeenCalled();
      expect(backend.getInstalledRom).not.toHaveBeenCalledWith(OLD_ROM);
    });
  },
);
