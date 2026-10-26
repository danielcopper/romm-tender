import { describe, it, expect, vi, beforeEach } from "vitest";
import { executeSlotDelete, executeSlotSwitch, findStrandedVersion, loadSlotDeleteInfo } from "./saveSlotFlow";
import * as backend from "../api/backend";
import * as connectionState from "./connectionState";
import * as toast from "./toast";
import type { SaveStranding, VersionInfo, VersionList } from "../api/backend";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import type { SaveStatus, SlotDeleteInfo } from "../types";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  checkLocalDrift: vi.fn(),
  debugLog: vi.fn(() => Promise.resolve()),
  deleteSlot: vi.fn(),
  getSlotDeleteInfo: vi.fn(),
  getVersionList: vi.fn(),
  logWarn: vi.fn(),
  switchSlot: vi.fn(),
}));

vi.mock("./connectionState", () => ({
  reportServerReachable: vi.fn(),
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
      message: "Can't switch — RomM server is not reachable",
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
          "switch_incomplete",
          "sync_disabled",
          "savefiles_in_content_dir",
        ] as const
      ).map((reason) => ({ purpose, reason })),
    ),
  )("tells the connection store nothing for a $reason failure answer ($purpose)", async ({ purpose, reason }) => {
    vi.mocked(backend.switchSlot).mockResolvedValueOnce({ success: false, reason, message: "Not switched" });

    await expect(executeSlotSwitch(7, "main", purpose)).resolves.toMatchObject({ ok: false, reason });
    expect(connectionState.reportServerReachable).not.toHaveBeenCalled();
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
    vi.mocked(backend.switchSlot).mockRejectedValueOnce(new Error("socket closed"));
    await expect(executeSlotSwitch(7, "main", "activate")).resolves.toEqual({
      ok: false,
      reason: null,
      message: "An error occurred while switching slots",
    });
    expect(backend.debugLog).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: switchSlot to 'main' (activate) threw: Error: socket closed",
    );

    vi.mocked(backend.debugLog).mockClear();
    vi.mocked(backend.switchSlot).mockRejectedValueOnce(new Error("socket closed"));
    await expect(executeSlotSwitch(7, "new", "create")).resolves.toEqual({
      ok: false,
      reason: null,
      message: "An error occurred while creating the slot",
    });
    expect(backend.debugLog).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: switchSlot to 'new' (create) threw: Error: socket closed",
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
    vi.mocked(backend.getSlotDeleteInfo).mockResolvedValueOnce({
      success: false,
      reason: "not_found",
      message: "No such slot",
    });

    await expect(loadSlotDeleteInfo(7, "old")).resolves.toBeNull();
    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("No such slot");
  });

  it("toasts that the info could not be loaded when the call throws, and logs it", async () => {
    vi.mocked(backend.getSlotDeleteInfo).mockRejectedValueOnce(new Error("socket closed"));

    await expect(loadSlotDeleteInfo(7, "old")).resolves.toBeNull();
    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("Failed to load slot info");
    expect(backend.debugLog).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: getSlotDeleteInfo for 'old' threw: Error: socket closed",
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
    vi.mocked(backend.deleteSlot).mockResolvedValueOnce({
      success: false,
      reason: "server_unreachable",
      message: "RomM did not answer",
    });

    await expect(executeSlotDelete(7, "old")).resolves.toBe(false);
    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("RomM did not answer");
  });

  it("toasts the error sentence when the call throws, logs it, and answers false", async () => {
    vi.mocked(backend.deleteSlot).mockRejectedValueOnce(new Error("socket closed"));

    await expect(executeSlotDelete(7, "old")).resolves.toBe(false);
    expect(toast.showToast).toHaveBeenCalledExactlyOnceWith("An error occurred while deleting the slot");
    expect(backend.debugLog).toHaveBeenCalledExactlyOnceWith(
      "saveSlotFlow: deleteSlot for 'old' threw: Error: socket closed",
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

  it.each(["activate", "create"] as const)(
    "words a switch_stopped answer with the saves set aside and reloads the SAVES tab ($0)",
    async (purpose) => {
      vi.mocked(backend.switchSlot).mockResolvedValueOnce({
        success: false,
        reason: "switch_stopped",
        message: "Could not move pokemon.rtc into .romm-backup.",
        set_aside: ["pokemon.srm"],
      });
      const events = listenForDataChanges();
      try {
        await expect(executeSlotSwitch(7, "main", purpose)).resolves.toEqual({
          ok: false,
          reason: "switch_stopped",
          message:
            "The slot wasn't switched: a save couldn't be moved to .romm-backup. Already moved there: pokemon.srm.",
          partial: true,
        });
      } finally {
        events.stop();
      }
      expect(events.details).toEqual([{ type: "save_sync", rom_id: 7 }]);
    },
  );

  it.each(["activate", "create"] as const)(
    "words a switch_incomplete answer as a switch and reloads the SAVES tab ($0)",
    async (purpose) => {
      vi.mocked(backend.switchSlot).mockResolvedValueOnce({
        success: false,
        reason: "switch_incomplete",
        message: "Switched to slot but 1 save(s) failed to download — retry",
      });
      const events = listenForDataChanges();
      try {
        await expect(executeSlotSwitch(7, "main", purpose)).resolves.toEqual({
          ok: false,
          reason: "switch_incomplete",
          message:
            "Switched slots, but not every save of the new slot downloaded. Sync this game to download the rest.",
          partial: true,
        });
      } finally {
        events.stop();
      }
      expect(events.details).toEqual([{ type: "save_sync", rom_id: 7 }]);
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
