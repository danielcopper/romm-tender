import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useGamePlaytime } from "./playtimeReconcile";
import { updatePlaytimeDisplay } from "./metadataPatches";
import * as backend from "../api/backend";

// The real playtime write, so the signal the page reacts to is the one a
// session's end and a bulk pass actually send.
vi.mock("../api/backend", () => ({
  reconcilePlaytime: vi.fn(),
  debugLog: vi.fn(),
  logInfo: vi.fn(),
}));

const APP_ID = 4242;
const ROM_ID = 77;
const NOW = new Date("2026-09-21T12:00:00Z");

function steamOverview(rtLastTimePlayed: number): SteamAppOverview {
  return {
    appid: APP_ID,
    display_name: "",
    strDisplayName: "",
    rt_last_time_played: rtLastTimePlayed,
    minutes_playtime_forever: 60,
  };
}

/** The page open with RomM's last-played restored over Steam's *steamRt*. */
async function openPage(restoredIso: string, steamRt: number) {
  const ov = steamOverview(steamRt);
  vi.stubGlobal("__mobxGlobals", undefined);
  vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(() => ov), allApps: [] });
  vi.mocked(backend.reconcilePlaytime).mockResolvedValue({
    total_seconds: 3600,
    session_count: 1,
    last_played: restoredIso,
    server_query_failed: false,
  });
  const hook = renderHook(() => useGamePlaytime(APP_ID, ROM_ID));
  await waitFor(() => expect(backend.reconcilePlaytime).toHaveBeenCalledWith(ROM_ID));
  return hook;
}

describe("LAST PLAYED across a session's end on an open game page", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the session's end once the session ends", async () => {
    const twoDaysAgo = Math.floor(NOW.getTime() / 1000) - 2 * 86400;
    const { result } = await openPage("2026-09-20T12:00:00Z", twoDaysAgo);
    await waitFor(() => expect(result.current.lastPlayed).toBe("Yesterday"));

    // The session's end writes the new total and stamps Steam's last-played.
    act(() => {
      updatePlaytimeDisplay(APP_ID, 7200);
    });

    expect(result.current.lastPlayed).toBe("Today");
    expect(result.current.playtime).toBe("2 Hours");
  });

  it("keeps a restored last-played newer than Steam's across any other write", async () => {
    const lastMonth = Math.floor(new Date("2026-08-20T12:00:00Z").getTime() / 1000);
    const { result } = await openPage("2026-09-20T12:00:00Z", lastMonth);
    await waitFor(() => expect(result.current.lastPlayed).toBe("Yesterday"));

    // A bulk pass writes the total alone.
    act(() => {
      updatePlaytimeDisplay(APP_ID, 7200, false);
    });

    expect(result.current.lastPlayed).toBe("Yesterday");
    expect(result.current.playtime).toBe("2 Hours");
  });

  it("keeps the restored last-played over Steam's own now after a device change", async () => {
    // Steam sets its last-played to now on a device it has not seen the game on.
    const { result } = await openPage("2026-09-20T12:00:00Z", Math.floor(NOW.getTime() / 1000));
    await waitFor(() => expect(result.current.lastPlayed).toBe("Yesterday"));

    act(() => {
      updatePlaytimeDisplay(APP_ID, 7200, false);
    });

    expect(result.current.lastPlayed).toBe("Yesterday");
  });
});
