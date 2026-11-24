import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  executeCopySaveToSlot,
  executeDeleteLocalSaves,
  executeSlotDelete,
  executeSlotSwitch,
  executeVersionRestore,
  findStrandedVersion,
  loadFileVersions,
  loadSlotDeleteInfo,
} from "./saveSlotFlow";
import * as backend from "../api/backend";
import * as connectionState from "./connectionState";
import * as gameDetailStore from "./gameDetailStore";
import * as toast from "./toast";
import type { SaveStranding, VersionInfo, VersionList } from "../api/backend";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import type { SaveStatus, SaveVersionEntry, SlotDeleteInfo, SyncConflict } from "../types";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  checkLocalDrift: vi.fn(),
  copySaveToSlot: vi.fn(),
  debugLog: vi.fn(() => Promise.resolve()),
  deleteLocalSaves: vi.fn(),
  deleteSlot: vi.fn(),
  getSlotDeleteInfo: vi.fn(),
  getVersionList: vi.fn(),
  logWarn: vi.fn(),
  savesListFileVersions: vi.fn(),
  savesRollbackToVersion: vi.fn(),
  switchSlot: vi.fn(),
}));

vi.mock("./connectionState", () => ({
  reportServerReachable: vi.fn(),
}));

vi.mock("./gameDetailStore", () => ({
  noteSaveSyncDisplay: vi.fn(),
}));

vi.mock("./toast", () => ({
  showToast: vi.fn(),
}));

function version(overrides: Partial<VersionInfo>): VersionInfo {
  return {
    rom_id: 1,
    name: "Game (USA)",
    label: "USA",
    regions: [],
    languages: [],
    revision: "",
    tags: [],
    synced: true,
    installed: true,
    active: false,
    is_default: false,
    switchable: true,
    vanished: false,
    ...overrides,
  };
}

function versionList(versions: VersionInfo[], multi = true): VersionList {
  return { multi_version: multi, versions, bound_vanished: false };
}

function strandBy(verdicts: Record<number, SaveStranding>): void {
  vi.mocked(backend.checkLocalDrift).mockImplementation((romId: number) => {
    const stranding = verdicts[romId] ?? "none";
    return Promise.resolve({ rom_id: romId, drifted: stranding === "unsynced", only_here: false, stranding });
  });
}

const STATUS: SaveStatus = {
  rom_id: 7,
  files: [],
  playtime: {
    total_seconds: 0,
    session_count: 0,
    last_session_start: null,
    last_session_duration_sec: null,
    last_played: null,
  },
  device_id: "device",
  last_sync_check_at: null,
};

const notCancelled = () => false;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("findStrandedVersion", () => {
  it("answers null without probing when the game has one version", async () => {
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(versionList([version({ rom_id: 2 })], false));

    await expect(findStrandedVersion(10, notCancelled)).resolves.toBeNull();
    expect(backend.checkLocalDrift).not.toHaveBeenCalled();
  });

  it("probes only versions that are installed, inactive and not known to be gone from RomM", async () => {
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(
      versionList([
        version({ rom_id: 1, active: true }),
        version({ rom_id: 2, installed: false }),
        version({ rom_id: 3, vanished: true }),
        version({ rom_id: 4 }),
      ]),
    );
    strandBy({});

    await expect(findStrandedVersion(10, notCancelled)).resolves.toBeNull();
    expect(vi.mocked(backend.checkLocalDrift).mock.calls).toEqual([[4]]);
  });

  it("answers the first version with unsynced saves, by its label, and probes no further", async () => {
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(
      versionList([
        version({ rom_id: 2, label: "Europe" }),
        version({ rom_id: 3, label: "Japan" }),
        version({ rom_id: 4, label: "USA" }),
      ]),
    );
    strandBy({ 3: "unsynced", 4: "unsynced" });

    await expect(findStrandedVersion(10, notCancelled)).resolves.toEqual({ name: "Japan", stranding: "unsynced" });
    expect(vi.mocked(backend.checkLocalDrift).mock.calls).toEqual([[2], [3]]);
  });

  it("answers a version whose saves could not be checked the same way", async () => {
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(
      versionList([version({ rom_id: 2, label: "Europe" }), version({ rom_id: 3, label: "Japan" })]),
    );
    strandBy({ 2: "unchecked", 3: "unsynced" });

    await expect(findStrandedVersion(10, notCancelled)).resolves.toEqual({ name: "Europe", stranding: "unchecked" });
    expect(vi.mocked(backend.checkLocalDrift).mock.calls).toEqual([[2]]);
  });

  it("names a version by its name when it has no label, and by its id when it has neither", async () => {
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(versionList([version({ rom_id: 2, label: "" })]));
    strandBy({ 2: "unsynced" });
    await expect(findStrandedVersion(10, notCancelled)).resolves.toMatchObject({ name: "Game (USA)" });

    vi.mocked(backend.getVersionList).mockResolvedValueOnce(versionList([version({ rom_id: 3, label: "", name: "" })]));
    strandBy({ 3: "unsynced" });
    await expect(findStrandedVersion(10, notCancelled)).resolves.toMatchObject({ name: "3" });
  });

  it("probes nothing once cancelled during the version list", async () => {
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(versionList([version({ rom_id: 2 })]));
    strandBy({ 2: "unsynced" });

    await expect(findStrandedVersion(10, () => true)).resolves.toBeNull();
    expect(backend.checkLocalDrift).not.toHaveBeenCalled();
  });

  it("stops probing once cancelled during a probe, even one with unsynced saves", async () => {
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(
      versionList([version({ rom_id: 2 }), version({ rom_id: 3 })]),
    );
    let cancelled = false;
    vi.mocked(backend.checkLocalDrift).mockImplementation((romId: number) => {
      cancelled = true;
      return Promise.resolve({ rom_id: romId, drifted: true, only_here: false, stranding: "unsynced" });
    });

    await expect(findStrandedVersion(10, () => cancelled)).resolves.toBeNull();
    expect(vi.mocked(backend.checkLocalDrift).mock.calls).toEqual([[2]]);
  });

  it("throws what the version list throws", async () => {
    vi.mocked(backend.getVersionList).mockRejectedValueOnce(new Error("socket closed"));

    await expect(findStrandedVersion(10, notCancelled)).rejects.toThrow("socket closed");
  });

  it("answers a probe that rejects as could-not-check, and warns", async () => {
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(versionList([version({ rom_id: 2, label: "Europe" })]));
    vi.mocked(backend.checkLocalDrift).mockRejectedValueOnce(new Error("probe failed"));

    await expect(findStrandedVersion(10, notCancelled)).resolves.toEqual({ name: "Europe", stranding: "unchecked" });
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: local-save check failed for rom 2: Error: probe failed",
    );
  });

  it("answers a probe with no answer in time as could-not-check, and warns", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(backend.getVersionList).mockResolvedValueOnce(versionList([version({ rom_id: 2, label: "Europe" })]));
      vi.mocked(backend.checkLocalDrift).mockReturnValueOnce(new Promise(() => {}));

      const answer = findStrandedVersion(10, notCancelled);
      await vi.advanceTimersByTimeAsync(LOCAL_CALL_LIMIT_MS);
      await expect(answer).resolves.toEqual({ name: "Europe", stranding: "unchecked" });
      expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
        `saveSlotFlow: local-save check failed for rom 2: TimeoutError: timed out after ${LOCAL_CALL_LIMIT_MS}ms`,
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("answers null without probing when a multi-version list carries no versions", async () => {
    vi.mocked(backend.getVersionList).mockResolvedValueOnce({ multi_version: true, bound_vanished: false });

    await expect(findStrandedVersion(10, notCancelled)).resolves.toBeNull();
    expect(backend.checkLocalDrift).not.toHaveBeenCalled();
  });
});

describe("executeSlotSwitch", () => {
  it("answers the new save status and marks RomM reachable on success", async () => {
    vi.mocked(backend.switchSlot).mockResolvedValueOnce({ success: true, save_status: STATUS });

    await expect(executeSlotSwitch(7, "main", "activate")).resolves.toEqual({ ok: true, saveStatus: STATUS });
    expect(backend.switchSlot).toHaveBeenCalledWith(7, "main");
    expect(connectionState.reportServerReachable).toHaveBeenCalledExactlyOnceWith(true);
  });

  it("answers a new slot's save status the same way", async () => {
    vi.mocked(backend.switchSlot).mockResolvedValueOnce({ success: true, save_status: STATUS });

    await expect(executeSlotSwitch(7, "new", "create")).resolves.toEqual({ ok: true, saveStatus: STATUS });
    expect(backend.switchSlot).toHaveBeenCalledWith(7, "new");
    expect(connectionState.reportServerReachable).toHaveBeenCalledExactlyOnceWith(true);
  });

  it("marks RomM unreachable for a server_unreachable failure answer", async () => {
    vi.mocked(backend.switchSlot).mockResolvedValueOnce({
      success: false,
      reason: "server_unreachable",
      message: "RomM did not answer",
    });

    await expect(executeSlotSwitch(7, "main", "activate")).resolves.toEqual({
      ok: false,
      reason: "server_unreachable",
      message: "Can't switch — RomM server is not reachable",
    });
    expect(connectionState.reportServerReachable).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("marks RomM unreachable for a new slot's server_unreachable failure answer too", async () => {
    vi.mocked(backend.switchSlot).mockResolvedValueOnce({
      success: false,
      reason: "server_unreachable",
      message: "RomM did not answer",
    });

    await expect(executeSlotSwitch(7, "new", "create")).resolves.toEqual({
      ok: false,
      reason: "server_unreachable",
      message: "Can't create a slot — RomM server is not reachable",
    });
    expect(connectionState.reportServerReachable).toHaveBeenCalledExactlyOnceWith(false);
  });

  it.each(
    (["activate", "create"] as const).flatMap((purpose) =>
      (
        [
          "pending_uploads",
          "not_installed",
          "save_shape_unsupported",
          "not_found",
          "server_error",
          "auth_failed",
          "sync_disabled",
          "savefiles_in_content_dir",
        ] as const
      ).map((reason) => ({ purpose, reason })),
    ),
  )("tells the connection store nothing for a $reason failure answer ($purpose)", async ({ purpose, reason }) => {
    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.switchSlot).mockResolvedValueOnce({ success: false, reason, message: "Not switched" });

    await expect(executeSlotSwitch(7, "main", purpose)).resolves.toMatchObject({ ok: false, reason });
    expect(connectionState.reportServerReachable).not.toHaveBeenCalled();
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      `saveSlotFlow: switchSlot for rom 7 to 'main' (${purpose}) failed (${reason}): Not switched`,
    );
  });

  it("words an activate's failure answer through switchSlotFailureMessage, the backend's explanation included", async () => {
    vi.mocked(backend.switchSlot).mockResolvedValueOnce({
      success: false,
      reason: "save_shape_unsupported",
      message: "This game's saves span several files.",
    });
    await expect(executeSlotSwitch(7, "main", "activate")).resolves.toMatchObject({
      message: "This game's saves span several files.",
    });

    vi.mocked(backend.switchSlot).mockResolvedValueOnce({ success: false, reason: "not_found", message: "Gone" });
    await expect(executeSlotSwitch(7, "main", "activate")).resolves.toMatchObject({
      message: "Failed to switch slot",
    });
  });

  it("words a create's failure answer through createSlotFailureMessage", async () => {
    vi.mocked(backend.switchSlot).mockResolvedValueOnce({
      success: false,
      reason: "pending_uploads",
      message: "Upload first",
    });
    await expect(executeSlotSwitch(7, "new", "create")).resolves.toEqual({
      ok: false,
      reason: "pending_uploads",
      message: "Sync your saves first — local changes haven't been uploaded",
    });
    expect(connectionState.reportServerReachable).not.toHaveBeenCalled();

    vi.mocked(backend.switchSlot).mockResolvedValueOnce({
      success: false,
      reason: "save_shape_unsupported",
      message: "This game's saves span several files.",
    });
    await expect(executeSlotSwitch(7, "new", "create")).resolves.toMatchObject({ message: "Failed to create slot" });
  });

  it("answers a throw with no reason and the purpose's error sentence, logged and with reachability untouched", async () => {
    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.switchSlot).mockRejectedValueOnce(new Error("socket closed"));
    await expect(executeSlotSwitch(7, "main", "activate")).resolves.toEqual({
      ok: false,
      reason: null,
      message: "An error occurred while switching slots",
    });
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: switchSlot for rom 7 to 'main' (activate) threw: Error: socket closed",
    );

    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.switchSlot).mockRejectedValueOnce(new Error("socket closed"));
    await expect(executeSlotSwitch(7, "new", "create")).resolves.toEqual({
      ok: false,
      reason: null,
      message: "An error occurred while creating the slot",
    });
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: switchSlot for rom 7 to 'new' (create) threw: Error: socket closed",
    );
    expect(connectionState.reportServerReachable).not.toHaveBeenCalled();
  });
});

describe("loadSlotDeleteInfo", () => {
  const INFO: SlotDeleteInfo = {
    success: true,
    slot: "old",
    source: "server",
    server_save_count: 2,
    server_save_ids: [1, 2],
    local_file_count: 0,
    local_filenames: [],
    is_active: false,
  };

  it("answers the info and toasts nothing", async () => {
    vi.mocked(backend.getSlotDeleteInfo).mockResolvedValueOnce(INFO);

    await expect(loadSlotDeleteInfo(7, "old")).resolves.toEqual(INFO);
    expect(backend.getSlotDeleteInfo).toHaveBeenCalledWith(7, "old");
    expect(toast.showToast).not.toHaveBeenCalled();
  });

  it("toasts a failure answer's own message and answers null", async () => {
    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.getSlotDeleteInfo).mockResolvedValueOnce({
      success: false,
      reason: "not_found",
      message: "No such slot",
    });

    await expect(loadSlotDeleteInfo(7, "old")).resolves.toBeNull();
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: getSlotDeleteInfo for rom 7 slot 'old' failed (not_found): No such slot",
    );
    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("No such slot");
  });

  it("toasts that the info could not be loaded when the call throws, and logs it", async () => {
    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.getSlotDeleteInfo).mockRejectedValueOnce(new Error("socket closed"));

    await expect(loadSlotDeleteInfo(7, "old")).resolves.toBeNull();
    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("Failed to load slot info");
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: getSlotDeleteInfo for rom 7 slot 'old' threw: Error: socket closed",
    );
  });
});

describe("executeSlotDelete", () => {
  it("toasts the deletion and answers true", async () => {
    vi.mocked(backend.deleteSlot).mockResolvedValueOnce({ success: true, deleted_server_saves: 2, cleaned_files: 0 });

    await expect(executeSlotDelete(7, "old")).resolves.toBe(true);
    expect(backend.deleteSlot).toHaveBeenCalledWith(7, "old");
    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("Slot 'old' deleted");
  });

  it("toasts a failure answer's own message and answers false", async () => {
    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.deleteSlot).mockResolvedValueOnce({
      success: false,
      reason: "server_unreachable",
      message: "RomM did not answer",
    });

    await expect(executeSlotDelete(7, "old")).resolves.toBe(false);
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: deleteSlot for rom 7 slot 'old' failed (server_unreachable): RomM did not answer",
    );
    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("RomM did not answer");
  });

  it("toasts the error sentence when the call throws, logs it, and answers false", async () => {
    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.deleteSlot).mockRejectedValueOnce(new Error("socket closed"));

    await expect(executeSlotDelete(7, "old")).resolves.toBe(false);
    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("An error occurred while deleting the slot");
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: deleteSlot for rom 7 slot 'old' threw: Error: socket closed",
    );
  });
});

describe("executeSlotSwitch on a switch that stopped partway", () => {
  function listenForDataChanges(): { details: unknown[]; stop: () => void } {
    const details: unknown[] = [];
    const listener = (e: Event) => details.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", listener);
    return { details, stop: () => globalThis.removeEventListener("romm_data_changed", listener) };
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function expectPartialSwitchOutcome(
    purpose: "activate" | "create",
    reason: "switch_stopped" | "switch_incomplete",
    message: string,
  ): Promise<void> {
    const events = listenForDataChanges();
    try {
      await expect(executeSlotSwitch(7, "main", purpose)).resolves.toEqual({
        ok: false,
        reason,
        message,
        partial: true,
      });
    } finally {
      events.stop();
    }
    expect(events.details).toEqual([{ type: "save_sync", rom_id: 7 }]);
    expect(connectionState.reportServerReachable).not.toHaveBeenCalled();
    expect(backend.logWarn).not.toHaveBeenCalled();
  }

  it.each([
    { purpose: "activate" as const, outcome: "switched" },
    { purpose: "create" as const, outcome: "created" },
  ])(
    "words a switch_stopped answer with the saves set aside and reloads the save surface ($purpose)",
    async ({ purpose, outcome }) => {
      vi.mocked(backend.switchSlot).mockResolvedValueOnce({
        success: false,
        reason: "switch_stopped",
        message: "Could not move pokemon.rtc into .romm-backup.",
        set_aside: ["pokemon.srm"],
      });
      await expectPartialSwitchOutcome(
        purpose,
        "switch_stopped",
        `The slot wasn't ${outcome}: a save couldn't be moved to .romm-backup. Already moved there: pokemon.srm.`,
      );
    },
  );

  it.each(["activate", "create"] as const)(
    "words a switch_incomplete answer as a switch and reloads the save surface ($0)",
    async (purpose) => {
      vi.mocked(backend.switchSlot).mockResolvedValueOnce({
        success: false,
        reason: "switch_incomplete",
        message: "Switched to slot but 1 save(s) failed to download — retry",
      });
      await expectPartialSwitchOutcome(
        purpose,
        "switch_incomplete",
        "Switched slots, but not every save of the new slot downloaded. Sync Save Files to finish.",
      );
    },
  );

  it("reloads nothing for a switch that refused with nothing moved", async () => {
    vi.mocked(backend.switchSlot).mockResolvedValueOnce({
      success: false,
      reason: "backup_failed",
      message: "Could not move pokemon.srm into .romm-backup (denied). Nothing was moved.",
    });
    const events = listenForDataChanges();
    try {
      await expect(executeSlotSwitch(7, "main", "activate")).resolves.toEqual({
        ok: false,
        reason: "backup_failed",
        message: "Can't switch — a save couldn't be moved to .romm-backup, nothing was moved",
      });
    } finally {
      events.stop();
    }
    expect(events.details).toEqual([]);
  });
});

describe("loadFileVersions", () => {
  const ENTRY: SaveVersionEntry = {
    id: 11,
    file_name: "save.srm",
    emulator: "mgba",
    updated_at: "2025-06-14T10:00:00Z",
    file_size_bytes: 1024,
    device_syncs: [],
    uploaded_by_us: true,
  };

  it("returns versions for ok and multi_file_unsupported", async () => {
    vi.mocked(backend.savesListFileVersions).mockResolvedValueOnce({ status: "ok", versions: [ENTRY] });
    await expect(loadFileVersions(7, "default", "save.srm")).resolves.toEqual({ ok: true, versions: [ENTRY] });

    vi.mocked(backend.savesListFileVersions).mockResolvedValueOnce({ status: "multi_file_unsupported", versions: [] });
    await expect(loadFileVersions(7, "default", "save.srm")).resolves.toEqual({ ok: true, versions: [] });
  });

  it("logs via logWarn and maps non-ok responses and thrown calls", async () => {
    vi.mocked(backend.savesListFileVersions).mockResolvedValueOnce({ status: "not_found", message: "404" });
    await expect(loadFileVersions(7, "default", "save.srm")).resolves.toEqual({
      ok: false,
      error: "RomM couldn't find this game's save data.",
    });
    expect(backend.logWarn).toHaveBeenCalledWith(
      "saveSlotFlow: server has no such entry for rom 7 slot 'default' file save.srm: 404",
    );

    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.savesListFileVersions).mockResolvedValueOnce({ status: "romm_refused", message: "429" });
    await expect(loadFileVersions(7, "default", "save.srm")).resolves.toEqual({
      ok: false,
      error: "Couldn't load the versions: 429",
    });
    expect(backend.logWarn).toHaveBeenCalledWith(
      "saveSlotFlow: RomM refused version list for rom 7 slot 'default' file save.srm: 429",
    );

    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.savesListFileVersions).mockResolvedValueOnce({
      status: "device_not_registered",
      message: "unreg",
    });
    await expect(loadFileVersions(7, "default", "save.srm")).resolves.toEqual({
      ok: false,
      error: "This device could not be registered with RomM. Tap retry.",
    });
    expect(backend.logWarn).toHaveBeenCalledWith(
      "saveSlotFlow: device not registered for rom 7 slot 'default' file save.srm: unreg",
    );

    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.savesListFileVersions).mockResolvedValueOnce({ status: "server_unreachable", message: "down" });
    await expect(loadFileVersions(7, "default", "save.srm")).resolves.toEqual({
      ok: false,
      error: "Couldn't reach RomM. Tap retry.",
    });
    expect(backend.logWarn).toHaveBeenCalledWith(
      "saveSlotFlow: server unreachable for rom 7 slot 'default' file save.srm: down",
    );

    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.savesListFileVersions).mockRejectedValueOnce(new Error("socket closed"));
    await expect(loadFileVersions(7, "default", "save.srm")).resolves.toEqual({
      ok: false,
      error: "Couldn't reach RomM. Tap retry.",
    });
    expect(backend.logWarn).toHaveBeenCalledWith(
      "saveSlotFlow: failed to load versions for rom 7 slot 'default' file save.srm: Error: socket closed",
    );
  });
});

const CONFLICT: SyncConflict = {
  type: "sync_conflict",
  rom_id: 7,
  filename: "save.srm",
  server_save_id: 1,
  server_updated_at: "2025-06-15T10:00:00Z",
  server_size: 100,
  local_path: "/local/save.srm",
  local_hash: "abc",
  local_mtime: "2025-06-15T10:00:00Z",
  local_size: 100,
  created_at: "2025-06-15T10:00:00Z",
};

async function expectConflictRejectionPropagates(
  run: (showConflict: (c: SyncConflict) => Promise<unknown>) => Promise<unknown>,
): Promise<void> {
  vi.mocked(toast.showToast).mockClear();
  const rejectingDialog = vi.fn().mockRejectedValueOnce(new Error("dialog boom"));
  await expect(run(rejectingDialog)).rejects.toThrow("dialog boom");
  await new Promise((r) => setTimeout(r, 0));
  expect(toast.showToast).not.toHaveBeenCalled();
}

describe("executeCopySaveToSlot", () => {
  it("toasts and announces save sync on ok and upload_failed, but skips announce on already_present", async () => {
    const events: unknown[] = [];
    const onEvt = (e: Event) => events.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onEvt);
    try {
      vi.mocked(backend.copySaveToSlot).mockResolvedValueOnce({ status: "ok" });
      await executeCopySaveToSlot(7, 10, "alt", vi.fn());
      expect(toast.showToast).toHaveBeenCalledWith("Save copied to slot 'alt'");
      expect(events).toEqual([{ type: "save_sync", rom_id: 7 }]);

      events.length = 0;
      vi.mocked(backend.copySaveToSlot).mockResolvedValueOnce({ status: "upload_failed", message: "500" });
      await executeCopySaveToSlot(7, 10, "alt", vi.fn());
      expect(events).toEqual([{ type: "save_sync", rom_id: 7 }]);

      events.length = 0;
      vi.mocked(backend.copySaveToSlot).mockResolvedValueOnce({ status: "already_present", existing_id: 99 });
      await executeCopySaveToSlot(7, 10, "alt", vi.fn());
      expect(toast.showToast).toHaveBeenCalledWith("Already in slot 'alt' as #99");
      expect(events).toEqual([]);
    } finally {
      globalThis.removeEventListener("romm_data_changed", onEvt);
    }
  });

  it("does nothing on an unrecognized copy status and handles conflicts and thrown calls", async () => {
    const showConflict = vi.fn().mockResolvedValue("resolved");
    vi.mocked(backend.copySaveToSlot).mockResolvedValueOnce({ status: "unrecognized" as never });
    await executeCopySaveToSlot(7, 10, "alt", showConflict);
    await new Promise((r) => setTimeout(r, 0));
    expect(toast.showToast).not.toHaveBeenCalled();
    expect(showConflict).not.toHaveBeenCalled();

    vi.mocked(backend.copySaveToSlot).mockResolvedValueOnce({ status: "conflict_blocked", conflicts: [CONFLICT] });
    await executeCopySaveToSlot(7, 10, "alt", showConflict);
    await new Promise((r) => setTimeout(r, 0));
    expect(showConflict).toHaveBeenCalledExactlyOnceWith(CONFLICT);
    expect(toast.showToast).not.toHaveBeenCalled();

    vi.mocked(backend.logWarn).mockClear();
    vi.mocked(backend.copySaveToSlot).mockRejectedValueOnce(new Error("net down"));
    await executeCopySaveToSlot(7, 10, "alt", showConflict);
    expect(backend.logWarn).toHaveBeenCalledWith(
      "saveSlotFlow: copy failed for rom 7 save 10 into 'alt': Error: net down",
    );
    expect(toast.showToast).toHaveBeenCalledWith("Couldn't copy the save. Check your connection and try again.");

    vi.mocked(backend.copySaveToSlot).mockResolvedValueOnce({ status: "conflict_blocked", conflicts: [CONFLICT] });
    await expectConflictRejectionPropagates((cb) => executeCopySaveToSlot(7, 10, "alt", cb));
  });
});

describe("executeVersionRestore", () => {
  const VERSION: SaveVersionEntry = {
    id: 11,
    file_name: "save.srm",
    emulator: "mgba",
    updated_at: "2025-06-14T10:00:00Z",
    file_size_bytes: 1024,
    device_syncs: [],
    uploaded_by_us: true,
  };

  it("returns true on ok and put_failed, false on refusal, conflict, or throw", async () => {
    const showConflict = vi.fn().mockResolvedValue("resolved");
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-14T10:30:00Z"));
    try {
      vi.mocked(backend.savesRollbackToVersion).mockResolvedValueOnce({ status: "ok" });
      await expect(executeVersionRestore(7, "default", VERSION, showConflict)).resolves.toBe(true);
      expect(toast.showToast).toHaveBeenCalledWith("Save restored from 30m ago");
    } finally {
      vi.useRealTimers();
    }

    vi.mocked(backend.savesRollbackToVersion).mockResolvedValueOnce({ status: "put_failed", message: "503" });
    await expect(executeVersionRestore(7, "default", VERSION, showConflict)).resolves.toBe(true);

    vi.mocked(backend.savesRollbackToVersion).mockResolvedValueOnce({ status: "version_deleted" });
    await expect(executeVersionRestore(7, "default", VERSION, showConflict)).resolves.toBe(false);

    vi.mocked(toast.showToast).mockClear();
    vi.mocked(backend.savesRollbackToVersion).mockResolvedValueOnce({ status: "unrecognized" as never });
    await expect(executeVersionRestore(7, "default", VERSION, showConflict)).resolves.toBe(false);
    await new Promise((r) => setTimeout(r, 0));
    expect(toast.showToast).not.toHaveBeenCalled();

    vi.mocked(backend.savesRollbackToVersion).mockRejectedValueOnce(new Error("boom"));
    await expect(executeVersionRestore(7, "default", VERSION, showConflict)).resolves.toBe(false);
    expect(backend.logWarn).toHaveBeenCalledWith(
      "saveSlotFlow: restore error for rom 7 slot 'default' save 11: Error: boom",
    );
    expect(toast.showToast).toHaveBeenCalledWith("Couldn't confirm the restore.", { subtext: "boom" });

    vi.mocked(backend.savesRollbackToVersion).mockResolvedValueOnce({
      status: "conflict_blocked",
      conflicts: [CONFLICT],
    });
    await expectConflictRejectionPropagates((cb) => executeVersionRestore(7, "default", VERSION, cb));
  });

  it("returns false once the conflict prompt for a conflict_blocked restore resolves", async () => {
    const showConflict = vi.fn().mockResolvedValue("resolved");
    vi.mocked(backend.savesRollbackToVersion).mockResolvedValueOnce({
      status: "conflict_blocked",
      conflicts: [CONFLICT],
    });
    await expect(executeVersionRestore(7, "default", VERSION, showConflict)).resolves.toBe(false);
    expect(showConflict).toHaveBeenCalledWith(CONFLICT);
  });
});

describe("executeDeleteLocalSaves", () => {
  it("toasts, updates shared display, and dispatches save_sync on success", async () => {
    vi.mocked(backend.deleteLocalSaves).mockResolvedValueOnce({
      success: true,
      deleted_count: 2,
      message: "Deleted 2",
    });
    const events: unknown[] = [];
    const onEvt = (e: Event) => events.push((e as CustomEvent).detail);
    globalThis.addEventListener("romm_data_changed", onEvt);
    try {
      await executeDeleteLocalSaves(100, 7);
      expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("Deleted 2");
      expect(gameDetailStore.noteSaveSyncDisplay).toHaveBeenCalledExactlyOnceWith(100, 7, {
        status: "none",
        label: "No saves",
        last_sync_check_at: null,
      });
      expect(events).toEqual([{ type: "save_sync", rom_id: 7 }]);
    } finally {
      globalThis.removeEventListener("romm_data_changed", onEvt);
    }
  });

  it("toasts result.message or fallback on failure, and logs+toasts on throw", async () => {
    vi.mocked(backend.deleteLocalSaves).mockResolvedValueOnce({ success: false, deleted_count: 0, message: "denied" });
    await executeDeleteLocalSaves(100, 7);
    expect(backend.logWarn).toHaveBeenCalledWith("saveSlotFlow: deleteLocalSaves for rom 7 failed: denied");
    expect(toast.showToast).toHaveBeenCalledWith("denied");

    vi.mocked(backend.deleteLocalSaves).mockResolvedValueOnce({ success: false, deleted_count: 0, message: "" });
    await executeDeleteLocalSaves(100, 7);
    expect(backend.logWarn).toHaveBeenCalledWith("saveSlotFlow: deleteLocalSaves for rom 7 failed: ");
    expect(toast.showToast).toHaveBeenCalledWith("Failed to delete saves");

    vi.mocked(backend.deleteLocalSaves).mockRejectedValueOnce(new Error("io"));
    await executeDeleteLocalSaves(100, 7);
    expect(backend.logWarn).toHaveBeenCalledWith("saveSlotFlow: deleteLocalSaves for rom 7 threw: Error: io");
    expect(toast.showToast).toHaveBeenCalledWith("Failed to delete saves");
  });
});
