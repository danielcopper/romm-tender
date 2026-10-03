/**
 * Tender's button and the REAL session manager together, with no backend
 * answering: the session manager's own map lacks the game, so only the ROM the
 * button names before its start lets the session open and close — and only
 * then does the button go from "Launching..." to Resume and back to Play.
 *
 * `CustomPlayButton.test.tsx` mocks the session manager; this file does not.
 */

import "@testing-library/jest-dom/vitest";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act } from "@testing-library/react";
import { CustomPlayButton } from "./CustomPlayButton";
import * as backend from "../api/backend";
import type { CachedGameDetail } from "../api/backend";

vi.mock("../utils/cachedGameDetailStore", () => ({
  getCachedGameDetail: vi.fn<(appId: number) => Promise<CachedGameDetail>>(),
  invalidateCachedGameDetail: vi.fn(),
}));
vi.mock("../utils/steamShortcuts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../utils/steamShortcuts")>()),
  setLaunchOptionsConfirmed: vi.fn().mockResolvedValue(true),
}));
vi.mock("../shared/FallbackLaunchModal", () => ({
  showFallbackLaunchModal: vi.fn(),
}));

import { getCachedGameDetail } from "../utils/cachedGameDetailStore";
import { showFallbackLaunchModal } from "../shared/FallbackLaunchModal";
import { initSessionManager, ADOPTION_POLL_MAX_MS } from "../utils/sessionManager";

const APP_ID = 100;
const ROM_ID = 42;

type LifetimeCb = (update: { bRunning: boolean; unAppID: number }) => void;

describe("CustomPlayButton with the real session manager and no backend", () => {
  const never = <T,>(): Promise<T> => new Promise<T>(() => {});

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: { RunGame: vi.fn() },
      GameSessions: { RegisterForAppLifetimeNotifications: vi.fn(() => ({ unregister: vi.fn() })) },
    });
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({ GetGameID: () => "gid-1" })),
      allApps: [],
    });
    vi.stubGlobal("SteamUIStore", { RunningApps: [] });
    vi.mocked(getCachedGameDetail).mockResolvedValue({
      found: true,
      rom_id: ROM_ID,
      rom_name: "Test ROM",
      installed: true,
    });
    // The session manager's map, read at its start-up, does not hold this game.
    vi.mocked(backend.getAppIdRomIdMap).mockResolvedValue({});
    vi.mocked(showFallbackLaunchModal).mockResolvedValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("a start through Launch Anyway shows Resume while the game runs and Play once it exits", async () => {
    const { findByText, getByText } = render(<CustomPlayButton appId={APP_ID} />);
    const playBtn = await findByText("Play");

    vi.useFakeTimers();
    const init = initSessionManager();
    await vi.advanceTimersByTimeAsync(ADOPTION_POLL_MAX_MS);
    await init;
    const calls = vi.mocked(SteamClient.GameSessions.RegisterForAppLifetimeNotifications).mock.calls;
    const lifetime = calls[calls.length - 1]![0] as LifetimeCb;

    // From here on nothing the backend is asked answers.
    vi.mocked(backend.getAppIdRomIdMap).mockReturnValue(never());
    vi.mocked(backend.getInstalledRom).mockReturnValue(never());
    vi.mocked(backend.recordSessionStart).mockReturnValue(never());
    vi.mocked(backend.finalizeGameSession).mockReturnValue(never());

    await act(async () => {
      playBtn.click();
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(SteamClient.Apps.RunGame).toHaveBeenCalledWith("gid-1", "", -1, 100);
    expect(getByText("Launching...")).toBeInTheDocument();

    await act(async () => {
      lifetime({ bRunning: true, unAppID: APP_ID });
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(getByText("Resume")).toBeInTheDocument();

    await act(async () => {
      lifetime({ bRunning: false, unAppID: APP_ID });
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(getByText("Play")).toBeInTheDocument();
  });
});
