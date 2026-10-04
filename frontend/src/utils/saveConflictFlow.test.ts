import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { toaster } from "../api/host";
import * as backend from "../api/backend";
import {
  CONFLICT_EXPLANATION,
  STALE_CONFLICT_MESSAGE,
  announceSaveSync,
  conflictTitle,
  formatConflictSize,
  localSaveDetail,
  resolveConflictsSequentially,
  resolveKnownConflicts,
  resolveOneConflict,
  serverSaveDetail,
  serverSaveLabel,
  type SyncConflictResolution,
} from "./saveConflictFlow";
import { getRommConnectionState, setRommConnectionState } from "./connectionState";
import type { SaveStatus, SyncConflict } from "../types";

function makeConflict(overrides: Partial<SyncConflict> = {}): SyncConflict {
  return {
    type: "sync_conflict",
    rom_id: 42,
    filename: "savefile.srm",
    server_save_id: 9,
    server_updated_at: "2026-05-01T10:00:00Z",
    server_size: 2048,
    local_path: "/saves/savefile.srm",
    local_hash: "abc",
    local_mtime: "2026-05-01T09:00:00Z",
    local_size: 1024,
    created_at: "2026-05-01T11:00:00Z",
    ...overrides,
  };
}

function saveStatus(overrides: Partial<SaveStatus> = {}): SaveStatus {
  return {
    rom_id: 42,
    files: [],
    playtime: {
      total_seconds: 0,
      session_count: 0,
      last_session_start: null,
      last_session_duration_sec: null,
      last_played: null,
    },
    device_id: "dev-1",
    last_sync_check_at: null,
    ...overrides,
  };
}

function toastBodies(): unknown[] {
  return vi.mocked(toaster.toast).mock.calls.map(([data]) => data.body);
}

let logSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.mocked(backend.resolveSyncConflict).mockReset();
  vi.mocked(backend.getSaveStatus).mockReset();
  vi.mocked(toaster.toast).mockClear();
  logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
  setRommConnectionState("connected");
});

afterEach(() => {
  logSpy.mockRestore();
});

describe("conflict wording", () => {
  it("names the file in the title and the server save's id in its label", () => {
    const conflict = makeConflict({ filename: "game.srm", server_save_id: 77 });
    expect(conflictTitle(conflict)).toBe("Save conflict for game.srm");
    expect(serverSaveLabel(conflict)).toBe("Server save (id=77)");
  });

  it("states an absent or zero size as unknown, never as 0 B", () => {
    expect(formatConflictSize(null)).toBe("unknown");
    expect(formatConflictSize(0)).toBe("unknown");
    expect(formatConflictSize(1024)).toBe("1.0 KB");
  });

  it("pairs each side's size with what happened to it", () => {
    const conflict = makeConflict({ local_size: 500, server_size: null });
    expect(localSaveDetail(conflict)).toMatch(/^500 B · modified \S/);
    expect(serverSaveDetail(conflict)).toMatch(/^unknown · uploaded \S/);
  });

  it("explains that the version not kept is overwritten", () => {
    expect(CONFLICT_EXPLANATION).toBe(
      "Both your local save and the server save have changed since the last sync. Pick which version to keep — the " +
        "other will be overwritten.",
    );
  });
});

describe("resolveOneConflict", () => {
  it("sends the chosen side and confirms keeping the local save", async () => {
    vi.mocked(backend.resolveSyncConflict).mockResolvedValue({ success: true });
    const conflict = makeConflict();

    const outcome = await resolveOneConflict(conflict, "keep_local");

    expect(vi.mocked(backend.resolveSyncConflict)).toHaveBeenCalledWith(42, "savefile.srm", 9, "keep_local");
    expect(outcome).toEqual({ ok: true, toast: "Conflict resolved — kept your local save (uploaded to server)." });
  });

  it("confirms using the server save, and that the local one was backed up", async () => {
    vi.mocked(backend.resolveSyncConflict).mockResolvedValue({ success: true });

    const outcome = await resolveOneConflict(makeConflict(), "use_server");

    expect(outcome).toEqual({
      ok: true,
      toast: "Conflict resolved — used the server save · your local was backed up.",
    });
  });

  it("answers a stale conflict with its own sentence rather than the backend's", async () => {
    vi.mocked(backend.resolveSyncConflict).mockResolvedValue({
      success: false,
      reason: "stale_conflict",
      message: "out of date",
    });

    const outcome = await resolveOneConflict(makeConflict({ rom_id: 7, filename: "stale.srm" }), "keep_local");

    expect(outcome).toEqual({ ok: false, message: STALE_CONFLICT_MESSAGE });
    expect(STALE_CONFLICT_MESSAGE).toBe(
      "The server save has been updated by another device. Please cancel and retry sync to get the latest version.",
    );
    expect(logSpy).toHaveBeenCalledWith("resolveSyncConflict(7, stale.srm, keep_local) stale: out of date");
  });

  it("passes any other refusal's message through", async () => {
    vi.mocked(backend.resolveSyncConflict).mockResolvedValue({ success: false, message: "permission denied" });

    const outcome = await resolveOneConflict(makeConflict({ rom_id: 11, filename: "x.srm" }), "use_server");

    expect(outcome).toEqual({ ok: false, message: "permission denied" });
    expect(logSpy).toHaveBeenCalledWith("resolveSyncConflict(11, x.srm, use_server) failed: permission denied");
  });

  it("falls back to a generic sentence for a refusal with no message", async () => {
    vi.mocked(backend.resolveSyncConflict).mockResolvedValue({ success: false });

    expect(await resolveOneConflict(makeConflict(), "keep_local")).toEqual({
      ok: false,
      message: "Failed to resolve conflict",
    });
  });

  it("turns a throw into a message instead of rejecting", async () => {
    vi.mocked(backend.resolveSyncConflict).mockRejectedValue(new Error("network boom"));

    const outcome = await resolveOneConflict(makeConflict({ rom_id: 3, filename: "boom.srm" }), "keep_local");

    expect(outcome).toEqual({ ok: false, message: "network boom" });
    expect(logSpy).toHaveBeenCalledWith("resolveSyncConflict(3, boom.srm, keep_local) threw: network boom");
  });

  it("an empty rejection still produces a sentence", async () => {
    vi.mocked(backend.resolveSyncConflict).mockRejectedValue("");

    expect(await resolveOneConflict(makeConflict(), "use_server")).toEqual({
      ok: false,
      message: "Failed to resolve conflict",
    });
  });
});

describe("resolveConflictsSequentially", () => {
  it("an empty list is resolved and asks nothing", async () => {
    const showOne = vi.fn<(c: SyncConflict) => Promise<SyncConflictResolution>>();

    await expect(resolveConflictsSequentially([], showOne)).resolves.toBe("resolved");
    expect(showOne).not.toHaveBeenCalled();
  });

  it("asks once per conflict, in order, never two at once", async () => {
    const asked: string[] = [];
    let open = 0;
    const showOne = vi.fn(async (c: SyncConflict): Promise<SyncConflictResolution> => {
      open++;
      expect(open).toBe(1);
      asked.push(c.filename);
      await Promise.resolve();
      open--;
      return "keep_local";
    });
    const conflicts = [makeConflict({ filename: "a.srm" }), makeConflict({ filename: "b.srm" })];

    await expect(resolveConflictsSequentially(conflicts, showOne)).resolves.toBe("resolved");
    expect(asked).toEqual(["a.srm", "b.srm"]);
  });

  it("stops at the first cancel and asks about nothing after it", async () => {
    const showOne = vi
      .fn<(c: SyncConflict) => Promise<SyncConflictResolution>>()
      .mockResolvedValueOnce("use_server")
      .mockResolvedValueOnce("cancel")
      .mockResolvedValueOnce("keep_local");
    const conflicts = [
      makeConflict({ filename: "a.srm" }),
      makeConflict({ filename: "b.srm" }),
      makeConflict({ filename: "c.srm" }),
    ];

    await expect(resolveConflictsSequentially(conflicts, showOne)).resolves.toBe("cancel");
    expect(showOne).toHaveBeenCalledTimes(2);
  });
});

describe("announceSaveSync", () => {
  it("dispatches the save_sync change for the ROM", () => {
    const listener = vi.fn();
    globalThis.addEventListener("romm_data_changed", listener);
    try {
      announceSaveSync(42);
    } finally {
      globalThis.removeEventListener("romm_data_changed", listener);
    }
    expect((listener.mock.calls[0]![0] as CustomEvent).detail).toEqual({ type: "save_sync", rom_id: 42 });
  });
});

describe("resolveKnownConflicts", () => {
  let dataChanged: ReturnType<typeof vi.fn<(e: Event) => void>>;

  beforeEach(() => {
    dataChanged = vi.fn<(e: Event) => void>();
    globalThis.addEventListener("romm_data_changed", dataChanged);
  });

  afterEach(() => {
    globalThis.removeEventListener("romm_data_changed", dataChanged);
  });

  it("reads the status and hands its conflicts over, then announces the change", async () => {
    const conflict = makeConflict();
    vi.mocked(backend.getSaveStatus).mockResolvedValue(saveStatus({ conflicts: [conflict] }));
    const resolveAll = vi.fn().mockResolvedValue("resolved");

    await expect(resolveKnownConflicts(42, resolveAll, "test")).resolves.toBe("resolved");

    expect(vi.mocked(backend.getSaveStatus)).toHaveBeenCalledWith(42);
    expect(resolveAll).toHaveBeenCalledWith([conflict]);
    expect(dataChanged).toHaveBeenCalledTimes(1);
    expect(getRommConnectionState()).toBe("connected");
  });

  it("a cancelled dialog is cancelled, and nothing is announced", async () => {
    vi.mocked(backend.getSaveStatus).mockResolvedValue(saveStatus({ conflicts: [makeConflict()] }));

    await expect(resolveKnownConflicts(42, vi.fn().mockResolvedValue("cancel"), "test")).resolves.toBe("cancelled");

    expect(dataChanged).not.toHaveBeenCalled();
  });

  it("a conflict already cleared elsewhere is resolved without asking", async () => {
    vi.mocked(backend.getSaveStatus).mockResolvedValue(saveStatus());
    const resolveAll = vi.fn();

    await expect(resolveKnownConflicts(42, resolveAll, "test")).resolves.toBe("resolved");

    expect(resolveAll).not.toHaveBeenCalled();
    expect(dataChanged).toHaveBeenCalledTimes(1);
  });

  it("a clean read brings the connection store back online", async () => {
    setRommConnectionState("offline");
    vi.mocked(backend.getSaveStatus).mockResolvedValue(saveStatus({ conflicts: [] }));

    await resolveKnownConflicts(42, vi.fn(), "test");

    expect(getRommConnectionState()).toBe("connected");
  });

  it("a refused read toasts the refusal and asks nothing", async () => {
    vi.mocked(backend.getSaveStatus).mockResolvedValue({
      success: false,
      reason: "prune_active",
      message: "Cleanup is active.",
    });
    const resolveAll = vi.fn();

    await expect(resolveKnownConflicts(42, resolveAll, "test")).resolves.toBe("failed");

    expect(toastBodies()).toEqual(["Cleanup is active."]);
    expect(resolveAll).not.toHaveBeenCalled();
    expect(dataChanged).not.toHaveBeenCalled();
  });

  it("an unreachable server read drives the store offline and says so", async () => {
    vi.mocked(backend.getSaveStatus).mockResolvedValue(
      saveStatus({ server_query_failed: true, server_query_reason: "server_unreachable", conflicts: [] }),
    );
    const resolveAll = vi.fn();

    await expect(resolveKnownConflicts(42, resolveAll, "test")).resolves.toBe("failed");

    expect(getRommConnectionState()).toBe("offline");
    expect(toastBodies()).toEqual(["Couldn't reach server to resolve conflict"]);
    expect(resolveAll).not.toHaveBeenCalled();
    expect(dataChanged).not.toHaveBeenCalled();
  });

  it("any other failed server read leaves the store alone and does not blame the connection", async () => {
    vi.mocked(backend.getSaveStatus).mockResolvedValue(
      saveStatus({ server_query_failed: true, server_query_reason: "not_found", conflicts: [] }),
    );

    await expect(resolveKnownConflicts(42, vi.fn(), "test")).resolves.toBe("failed");

    expect(getRommConnectionState()).toBe("connected");
    expect(toastBodies()).toEqual(["RomM couldn't find this game's save data — conflict left unresolved"]);
  });

  it("a thrown read toasts and is failed", async () => {
    vi.mocked(backend.getSaveStatus).mockRejectedValue(new Error("network down"));

    await expect(resolveKnownConflicts(42, vi.fn(), "test")).resolves.toBe("failed");

    expect(toastBodies()).toEqual(["Couldn't reach server to resolve conflict"]);
    expect(dataChanged).not.toHaveBeenCalled();
  });

  it("a read that never answers times out as a failure", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(backend.getSaveStatus).mockReturnValue(new Promise<never>(() => {}));
      const pending = resolveKnownConflicts(42, vi.fn(), "test");

      await vi.advanceTimersByTimeAsync(15000);

      await expect(pending).resolves.toBe("failed");
      expect(toastBodies()).toEqual(["Couldn't reach server to resolve conflict"]);
    } finally {
      vi.useRealTimers();
    }
  });
});
