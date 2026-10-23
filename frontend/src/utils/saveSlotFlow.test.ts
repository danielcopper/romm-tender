import { describe, it, expect, vi, beforeEach } from "vitest";
import { executeSlotDelete, executeSlotSwitch, loadSlotDeleteInfo } from "./saveSlotFlow";
import * as backend from "../api/backend";
import * as connectionState from "./connectionState";
import * as toast from "./toast";
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

beforeEach(() => {
  vi.clearAllMocks();
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
