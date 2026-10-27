import { describe, it, expect } from "vitest";
import {
  CREATE_SLOT_ERROR_MESSAGE,
  DELETE_SLOT_ERROR_MESSAGE,
  SLOT_DELETE_INFO_ERROR_MESSAGE,
  SWITCH_SLOT_ERROR_MESSAGE,
  copyToSlotFeedback,
  createSlotFailureMessage,
  partialSlotSwitchMessage,
  formatSlotDeleteDescription,
  formatSlotDeleteLines,
  rollbackFeedback,
  slotDeletedMessage,
  strandedVersionNotice,
  switchSlotFailureMessage,
  unsupportedCopyMessage,
  unsupportedRestoreMessage,
} from "./saveSlotWording";
import type { EndpointFailure, SlotDeleteInfo, SyncConflict, WireReason } from "../types";

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

  it("formats upload_failed as a copy made on this device that the next sync uploads or asks about", () => {
    expect(copyToSlotFeedback({ status: "upload_failed", message: "server 500" }, "speedrun")).toEqual({
      kind: "toast",
      message:
        "Save copied to slot 'speedrun' on this device, but not uploaded to RomM yet. The next sync uploads it or asks you which to keep.",
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

  it("words a running game by the backend's message, which names the game", () => {
    const running = "Celeste is running. Quit it first; its saves sync when you quit it.";
    expect(switchSlotFailureMessage("game_running", running)).toBe(running);
  });

  it("answers an unknown reason with the fallback, even when the backend sent a message", () => {
    expect(switchSlotFailureMessage("unknown", "Specific backend error")).toBe("Failed to switch slot");
    expect(switchSlotFailureMessage("unknown", "Specific backend error", "Failed custom")).toBe("Failed custom");
    expect(switchSlotFailureMessage(undefined)).toBe("Failed to switch slot");
  });
});

describe("createSlotFailureMessage", () => {
  it("words pending_uploads as a switch does", () => {
    expect(createSlotFailureMessage("pending_uploads")).toBe(
      "Sync your saves first — local changes haven't been uploaded",
    );
  });

  it("says a slot can't be created where RomM is not reachable", () => {
    expect(createSlotFailureMessage("server_unreachable")).toBe("Can't create a slot — RomM server is not reachable");
  });

  it("tells the reader to download the game first where it is not installed", () => {
    expect(createSlotFailureMessage("not_installed")).toBe("Can't create a slot — download the game first");
  });

  it("words a running game by the backend's message, which names the game", () => {
    const running = "Celeste is running. Quit it first; its saves sync when you quit it.";
    expect(createSlotFailureMessage("game_running", running)).toBe(running);
  });

  it("says the slot could not be created for every other reason, the ones a switch words included", () => {
    const reasons: WireReason[] = ["savefiles_in_content_dir", "save_shape_unsupported", "not_found"];
    for (const reason of reasons) {
      expect(createSlotFailureMessage(reason)).toBe("Failed to create slot");
    }
  });
});

describe("the slot switch and delete sentences", () => {
  it("names the version whose saves haven't been synced", () => {
    expect(strandedVersionNotice({ name: "Japan", stranding: "unsynced" })).toBe(
      'Version "Japan" has saves that haven\'t been synced to RomM — switch back to sync them.',
    );
  });

  it("says when the version's saves could not be checked", () => {
    expect(strandedVersionNotice({ name: "Japan", stranding: "unchecked" })).toBe(
      "Tender couldn't check whether version \"Japan\" has saves that haven't been synced to RomM — switch back to " +
        "check and sync them.",
    );
  });

  it("names the deleted slot as given", () => {
    expect(slotDeletedMessage("old")).toBe("Slot 'old' deleted");
  });

  it("keeps each error sentence", () => {
    expect(SWITCH_SLOT_ERROR_MESSAGE).toBe("An error occurred while switching slots");
    expect(CREATE_SLOT_ERROR_MESSAGE).toBe("An error occurred while creating the slot");
    expect(SLOT_DELETE_INFO_ERROR_MESSAGE).toBe("Failed to load slot info");
    expect(DELETE_SLOT_ERROR_MESSAGE).toBe("An error occurred while deleting the slot");
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
  it("is a toast naming its reason, for a copy and for a restore", () => {
    const refusal = { success: false, reason: "blocked_by_update", message: "An update is being installed" } as never;
    expect(copyToSlotFeedback(refusal, "backup")).toEqual({
      kind: "toast",
      message: "Couldn't copy the save: An update is being installed",
    });
    expect(rollbackFeedback(refusal, null)).toEqual({
      kind: "toast",
      message: "Couldn't restore the save: An update is being installed",
    });
  });
});

describe("a restore or a copy the endpoint refused", () => {
  const refusals: EndpointFailure[] = [
    {
      success: false,
      reason: "blocked_by_update",
      message: "Tender is installing an update and will restart in a moment.",
    },
    {
      success: false,
      reason: "blocked_by_migration",
      message: "Pending RetroDECK migration. Open the Tender menu (QAM) to migrate or dismiss.",
    },
    {
      success: false,
      reason: "prune_active",
      message: "A removed-game cleanup is in progress; wait for it to finish before changing local game data.",
    },
  ];

  it.each(refusals)("a refusal by the conflict rules ($reason) names its reason on a restore", (refusal) => {
    expect(rollbackFeedback(refusal, null)).toEqual({
      kind: "toast",
      message: `Couldn't restore the save: ${refusal.message}`,
    });
  });

  it.each(refusals)("a refusal by the conflict rules ($reason) names its reason on a copy", (refusal) => {
    expect(copyToSlotFeedback(refusal, "backup")).toEqual({
      kind: "toast",
      message: `Couldn't copy the save: ${refusal.message}`,
    });
  });

  it("a restore whose download RomM answered with an error names RomM's answer", () => {
    const refused: EndpointFailure = {
      success: false,
      reason: "server_error",
      message: "RomM reported an error (HTTP 500) — Internal Server Error",
    };
    expect(rollbackFeedback(refused, "2025-06-14T10:00:00Z")).toEqual({
      kind: "toast",
      message: "Couldn't restore the save: RomM reported an error (HTTP 500) — Internal Server Error",
    });
  });

  it("an answer with neither a status nor a failure is still no feedback", () => {
    const unknown = { success: true } as never;
    expect(rollbackFeedback(unknown, null)).toBeNull();
    expect(copyToSlotFeedback(unknown, "backup")).toBeNull();
  });
});

describe("the reviewer's edges", () => {
  it("a save_shape_unsupported switch with no message answers the fallback", () => {
    expect(switchSlotFailureMessage("save_shape_unsupported")).toBe("Failed to switch slot");
    expect(switchSlotFailureMessage("save_shape_unsupported", "")).toBe("Failed to switch slot");
  });

  it("a copy refused as unsupported says why", () => {
    expect(copyToSlotFeedback({ status: "unsupported", reason: "savefiles_in_content_dir" }, "backup")).toEqual({
      kind: "toast",
      message: "Save sync is off for this game: its saves are written beside the game file.",
    });
  });
});

describe("rollbackFeedback for a restore that RomM or this device refused", () => {
  it("words a version this device could not write", () => {
    const message = "it could not be written on this device (No space left on device)";
    expect(rollbackFeedback({ status: "write_failed", message }, null)).toEqual({
      kind: "toast",
      message: `Couldn't restore the save: ${message}`,
    });
  });

  it("words what RomM answered the listing with", () => {
    const message = "RomM is rate-limiting requests — try again in a moment";
    expect(rollbackFeedback({ status: "romm_refused", message }, null)).toEqual({
      kind: "toast",
      message: `Couldn't restore the save: ${message}`,
    });
  });
});

describe("a slot switch whose backup failed", () => {
  it("says nothing was moved where the first backup failed", () => {
    expect(switchSlotFailureMessage("backup_failed", "Could not move a.srm (denied). Nothing was moved.")).toBe(
      "Can't switch — a save couldn't be moved to .romm-backup, nothing was moved",
    );
    expect(createSlotFailureMessage("backup_failed")).toBe(
      "Can't create a slot — a save couldn't be moved to .romm-backup, nothing was moved",
    );
  });

  it("names the saves a switch that stopped partway had already moved", () => {
    expect(partialSlotSwitchMessage("switch_stopped", ["pokemon.srm", "pokemon.rtc"], "activate")).toBe(
      "The slot wasn't switched: a save couldn't be moved to .romm-backup. " +
        "Already moved there: pokemon.srm, pokemon.rtc.",
    );
  });

  it("says a new slot wasn't created where creating it stopped partway", () => {
    expect(partialSlotSwitchMessage("switch_stopped", ["pokemon.srm"], "create")).toBe(
      "The slot wasn't created: a save couldn't be moved to .romm-backup. Already moved there: pokemon.srm.",
    );
  });

  it("says a switch that downloaded only part of the new slot switched", () => {
    expect(partialSlotSwitchMessage("switch_incomplete", [], "activate")).toBe(
      "Switched slots, but not every save of the new slot downloaded. Sync Save Files to finish.",
    );
  });

  it("has no partial sentence for any other answer", () => {
    const reasons: WireReason[] = ["backup_failed", "pending_uploads", "server_unreachable", "not_found"];
    for (const reason of reasons) {
      expect(partialSlotSwitchMessage(reason, [], "activate")).toBeNull();
    }
  });
});

describe("a slot switch whose backup folder can't take backups", () => {
  it("says the folder can't take backups and nothing was moved", () => {
    expect(switchSlotFailureMessage("backup_folder_unusable", "Could not use the .romm-backup folder (denied).")).toBe(
      "Can't switch — the .romm-backup folder can't take backups, nothing was moved",
    );
    expect(createSlotFailureMessage("backup_folder_unusable")).toBe(
      "Can't create a slot — the .romm-backup folder can't take backups, nothing was moved",
    );
  });
});
