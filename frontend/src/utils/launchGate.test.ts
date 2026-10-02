import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  runLaunchGate,
  markLaunchSkipped,
  consumeLaunchSkip,
  LAUNCH_SKIP_WINDOW_MS,
  NO_ANSWER_MESSAGE,
} from "./launchGate";
import type { LaunchGateOps, PreLaunchSyncOutcome } from "./launchGate";
import { TimeoutError } from "./withTimeout";
import type { SyncConflict } from "../types";

function conflict(overrides: Partial<SyncConflict> = {}): SyncConflict {
  return {
    type: "sync_conflict",
    rom_id: 42,
    filename: "save.srm",
    server_save_id: 7,
    server_updated_at: "2026-01-01T00:00:00Z",
    server_size: 1024,
    local_path: "/local/save.srm",
    local_hash: "abc",
    local_mtime: "2026-01-01T00:00:00Z",
    local_size: 1024,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

// All-pass ops: migration not pending, tracking proceeds, core OK, online,
// sync succeeds with no conflicts. Each test overrides only the step it drives.
function makeOps(overrides: Partial<LaunchGateOps> = {}): LaunchGateOps {
  const okSync: PreLaunchSyncOutcome = { success: true, message: "" };
  return {
    migrationPending: vi.fn(() => false),
    hasLaunchTarget: vi.fn(async () => true),
    ensureTrackingConfigured: vi.fn(async (): Promise<"proceed" | "abort"> => "proceed"),
    checkCoreChange: vi.fn(async () => true),
    checkReachability: vi.fn(async () => true),
    preLaunchSync: vi.fn(async () => okSync),
    checkLocalDrift: vi.fn(async () => false),
    ...overrides,
  };
}

describe("runLaunchGate — verdict branches", () => {
  it("blocks with migration_pending when a migration is pending", async () => {
    const ops = makeOps({ migrationPending: () => true });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({
      decision: "block",
      reason: "migration_pending",
    });
    // Later steps must not run once migration blocks.
    expect(ops.hasLaunchTarget).not.toHaveBeenCalled();
    expect(ops.ensureTrackingConfigured).not.toHaveBeenCalled();
    expect(ops.checkReachability).not.toHaveBeenCalled();
  });

  it("blocks with no_launch_target when the ROM has no launch target", async () => {
    const ops = makeOps({ hasLaunchTarget: vi.fn(async () => false) });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({
      decision: "block",
      reason: "no_launch_target",
    });
    // The save-sync work must not run: there is no session for a synced save to
    // belong to, and a pre-launch upload before a launch that never happens is
    // pure risk.
    expect(ops.ensureTrackingConfigured).not.toHaveBeenCalled();
    expect(ops.preLaunchSync).not.toHaveBeenCalled();
    expect(ops.checkReachability).not.toHaveBeenCalled();
  });

  it("proceeds past the launch-target step when the ROM has one", async () => {
    const ops = makeOps({ hasLaunchTarget: vi.fn(async () => true) });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({ decision: "allow" });
    expect(ops.hasLaunchTarget).toHaveBeenCalled();
    expect(ops.ensureTrackingConfigured).toHaveBeenCalled();
  });

  it("aborts when tracking setup returns abort", async () => {
    const ops = makeOps({ ensureTrackingConfigured: vi.fn(async (): Promise<"proceed" | "abort"> => "abort") });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({ decision: "abort" });
    expect(ops.checkCoreChange).not.toHaveBeenCalled();
    expect(ops.checkReachability).not.toHaveBeenCalled();
  });

  it("aborts when the core-change confirm is cancelled", async () => {
    const ops = makeOps({ checkCoreChange: vi.fn(async () => false) });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({ decision: "abort" });
    expect(ops.checkReachability).not.toHaveBeenCalled();
  });

  it("returns conflict when online pre-launch sync surfaces conflicts", async () => {
    const conflicts = [conflict()];
    const ops = makeOps({
      checkReachability: vi.fn(async () => true),
      preLaunchSync: vi.fn(async () => ({ success: false, message: "conflict", conflicts })),
    });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({ decision: "conflict", conflicts });
    // Online branch: drift check is never consulted.
    expect(ops.checkLocalDrift).not.toHaveBeenCalled();
  });

  it("returns sync_failed (with message) when online sync fails without conflicts", async () => {
    const ops = makeOps({
      checkReachability: vi.fn(async () => true),
      preLaunchSync: vi.fn(async () => ({ success: false, message: "device not registered" })),
    });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({
      decision: "sync_failed",
      message: "device not registered",
    });
  });

  it("allows when online sync succeeds with no conflicts", async () => {
    const ops = makeOps({
      checkReachability: vi.fn(async () => true),
      preLaunchSync: vi.fn(async () => ({ success: true, message: "synced" })),
    });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({ decision: "allow" });
    expect(ops.checkLocalDrift).not.toHaveBeenCalled();
  });

  it("returns offline_drift when offline and the local save has drifted", async () => {
    const ops = makeOps({
      checkReachability: vi.fn(async () => false),
      checkLocalDrift: vi.fn(async () => true),
    });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({ decision: "offline_drift" });
    // Offline branch: pre-launch sync is never attempted.
    expect(ops.preLaunchSync).not.toHaveBeenCalled();
  });

  it("allows when offline and the local save has not drifted", async () => {
    const ops = makeOps({
      checkReachability: vi.fn(async () => false),
      checkLocalDrift: vi.fn(async () => false),
    });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({ decision: "allow" });
    expect(ops.preLaunchSync).not.toHaveBeenCalled();
  });

  it("never throws — an injected callback that throws resolves to allow", async () => {
    const ops = makeOps({
      // A bug in a gate step must not trap the user's game.
      checkReachability: vi.fn(async () => {
        throw new Error("probe blew up");
      }),
    });
    // Observable allow (not just "didn't throw") — non-vacuous.
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({ decision: "allow" });
  });

  it("never throws — a synchronous migrationPending throw resolves to allow", async () => {
    const ops = makeOps({
      migrationPending: () => {
        throw new Error("migration state read blew up");
      },
    });
    await expect(runLaunchGate(100, 42, ops)).resolves.toEqual({ decision: "allow" });
  });
});

describe("runLaunchGate — a step that gets no answer in time", () => {
  const noAnswer = async (): Promise<never> => {
    throw new TimeoutError(5000);
  };

  it.each([
    ["the launch-target read", { hasLaunchTarget: noAnswer }],
    ["the tracking setup", { ensureTrackingConfigured: noAnswer }],
    ["the core-change check", { checkCoreChange: noAnswer }],
    ["the reachability probe", { checkReachability: noAnswer }],
    ["the pre-launch sync", { preLaunchSync: noAnswer }],
    ["the local-drift check", { checkReachability: vi.fn(async () => false), checkLocalDrift: noAnswer }],
  ] satisfies [string, Partial<LaunchGateOps>][])(
    "%s → the no-answer sync_failed, never allow",
    async (_step, overrides) => {
      await expect(runLaunchGate(100, 42, makeOps(overrides))).resolves.toEqual({
        decision: "sync_failed",
        message: NO_ANSWER_MESSAGE,
        noAnswer: true,
      });
    },
  );

  it("runs no later step once a step got no answer", async () => {
    const ops = makeOps({ checkCoreChange: noAnswer });
    await runLaunchGate(100, 42, ops);
    expect(ops.checkReachability).not.toHaveBeenCalled();
    expect(ops.preLaunchSync).not.toHaveBeenCalled();
    expect(ops.checkLocalDrift).not.toHaveBeenCalled();
  });
});

describe("skip-set — markLaunchSkipped / consumeLaunchSkip", () => {
  // Module-level set: clear any residue between tests by consuming the ids used.
  beforeEach(() => {
    consumeLaunchSkip(555);
    consumeLaunchSkip(777);
  });

  it("consumeLaunchSkip returns true once after marking, then false (one-shot)", () => {
    markLaunchSkipped(555);
    expect(consumeLaunchSkip(555)).toBe(true);
    // Mark is consumed — a second read is false.
    expect(consumeLaunchSkip(555)).toBe(false);
  });

  it("consumeLaunchSkip returns false for an unmarked id", () => {
    expect(consumeLaunchSkip(777)).toBe(false);
  });

  describe("the skip window", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("a mark consumed at the end of the window still skips", () => {
      markLaunchSkipped(555);
      vi.advanceTimersByTime(LAUNCH_SKIP_WINDOW_MS);
      expect(consumeLaunchSkip(555)).toBe(true);
    });

    it("a mark consumed just after the window no longer skips, and is gone", () => {
      markLaunchSkipped(555);
      vi.advanceTimersByTime(LAUNCH_SKIP_WINDOW_MS + 1);
      expect(consumeLaunchSkip(555)).toBe(false);
      // Back inside the old window, a mark still held would let the start through;
      // the consume above deleted it, so nothing does.
      vi.setSystemTime(Date.now() - LAUNCH_SKIP_WINDOW_MS);
      expect(consumeLaunchSkip(555)).toBe(false);
    });

    it("an expired mark on another appId is dropped when a new mark is set", () => {
      markLaunchSkipped(777);
      vi.advanceTimersByTime(LAUNCH_SKIP_WINDOW_MS + 1);
      markLaunchSkipped(555);
      // Moving the clock back is the only way to ask whether 777's mark is still
      // held: had the new mark left it standing, it would be inside the window again.
      vi.setSystemTime(Date.now() - LAUNCH_SKIP_WINDOW_MS - 1);
      expect(consumeLaunchSkip(777)).toBe(false);
    });
  });
});
