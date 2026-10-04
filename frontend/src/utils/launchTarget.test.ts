import { describe, it, expect, beforeEach, vi } from "vitest";
import { romHasLaunchTarget, NO_LAUNCH_TARGET_TOAST_BODY } from "./launchTarget";
import * as backend from "../api/backend";
import type { InstalledRom } from "../types/api";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { TimeoutError } from "./withTimeout";

vi.mock("../api/backend", () => ({
  getInstalledRom: vi.fn(),
  logError: vi.fn(),
}));

function installed(overrides: Partial<InstalledRom> = {}): InstalledRom {
  return {
    rom_id: 42,
    file_name: "game.iso",
    file_path: "/roms/ps3/game.iso",
    system: "ps3",
    platform_slug: "ps3",
    installed_at: "2026-01-01T00:00:00Z",
    launchable: true,
    ...overrides,
  };
}

describe("romHasLaunchTarget", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("is true for an install the system can launch", async () => {
    vi.mocked(backend.getInstalledRom).mockResolvedValue(installed());
    await expect(romHasLaunchTarget(42, "Watcher")).resolves.toBe(true);
  });

  it("is false for an install recorded as unlaunchable", async () => {
    vi.mocked(backend.getInstalledRom).mockResolvedValue(
      installed({ file_name: "Puppeteer.pkg", file_path: "/roms/ps3/Puppeteer/Puppeteer.pkg", launchable: false }),
    );
    await expect(romHasLaunchTarget(42, "Watcher")).resolves.toBe(false);
  });

  it("is true when there is no install record — not this check's business", async () => {
    // The not-downloaded case has its own handling; this probe answers only
    // "downloaded, but nothing bootable".
    vi.mocked(backend.getInstalledRom).mockResolvedValue(null);
    await expect(romHasLaunchTarget(42, "Watcher")).resolves.toBe(true);
  });

  it("fails open and logs when the read throws", async () => {
    vi.mocked(backend.getInstalledRom).mockRejectedValue(new Error("bridge down"));

    await expect(romHasLaunchTarget(42, "CustomPlayButton")).resolves.toBe(true);
    expect(vi.mocked(backend.logError)).toHaveBeenCalledWith(
      expect.stringContaining("CustomPlayButton launch-target check threw (allowing launch)"),
    );
  });

  it("rejects with the expired limit, rather than failing open, when the read never answers", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(backend.getInstalledRom).mockReturnValue(new Promise<never>(() => {}));
      const answer = romHasLaunchTarget(42, "CustomPlayButton");
      const settled = expect(answer).rejects.toBeInstanceOf(TimeoutError);

      await vi.advanceTimersByTimeAsync(LOCAL_CALL_LIMIT_MS);

      await settled;
      expect(vi.mocked(backend.logError)).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("NO_LAUNCH_TARGET_TOAST_BODY", () => {
  it("tells the user the files were kept and where to look", async () => {
    // The copy is the whole user-facing payoff of the block — a bare "cannot
    // launch" would read as "the download failed", which is the opposite of
    // what happened.
    expect(NO_LAUNCH_TARGET_TOAST_BODY).toContain("on disk");
    expect(NO_LAUNCH_TARGET_TOAST_BODY).toContain("game page");
  });
});
