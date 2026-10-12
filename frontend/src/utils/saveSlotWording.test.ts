import { describe, it, expect } from "vitest";
import {
  copyToSlotFeedback,
  formatSlotDeleteDescription,
  formatSlotDeleteLines,
  rollbackFeedback,
  switchSlotFailureMessage,
  unsupportedCopyMessage,
  unsupportedRestoreMessage,
} from "./saveSlotWording";
import type { SlotDeleteInfo, SyncConflict } from "../types";

describe("unsupportedRestoreMessage", () => {
  it("says the saves are written beside the game file for savefiles_in_content_dir", () => {
    expect(unsupportedRestoreMessage({ status: "unsupported", reason: "savefiles_in_content_dir" })).toBe(
      "Save sync is off for this game: its saves are written beside the game file.",
    );
  });

  it("shows the backend's own explanation for save_shape_unsupported", () => {
    const message = "Save sync is unavailable: this emulator writes saves inside the game file itself.";
    expect(unsupportedRestoreMessage({ status: "unsupported", reason: "save_shape_unsupported", message })).toBe(
      message,
    );
  });

  it("falls back to the multi-file sentence for save_shape_unsupported without a message", () => {
    expect(unsupportedRestoreMessage({ status: "unsupported", reason: "save_shape_unsupported" })).toBe(
      "Restoring isn't available for multi-file saves yet.",
    );
  });

  it("names the multi-file refusal when the backend gives no reason", () => {
    expect(unsupportedRestoreMessage({ status: "unsupported" })).toBe(
      "Restoring isn't available for multi-file saves yet.",
    );
  });
});

describe("unsupportedCopyMessage", () => {
  it("says the saves are written beside the game file for savefiles_in_content_dir", () => {
    expect(unsupportedCopyMessage({ status: "unsupported", reason: "savefiles_in_content_dir" })).toBe(
      "Save sync is off for this game: its saves are written beside the game file.",
    );
  });

  it("shows the backend's own explanation for save_shape_unsupported", () => {
    const message = "Copying is unavailable for this save shape.";
    expect(unsupportedCopyMessage({ status: "unsupported", reason: "save_shape_unsupported", message })).toBe(message);
  });

  it("falls back to the multi-file sentence for unsupported without a message", () => {
    expect(unsupportedCopyMessage({ status: "unsupported" })).toBe("Copying isn't available for multi-file saves yet.");
  });
});

describe("copyToSlotFeedback", () => {
  it("formats ok status", () => {
    expect(copyToSlotFeedback({ status: "ok" }, "speedrun")).toEqual({
      kind: "toast",
      message: "Save copied to slot 'speedrun'",
    });
  });

  it("formats ok status with empty target as Manual archive", () => {
    expect(copyToSlotFeedback({ status: "ok" }, "")).toEqual({
      kind: "toast",
      message: "Save copied to slot 'Manual archive'",
    });
  });

  it("formats already_present status", () => {
    expect(copyToSlotFeedback({ status: "already_present", existing_id: 12 }, "speedrun")).toEqual({
      kind: "toast",
      message: "Already in slot 'speedrun' as #12",
    });
  });

  it("formats conflict_blocked status with a conflict", () => {
    const conflict: SyncConflict = {
      type: "sync_conflict",
      rom_id: 1,
      filename: "save.srm",
      server_save_id: 2,
      server_updated_at: "2026-01-02",
      server_size: 100,
      local_path: "/path",
      local_hash: "h",
      local_mtime: "2026-01-01",
      local_size: 100,
      created_at: "2026-01-02",
    };
    expect(copyToSlotFeedback({ status: "conflict_blocked", conflicts: [conflict] }, "slotA")).toEqual({
      kind: "conflict",
      conflict,
    });
  });

  it("formats conflict_blocked status without conflicts", () => {
    expect(copyToSlotFeedback({ status: "conflict_blocked", conflicts: [] }, "slotA")).toEqual({
      kind: "toast",
      message: "Copy blocked by a sync conflict. Sync this save, then try again.",
    });
  });

  it("formats target_slot_busy status", () => {
    expect(copyToSlotFeedback({ status: "target_slot_busy", message: "busy" }, "slotB")).toEqual({
      kind: "toast",
      message: "Slot 'slotB' has newer changes on another device — sync it first, then copy again.",
    });
  });

  it("formats preflight_failed status", () => {
    expect(copyToSlotFeedback({ status: "preflight_failed", errors: ["network err"] }, "slotB")).toEqual({
      kind: "toast",
      message: "Sync failed before copy: network err",
    });
  });

  it("formats server_unreachable, not_found, version_deleted, rom_not_installed, not_configured, copy_failed, invalid_slot_name", () => {
    expect(copyToSlotFeedback({ status: "server_unreachable", message: "unreachable" }, "s")).toEqual({
      kind: "toast",
      message: "Couldn't reach RomM. Check your connection and try again.",
    });
    expect(copyToSlotFeedback({ status: "not_found", message: "not found" }, "s")).toEqual({
      kind: "toast",
      message: "RomM couldn't find this game's save data — nothing was copied.",
    });
    expect(copyToSlotFeedback({ status: "version_deleted" }, "s")).toEqual({
      kind: "toast",
      message: "This save no longer exists on the server.",
    });
    expect(copyToSlotFeedback({ status: "rom_not_installed" }, "s")).toEqual({
      kind: "toast",
      message: "ROM is no longer installed locally. Reinstall and try again.",
    });
    expect(copyToSlotFeedback({ status: "not_configured" }, "s")).toEqual({
      kind: "toast",
      message: "Set up save slots for this game first, then copy.",
    });
    expect(copyToSlotFeedback({ status: "copy_failed", message: "disk full" }, "s")).toEqual({
      kind: "toast",
      message: "Couldn't copy the save: disk full",
    });
    expect(copyToSlotFeedback({ status: "invalid_slot_name" }, "s")).toEqual({
      kind: "toast",
      message: "Enter a valid slot name.",
    });
  });
});

describe("rollbackFeedback", () => {
  it("formats ok status", () => {
    expect(rollbackFeedback({ status: "ok" }, new Date().toISOString())).toEqual({
      kind: "toast",
      message: "Save restored from just now",
    });
  });

  it("formats conflict_blocked status with a conflict", () => {
    const conflict: SyncConflict = {
      type: "sync_conflict",
      rom_id: 1,
      filename: "save.srm",
      server_save_id: 2,
      server_updated_at: "2026-01-02",
      server_size: 100,
      local_path: "/path",
      local_hash: "h",
      local_mtime: "2026-01-01",
      local_size: 100,
      created_at: "2026-01-02",
    };
    expect(rollbackFeedback({ status: "conflict_blocked", conflicts: [conflict] }, null)).toEqual({
      kind: "conflict",
      conflict,
    });
  });

  it("formats conflict_blocked status without conflicts", () => {
    expect(rollbackFeedback({ status: "conflict_blocked", conflicts: [] }, null)).toEqual({
      kind: "toast",
      message: "Restore blocked by a sync conflict. Sync this save, then try again.",
    });
  });

  it("formats preflight_failed and put_failed status", () => {
    expect(rollbackFeedback({ status: "preflight_failed", errors: ["err"] }, null)).toEqual({
      kind: "toast",
      message: "Sync failed before restore: err",
    });
    expect(rollbackFeedback({ status: "put_failed", message: "failed" }, null)).toEqual({
      kind: "toast",
      message:
        "Restored locally, but the server didn't update. Other devices will see the previous version until you retry.",
    });
  });

  it("formats server_unreachable, not_found, version_deleted, rom_not_installed, unsupported", () => {
    expect(rollbackFeedback({ status: "server_unreachable", message: "unreachable" }, null)).toEqual({
      kind: "toast",
      message: "Couldn't reach RomM. Check your connection and try again.",
    });
    expect(rollbackFeedback({ status: "not_found", message: "not found" }, null)).toEqual({
      kind: "toast",
      message: "RomM couldn't find this game's save data — nothing was restored.",
    });
    expect(rollbackFeedback({ status: "version_deleted" }, null)).toEqual({
      kind: "toast",
      message: "This version no longer exists on the server",
    });
    expect(rollbackFeedback({ status: "rom_not_installed" }, null)).toEqual({
      kind: "toast",
      message: "ROM is no longer installed locally. Reinstall and try again.",
    });
    expect(rollbackFeedback({ status: "unsupported" }, null)).toEqual({
      kind: "toast",
      message: "Restoring isn't available for multi-file saves yet.",
    });
  });
});

describe("switchSlotFailureMessage", () => {
  it("formats known reasons", () => {
    expect(switchSlotFailureMessage("pending_uploads")).toBe(
      "Sync your saves first — local changes haven't been uploaded",
    );
    expect(switchSlotFailureMessage("server_unreachable")).toBe("Can't switch — RomM server is not reachable");
    expect(switchSlotFailureMessage("not_installed")).toBe("Can't switch — download the game first");
    expect(switchSlotFailureMessage("savefiles_in_content_dir")).toBe(
      "Can't switch — this game's saves are written beside the game file",
    );
    expect(switchSlotFailureMessage("save_shape_unsupported", "custom shape msg")).toBe("custom shape msg");
  });

  it("answers an unknown reason with the fallback, even when the backend sent a message", () => {
    expect(switchSlotFailureMessage("unknown", "Specific backend error")).toBe("Failed to switch slot");
    expect(switchSlotFailureMessage("unknown", "Specific backend error", "Failed custom")).toBe("Failed custom");
    expect(switchSlotFailureMessage(undefined)).toBe("Failed to switch slot");
  });
});

describe("formatSlotDeleteLines and formatSlotDeleteDescription", () => {
  it("formats server slot delete lines with pluralization", () => {
    const info: SlotDeleteInfo = {
      success: true,
      slot: "speedrun",
      source: "server",
      server_save_count: 3,
      server_save_ids: [1, 2, 3],
      local_file_count: 1,
      local_filenames: ["speedrun.srm"],
      is_active: false,
    };
    const lines = formatSlotDeleteLines(info);
    expect(lines).toEqual([
      "This will permanently delete 3 saves from slot 'speedrun' on the RomM server.",
      "1 tracked file will be unlinked.",
    ]);
    expect(formatSlotDeleteDescription(info)).toBe(
      "This will permanently delete 3 saves from slot 'speedrun' on the RomM server.\n\n1 tracked file will be unlinked.\n\nThis cannot be undone.",
    );
  });

  it("formats singular server save and multiple local files", () => {
    const info: SlotDeleteInfo = {
      success: true,
      slot: "single",
      source: "server",
      server_save_count: 1,
      server_save_ids: [1],
      local_file_count: 2,
      local_filenames: ["a.srm", "b.srm"],
      is_active: false,
    };
    expect(formatSlotDeleteLines(info)).toEqual([
      "This will permanently delete 1 save from slot 'single' on the RomM server.",
      "2 tracked files will be unlinked.",
    ]);
  });

  it("formats local-only slot delete", () => {
    const info: SlotDeleteInfo = {
      success: true,
      slot: "localonly",
      source: "local",
      server_save_count: 0,
      server_save_ids: [],
      local_file_count: 0,
      local_filenames: [],
      is_active: false,
    };
    expect(formatSlotDeleteLines(info)).toEqual(["This will remove slot 'localonly' from your local configuration."]);
  });
});

describe("a refusal with no status", () => {
  it("is no feedback for a copy or a restore", () => {
    const refusal = { success: false, reason: "blocked_by_update", message: "An update is being installed" } as never;
    expect(copyToSlotFeedback(refusal, "backup")).toBeNull();
    expect(rollbackFeedback(refusal, null)).toBeNull();
  });
});
