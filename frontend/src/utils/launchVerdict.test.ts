import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { toaster } from "../api/host";
import * as launchGate from "./launchGate";
import { actOnGateVerdict, runGateLoop, type LaunchPrompts } from "./launchVerdict";
import { NO_LAUNCH_TARGET_TOAST_BODY } from "./launchTarget";
import { startingSourceOffSentence } from "./emulatorSourceWording";
import type { GateVerdict, LaunchGateOps } from "./launchGate";
import type { SyncConflict } from "../types";

vi.mock("./launchGate", async (importActual) => {
  const actual = await importActual<typeof import("./launchGate")>();
  return { ...actual, runLaunchGate: vi.fn() };
});

const ROM_ID = 42;

const conflict: SyncConflict = {
  type: "sync_conflict",
  rom_id: ROM_ID,
  filename: "save.srm",
  server_save_id: 7,
  server_updated_at: "2026-01-01T00:00:00Z",
  server_size: 1024,
  local_path: "/local/save.srm",
  local_hash: "abc",
  local_mtime: "2026-01-01T00:00:00Z",
  local_size: 1024,
  created_at: "2026-01-01T00:00:00Z",
};

type GateLoopHooks = Parameters<typeof runGateLoop>[3];

interface Hooks extends GateLoopHooks {
  prompts: {
    resolveConflicts: Mock<LaunchPrompts["resolveConflicts"]>;
    askOfflineDrift: Mock<LaunchPrompts["askOfflineDrift"]>;
    confirmFallbackLaunch: Mock<LaunchPrompts["confirmFallbackLaunch"]>;
  };
  launch: Mock<() => Promise<void>>;
  onDeclined: Mock<() => void>;
  onConflictCancelled: Mock<() => void>;
  onRetry: Mock<() => void>;
  onMigrationBlocked: Mock<() => void>;
}

function makeHooks(): Hooks {
  return {
    prompts: {
      resolveConflicts: vi.fn(),
      askOfflineDrift: vi.fn(),
      confirmFallbackLaunch: vi.fn(),
    },
    launch: vi.fn(() => Promise.resolve()),
    onDeclined: vi.fn(),
    onConflictCancelled: vi.fn(),
    onRetry: vi.fn(),
    onMigrationBlocked: vi.fn(),
  };
}

const SETTLES = ["launch", "onDeclined", "onConflictCancelled", "onRetry", "onMigrationBlocked"] as const;

/** Each hook named in `called` ran once, and every other one never ran. */
function expectOnly(hooks: Hooks, called: (typeof SETTLES)[number][]): void {
  for (const key of SETTLES) {
    if (called.includes(key)) expect(hooks[key]).toHaveBeenCalledTimes(1);
    else expect(hooks[key]).not.toHaveBeenCalled();
  }
}

describe("actOnGateVerdict", () => {
  let hooks: Hooks;
  let dataChanged: Mock<(e: Event) => void>;

  beforeEach(() => {
    vi.clearAllMocks();
    hooks = makeHooks();
    dataChanged = vi.fn();
    globalThis.addEventListener("romm_data_changed", dataChanged);
    return () => globalThis.removeEventListener("romm_data_changed", dataChanged);
  });

  it("allow launches", async () => {
    expect(await actOnGateVerdict({ decision: "allow" }, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["launch"]);
  });

  it("abort is a decline, with no toast", async () => {
    expect(await actOnGateVerdict({ decision: "abort" }, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["onDeclined"]);
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("a migration block goes to its own hook, not to the decline", async () => {
    const verdict: GateVerdict = { decision: "block", reason: "migration_pending" };
    expect(await actOnGateVerdict(verdict, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["onMigrationBlocked"]);
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("a missing launch target is toasted, then declined", async () => {
    const verdict: GateVerdict = { decision: "block", reason: "no_launch_target" };
    expect(await actOnGateVerdict(verdict, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["onDeclined"]);
    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: NO_LAUNCH_TARGET_TOAST_BODY }));
  });

  it("a switched-off source is toasted by its kind, then declined", async () => {
    const verdict: GateVerdict = { decision: "block", reason: "source_switched_off", source: "retrodeck" };
    expect(await actOnGateVerdict(verdict, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["onDeclined"]);
    expect(toaster.toast).toHaveBeenCalledWith(
      expect.objectContaining({ body: startingSourceOffSentence("retrodeck") }),
    );
  });

  it("a not-installed block is declined without a toast", async () => {
    const verdict: GateVerdict = { decision: "block", reason: "not_installed" };
    expect(await actOnGateVerdict(verdict, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["onDeclined"]);
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("conflicts resolved: siblings are told about this ROM, then the game launches", async () => {
    hooks.prompts.resolveConflicts.mockResolvedValue("resolved");
    const order: string[] = [];
    dataChanged.mockImplementation(() => {
      order.push("announce");
    });
    hooks.launch.mockImplementation(async () => {
      order.push("launch");
    });

    expect(await actOnGateVerdict({ decision: "conflict", conflicts: [conflict] }, ROM_ID, hooks)).toBe("done");

    expect(hooks.prompts.resolveConflicts).toHaveBeenCalledWith([conflict]);
    expect((dataChanged.mock.calls[0]?.[0] as CustomEvent | undefined)?.detail).toEqual({
      type: "save_sync",
      rom_id: ROM_ID,
    });
    expect(order).toEqual(["announce", "launch"]);
    expectOnly(hooks, ["launch"]);
  });

  it("a cancelled conflict walk launches nothing and announces nothing", async () => {
    hooks.prompts.resolveConflicts.mockResolvedValue("cancel");
    expect(await actOnGateVerdict({ decision: "conflict", conflicts: [conflict] }, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["onConflictCancelled"]);
    expect(dataChanged).not.toHaveBeenCalled();
  });

  it("offline drift: Start Anyway launches", async () => {
    hooks.prompts.askOfflineDrift.mockResolvedValue("start_anyway");
    expect(await actOnGateVerdict({ decision: "offline_drift" }, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["launch"]);
  });

  it("offline drift: Retry asks for the gate again", async () => {
    hooks.prompts.askOfflineDrift.mockResolvedValue("retry");
    expect(await actOnGateVerdict({ decision: "offline_drift" }, ROM_ID, hooks)).toBe("retry");
    expectOnly(hooks, ["onRetry"]);
  });

  it("offline drift: Cancel is a decline", async () => {
    hooks.prompts.askOfflineDrift.mockResolvedValue("cancel");
    expect(await actOnGateVerdict({ decision: "offline_drift" }, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["onDeclined"]);
  });

  it("sync failed: the fallback prompt gets the sync's message, and a yes launches", async () => {
    hooks.prompts.confirmFallbackLaunch.mockResolvedValue(true);
    expect(await actOnGateVerdict({ decision: "sync_failed", message: "Server said no" }, ROM_ID, hooks)).toBe("done");
    expect(hooks.prompts.confirmFallbackLaunch).toHaveBeenCalledWith("Server said no");
    expectOnly(hooks, ["launch"]);
    expect(hooks.launch).toHaveBeenCalledWith({ skipReconfirm: false });
  });

  it("sync failed with no answer: a yes launches without the re-confirm", async () => {
    hooks.prompts.confirmFallbackLaunch.mockResolvedValue(true);
    const verdict = { decision: "sync_failed", message: "Couldn't check your saves in time", noAnswer: true } as const;
    expect(await actOnGateVerdict(verdict, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["launch"]);
    expect(hooks.launch).toHaveBeenCalledWith({ skipReconfirm: true });
  });

  it("sync failed: a no is a decline", async () => {
    hooks.prompts.confirmFallbackLaunch.mockResolvedValue(false);
    expect(await actOnGateVerdict({ decision: "sync_failed", message: "" }, ROM_ID, hooks)).toBe("done");
    expectOnly(hooks, ["onDeclined"]);
  });
});

describe("runGateLoop", () => {
  const ops = {} as LaunchGateOps;
  let hooks: Hooks;

  beforeEach(() => {
    vi.clearAllMocks();
    hooks = makeHooks();
    vi.mocked(launchGate.runLaunchGate).mockReset();
  });

  it("runs the gate once and acts on its verdict", async () => {
    vi.mocked(launchGate.runLaunchGate).mockResolvedValue({ decision: "allow" });
    await runGateLoop(100, ROM_ID, ops, hooks);
    expect(launchGate.runLaunchGate).toHaveBeenCalledTimes(1);
    expect(launchGate.runLaunchGate).toHaveBeenCalledWith(100, ROM_ID, ops);
    expect(hooks.launch).toHaveBeenCalledTimes(1);
  });

  it("runs the gate again after each Retry, and acts on the new verdict", async () => {
    vi.mocked(launchGate.runLaunchGate)
      .mockResolvedValueOnce({ decision: "offline_drift" })
      .mockResolvedValueOnce({ decision: "offline_drift" })
      .mockResolvedValueOnce({ decision: "allow" });
    hooks.prompts.askOfflineDrift.mockResolvedValue("retry");

    await runGateLoop(100, ROM_ID, ops, hooks);

    expect(launchGate.runLaunchGate).toHaveBeenCalledTimes(3);
    expect(hooks.onRetry).toHaveBeenCalledTimes(2);
    expect(hooks.launch).toHaveBeenCalledTimes(1);
  });

  it("without an error policy, a gate throw reaches the caller", async () => {
    vi.mocked(launchGate.runLaunchGate).mockRejectedValue(new Error("boom"));
    await expect(runGateLoop(100, ROM_ID, ops, hooks)).rejects.toThrow("boom");
    expect(hooks.launch).not.toHaveBeenCalled();
  });

  it("with an error policy, a gate throw is acted on as the verdict the policy answers", async () => {
    vi.mocked(launchGate.runLaunchGate).mockRejectedValue(new Error("boom"));
    const onGateError = vi.fn((): GateVerdict => ({ decision: "allow" }));

    await runGateLoop(100, ROM_ID, ops, { ...hooks, onGateError });

    expect(onGateError).toHaveBeenCalledWith(expect.objectContaining({ message: "boom" }));
    expect(hooks.launch).toHaveBeenCalledTimes(1);
  });

  it("the error policy also covers a gate run after a Retry", async () => {
    vi.mocked(launchGate.runLaunchGate)
      .mockResolvedValueOnce({ decision: "offline_drift" })
      .mockRejectedValueOnce(new Error("second"));
    hooks.prompts.askOfflineDrift.mockResolvedValue("retry");
    const onGateError = vi.fn((): GateVerdict => ({ decision: "abort" }));

    await runGateLoop(100, ROM_ID, ops, { ...hooks, onGateError });

    expect(onGateError).toHaveBeenCalledWith(expect.objectContaining({ message: "second" }));
    expect(hooks.onDeclined).toHaveBeenCalledTimes(1);
    expect(hooks.launch).not.toHaveBeenCalled();
  });
});
