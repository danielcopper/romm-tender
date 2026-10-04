import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  computeSyncSummary,
  displaySlot,
  formatRelativeTime,
  pickLastSyncer,
  attributionLabel,
  formatAttributionSegment,
  statusLabel,
  unsupportedRestoreMessage,
  unsupportedCopyMessage,
  formatCopySaveToSlotFeedback,
  formatRollbackFeedback,
  switchSlotFailureMessage,
  formatSlotDeleteLines,
  formatSlotDeleteDescription,
  sortSaveSlots,
  filterSaveSlotsForDisplay,
} from "./saveHelpers";
import type { DeviceSyncInfo, SaveStatus, SyncConflict, SlotDeleteInfo, SaveSlotSummary } from "../types";

describe("displaySlot", () => {
  it("returns 'Manual archive' for null", () => {
    expect(displaySlot(null)).toBe("Manual archive");
  });

  it("returns 'Manual archive' for undefined", () => {
    expect(displaySlot(undefined)).toBe("Manual archive");
  });

  it("returns 'Manual archive' for empty string", () => {
    expect(displaySlot("")).toBe("Manual archive");
  });

  it("returns the slot name as-is for non-empty input", () => {
    expect(displaySlot("speedrun")).toBe("speedrun");
  });
});

describe("formatRelativeTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-15T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("returns empty string for null", () => {
    expect(formatRelativeTime(null)).toBe("");
  });

  it("returns empty string for empty input", () => {
    expect(formatRelativeTime("")).toBe("");
  });

  it("returns empty string for an unparseable timestamp", () => {
    expect(formatRelativeTime("not-a-date")).toBe("");
  });

  it("returns 'just now' for under one minute", () => {
    expect(formatRelativeTime("2025-06-15T11:59:30Z")).toBe("just now");
  });

  it("returns 'Nm ago' for minute granularity", () => {
    expect(formatRelativeTime("2025-06-15T11:30:00Z")).toBe("30m ago");
  });

  it("returns 'Nh ago' for hour granularity", () => {
    expect(formatRelativeTime("2025-06-15T08:00:00Z")).toBe("4h ago");
  });

  it("returns 'D Mon' for older dates", () => {
    // 10 days back puts us in early June; only the day + month tokens matter.
    const out = formatRelativeTime("2025-06-05T12:00:00Z");
    expect(out).toBe("5 Jun");
  });
});

describe("pickLastSyncer", () => {
  it("returns null for undefined", () => {
    expect(pickLastSyncer(undefined)).toBeNull();
  });

  it("returns null for empty array", () => {
    expect(pickLastSyncer([])).toBeNull();
  });

  it("returns the only entry when array has one element", () => {
    const ds: DeviceSyncInfo = { device_name: "deck", last_synced_at: "2025-06-15T10:00:00Z" } as DeviceSyncInfo;
    expect(pickLastSyncer([ds])).toBe(ds);
  });

  it("returns the entry with the latest last_synced_at", () => {
    const a: DeviceSyncInfo = { device_name: "older", last_synced_at: "2025-06-10T10:00:00Z" } as DeviceSyncInfo;
    const b: DeviceSyncInfo = { device_name: "newer", last_synced_at: "2025-06-15T10:00:00Z" } as DeviceSyncInfo;
    expect(pickLastSyncer([a, b])).toBe(b);
    expect(pickLastSyncer([b, a])).toBe(b);
  });

  it("ignores entries with null last_synced_at when picking the latest", () => {
    const withTime: DeviceSyncInfo = { device_name: "real", last_synced_at: "2025-06-15T10:00:00Z" } as DeviceSyncInfo;
    const noTime: DeviceSyncInfo = { device_name: "ghost", last_synced_at: null } as DeviceSyncInfo;
    expect(pickLastSyncer([withTime, noTime])).toBe(withTime);
    expect(pickLastSyncer([noTime, withTime])).toBe(withTime);
  });

  it("falls back to first entry when all entries have null last_synced_at", () => {
    const a: DeviceSyncInfo = { device_name: "a", last_synced_at: null } as DeviceSyncInfo;
    const b: DeviceSyncInfo = { device_name: "b", last_synced_at: null } as DeviceSyncInfo;
    expect(pickLastSyncer([a, b])).toBe(a);
  });
});

describe("attributionLabel", () => {
  it("returns '(this device)' for true", () => {
    expect(attributionLabel(true)).toBe("(this device)");
  });

  it("returns '(not this device)' for false", () => {
    expect(attributionLabel(false)).toBe("(not this device)");
  });

  it("returns null for null", () => {
    expect(attributionLabel(null)).toBeNull();
  });

  it("returns null for undefined", () => {
    expect(attributionLabel(undefined)).toBeNull();
  });
});

describe("formatAttributionSegment", () => {
  it("combines device name and '(this device)' label when uploadedByUs is true", () => {
    expect(formatAttributionSegment(true, "deck")).toBe("deck (this device) ✓");
  });

  it("returns the '(this device)' label alone when uploadedByUs is true and no device name", () => {
    expect(formatAttributionSegment(true, null)).toBe("(this device) ✓");
  });

  it("returns '(not this device)' alone when uploadedByUs is false, even when a device name is provided", () => {
    // The function intentionally drops the device name in the "false" branch:
    // lastSyncer is our own sync record, not the actual uploader.
    expect(formatAttributionSegment(false, "deck")).toBe("(not this device) ✓");
    expect(formatAttributionSegment(false, null)).toBe("(not this device) ✓");
  });

  it("returns 'device ✓' when uploadedByUs is unknown and a device name is provided", () => {
    expect(formatAttributionSegment(null, "deck")).toBe("deck ✓");
    expect(formatAttributionSegment(undefined, "deck")).toBe("deck ✓");
  });

  it("returns just '✓' when uploadedByUs is unknown and no device name is provided", () => {
    // Documents current behavior: the lone checkmark falls through the
    // label === null branch, so callers always get a non-null string —
    // never null — when uploadedByUs is unknown.
    expect(formatAttributionSegment(null, null)).toBe("✓");
    expect(formatAttributionSegment(undefined, undefined)).toBe("✓");
  });

  describe("showCheck=false (a not-yet-synced file must render no ✓, #1334)", () => {
    it("drops the ✓ from the '(this device)' branch", () => {
      expect(formatAttributionSegment(true, "deck", false)).toBe("deck (this device)");
      expect(formatAttributionSegment(true, null, false)).toBe("(this device)");
    });

    it("drops the ✓ from the '(not this device)' branch", () => {
      expect(formatAttributionSegment(false, "deck", false)).toBe("(not this device)");
    });

    it("drops the ✓ from the device-only branch", () => {
      expect(formatAttributionSegment(null, "deck", false)).toBe("deck");
    });

    it("returns null when there is nothing but a suppressed ✓", () => {
      expect(formatAttributionSegment(null, null, false)).toBeNull();
      expect(formatAttributionSegment(undefined, undefined, false)).toBeNull();
    });
  });
});

describe("statusLabel", () => {
  it("returns green 'Synced' for status 'synced'", () => {
    expect(statusLabel("synced", null)).toEqual({ color: "#5ba32b", label: "Synced" });
  });

  it("returns green 'Synced' for status 'skip'", () => {
    expect(statusLabel("skip", null)).toEqual({ color: "#5ba32b", label: "Synced" });
  });

  it("returns yellow 'Local changes' for status 'upload'", () => {
    expect(statusLabel("upload", null)).toEqual({ color: "#d4a72c", label: "Local changes" });
  });

  it("returns blue 'Server newer' for status 'download'", () => {
    expect(statusLabel("download", null)).toEqual({ color: "#1a9fff", label: "Server newer" });
  });

  it("returns red 'Conflict' for status 'conflict'", () => {
    expect(statusLabel("conflict", null)).toEqual({ color: "#d94126", label: "Conflict" });
  });

  it("returns grey 'Status unknown' for status 'unknown'", () => {
    expect(statusLabel("unknown", null)).toEqual({ color: "#8f98a0", label: "Status unknown" });
  });

  it("returns grey 'Status unknown' for status 'unknown' even when lastSyncAt is set", () => {
    expect(statusLabel("unknown", "2025-06-15T10:00:00Z")).toEqual({ color: "#8f98a0", label: "Status unknown" });
  });

  it("defaults to green 'Synced' when status is unknown but lastSyncAt is set", () => {
    expect(statusLabel("weird", "2025-06-15T10:00:00Z")).toEqual({ color: "#5ba32b", label: "Synced" });
  });

  it("defaults to grey 'Not synced' when status is unknown and lastSyncAt is null", () => {
    expect(statusLabel("weird", null)).toEqual({ color: "#8f98a0", label: "Not synced" });
  });
});

describe("computeSyncSummary", () => {
  const makeStatus = (overrides: Partial<SaveStatus> = {}): SaveStatus => ({
    rom_id: 1,
    files: [],
    playtime: {
      total_seconds: 0,
      session_count: 0,
      last_session_start: null,
      last_session_duration_sec: null,
      last_played: null,
    },
    device_id: "dev",
    last_sync_check_at: null,
    ...overrides,
  });

  const mkFile = (status: SaveStatus["files"][number]["status"]): SaveStatus["files"][number] => ({
    filename: "save.srm",
    local_path: "/local/save.srm",
    local_hash: "abc",
    local_mtime: "2025-06-15T10:00:00Z",
    local_size: 100,
    server_save_id: null,
    server_file_name: null,
    server_emulator: null,
    server_updated_at: null,
    server_size: null,
    last_sync_at: null,
    status,
  });

  it("returns null text for inactive slot", () => {
    expect(computeSyncSummary(false, makeStatus(), [])).toEqual({
      syncSummaryText: null,
      syncSummaryColor: "#8f98a0",
    });
  });

  it("returns null text when saveStatus is null", () => {
    expect(computeSyncSummary(true, null, [])).toEqual({
      syncSummaryText: null,
      syncSummaryColor: "#8f98a0",
    });
  });

  it("short-circuits to 'Server unreachable' when the reason is server_unreachable", () => {
    const status = makeStatus({
      server_query_failed: true,
      server_query_reason: "server_unreachable",
      files: [
        {
          filename: "save.srm",
          local_path: "/local/save.srm",
          local_hash: "abc",
          local_mtime: "2025-06-15T10:00:00Z",
          local_size: 100,
          server_save_id: null,
          server_file_name: null,
          server_emulator: null,
          server_updated_at: null,
          server_size: null,
          last_sync_at: null,
          status: "unknown",
        },
      ],
    });
    expect(computeSyncSummary(true, status, [])).toEqual({
      syncSummaryText: "Server unreachable",
      syncSummaryColor: "#8f98a0",
    });
  });

  it("does NOT claim the server is unreachable on a definitive 404", () => {
    // The Saves tab is #1560's own surface: saying "Server unreachable" for a
    // ROM the server merely answered 404 for is the exact lie #1570 removes.
    // It still must not run the matrix against the empty server list.
    const status = makeStatus({
      server_query_failed: true,
      server_query_reason: "not_found",
      files: [],
    });
    const { syncSummaryText } = computeSyncSummary(true, status, []);
    expect(syncSummaryText).not.toMatch(/unreachable|not reachable|offline/i);
    expect(syncSummaryText).not.toMatch(/no saves|has no save|without saves/i);
    expect(syncSummaryText).toBe("Save status unavailable");
  });

  it("does not claim unreachable when the query failed without a reason slug", () => {
    // Defensive: an older backend (or an unclassified failure) sends the flag
    // with no reason. Silence about the cause beats asserting the wrong one.
    const status = makeStatus({ server_query_failed: true, files: [] });
    const { syncSummaryText } = computeSyncSummary(true, status, []);
    expect(syncSummaryText).not.toMatch(/unreachable|not reachable|offline/i);
  });

  it("server_query_failed wins over conflicts", () => {
    const status = makeStatus({ server_query_failed: true, server_query_reason: "server_unreachable" });
    const conflicts: SyncConflict[] = [
      {
        type: "sync_conflict",
        rom_id: 1,
        filename: "save.srm",
        server_save_id: 1,
        server_updated_at: "2025-06-15T10:00:00Z",
        server_size: 100,
        local_path: null,
        local_hash: null,
        local_mtime: null,
        local_size: null,
        created_at: "2025-06-15T10:00:00Z",
      },
    ];
    expect(computeSyncSummary(true, status, conflicts)).toEqual({
      syncSummaryText: "Server unreachable",
      syncSummaryColor: "#8f98a0",
    });
  });

  it("returns 'Conflict detected' (red) when conflicts present", () => {
    const status = makeStatus({ files: [] });
    const conflicts: SyncConflict[] = [
      {
        type: "sync_conflict",
        rom_id: 1,
        filename: "save.srm",
        server_save_id: 1,
        server_updated_at: "2025-06-15T10:00:00Z",
        server_size: 100,
        local_path: null,
        local_hash: null,
        local_mtime: null,
        local_size: null,
        created_at: "2025-06-15T10:00:00Z",
      },
    ];
    expect(computeSyncSummary(true, status, conflicts)).toEqual({
      syncSummaryText: "Conflict detected",
      syncSummaryColor: "#d94126",
    });
  });

  it("returns 'No saves found' when fileCount is 0 and no conflicts", () => {
    expect(computeSyncSummary(true, makeStatus(), [])).toEqual({
      syncSummaryText: "No saves found",
      syncSummaryColor: "#8f98a0",
    });
  });

  it("returns 'Not synced' when files are all synced but last_sync_check_at is null", () => {
    const status = makeStatus({ files: [mkFile("synced")] });
    expect(computeSyncSummary(true, status, [])).toEqual({
      syncSummaryText: "Not synced",
      syncSummaryColor: "#8f98a0",
    });
  });

  it("returns yellow 'Local changes' when any file is pending upload (never a green ✓, #1334)", () => {
    const status = makeStatus({ last_sync_check_at: new Date().toISOString(), files: [mkFile("upload")] });
    expect(computeSyncSummary(true, status, [])).toEqual({
      syncSummaryText: "Local changes",
      syncSummaryColor: "#d4a72c",
    });
  });

  it("'Local changes' wins over an otherwise-synced sibling and a recent check", () => {
    const status = makeStatus({
      last_sync_check_at: new Date().toISOString(),
      files: [mkFile("synced"), mkFile("upload")],
    });
    expect(computeSyncSummary(true, status, [])).toEqual({
      syncSummaryText: "Local changes",
      syncSummaryColor: "#d4a72c",
    });
  });

  it("returns blue 'Server newer' when a file is pending download and none pending upload", () => {
    const status = makeStatus({
      last_sync_check_at: new Date().toISOString(),
      files: [mkFile("synced"), mkFile("download")],
    });
    expect(computeSyncSummary(true, status, [])).toEqual({
      syncSummaryText: "Server newer",
      syncSummaryColor: "#1a9fff",
    });
  });

  it("returns 'Synced just now' when relative time is 'just now'", () => {
    const nowIso = new Date().toISOString();
    const status = makeStatus({
      last_sync_check_at: nowIso,
      files: [
        {
          filename: "save.srm",
          local_path: "/local/save.srm",
          local_hash: "abc",
          local_mtime: nowIso,
          local_size: 100,
          server_save_id: null,
          server_file_name: null,
          server_emulator: null,
          server_updated_at: null,
          server_size: null,
          last_sync_at: null,
          status: "synced",
        },
      ],
    });
    expect(computeSyncSummary(true, status, [])).toEqual({
      syncSummaryText: "Synced just now",
      syncSummaryColor: "#5ba32b",
    });
  });

  it("returns 'Synced <rel>' with relative time when last_sync_check_at is older", () => {
    const oldIso = new Date(Date.now() - 30 * 60 * 1000).toISOString(); // 30 min ago
    const status = makeStatus({
      last_sync_check_at: oldIso,
      files: [
        {
          filename: "save.srm",
          local_path: "/local/save.srm",
          local_hash: "abc",
          local_mtime: oldIso,
          local_size: 100,
          server_save_id: null,
          server_file_name: null,
          server_emulator: null,
          server_updated_at: null,
          server_size: null,
          last_sync_at: null,
          status: "synced",
        },
      ],
    });
    const result = computeSyncSummary(true, status, []);
    expect(result.syncSummaryColor).toBe("#5ba32b");
    expect(result.syncSummaryText).toMatch(/^Synced \d+m ago$/);
  });
});

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

describe("formatCopySaveToSlotFeedback", () => {
  it("formats ok status", () => {
    expect(formatCopySaveToSlotFeedback({ status: "ok" }, "speedrun")).toEqual({
      kind: "toast",
      message: "Save copied to slot 'speedrun'",
    });
  });

  it("formats ok status with empty target as Manual archive", () => {
    expect(formatCopySaveToSlotFeedback({ status: "ok" }, "")).toEqual({
      kind: "toast",
      message: "Save copied to slot 'Manual archive'",
    });
  });

  it("formats already_present status", () => {
    expect(formatCopySaveToSlotFeedback({ status: "already_present", existing_id: 12 }, "speedrun")).toEqual({
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
    expect(formatCopySaveToSlotFeedback({ status: "conflict_blocked", conflicts: [conflict] }, "slotA")).toEqual({
      kind: "conflict",
      conflict,
    });
  });

  it("formats conflict_blocked status without conflicts", () => {
    expect(formatCopySaveToSlotFeedback({ status: "conflict_blocked", conflicts: [] }, "slotA")).toEqual({
      kind: "toast",
      message: "Copy blocked by a sync conflict. Sync this save, then try again.",
    });
  });

  it("formats target_slot_busy status", () => {
    expect(formatCopySaveToSlotFeedback({ status: "target_slot_busy", message: "busy" }, "slotB")).toEqual({
      kind: "toast",
      message: "Slot 'slotB' has newer changes on another device — sync it first, then copy again.",
    });
  });

  it("formats preflight_failed status", () => {
    expect(formatCopySaveToSlotFeedback({ status: "preflight_failed", errors: ["network err"] }, "slotB")).toEqual({
      kind: "toast",
      message: "Sync failed before copy: network err",
    });
  });

  it("formats server_unreachable, not_found, version_deleted, rom_not_installed, not_configured, copy_failed, invalid_slot_name", () => {
    expect(formatCopySaveToSlotFeedback({ status: "server_unreachable", message: "unreachable" }, "s")).toEqual({
      kind: "toast",
      message: "Couldn't reach RomM. Check your connection and try again.",
    });
    expect(formatCopySaveToSlotFeedback({ status: "not_found", message: "not found" }, "s")).toEqual({
      kind: "toast",
      message: "RomM couldn't find this game's save data — nothing was copied.",
    });
    expect(formatCopySaveToSlotFeedback({ status: "version_deleted" }, "s")).toEqual({
      kind: "toast",
      message: "This save no longer exists on the server.",
    });
    expect(formatCopySaveToSlotFeedback({ status: "rom_not_installed" }, "s")).toEqual({
      kind: "toast",
      message: "ROM is no longer installed locally. Reinstall and try again.",
    });
    expect(formatCopySaveToSlotFeedback({ status: "not_configured" }, "s")).toEqual({
      kind: "toast",
      message: "Set up save slots for this game first, then copy.",
    });
    expect(formatCopySaveToSlotFeedback({ status: "copy_failed", message: "disk full" }, "s")).toEqual({
      kind: "toast",
      message: "Couldn't copy the save: disk full",
    });
    expect(formatCopySaveToSlotFeedback({ status: "invalid_slot_name" }, "s")).toEqual({
      kind: "toast",
      message: "Enter a valid slot name.",
    });
  });
});

describe("formatRollbackFeedback", () => {
  it("formats ok status", () => {
    expect(formatRollbackFeedback({ status: "ok" }, new Date().toISOString())).toEqual({
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
    expect(formatRollbackFeedback({ status: "conflict_blocked", conflicts: [conflict] }, null)).toEqual({
      kind: "conflict",
      conflict,
    });
  });

  it("formats conflict_blocked status without conflicts", () => {
    expect(formatRollbackFeedback({ status: "conflict_blocked", conflicts: [] }, null)).toEqual({
      kind: "toast",
      message: "Restore blocked by a sync conflict. Sync this save, then try again.",
    });
  });

  it("formats preflight_failed and put_failed status", () => {
    expect(formatRollbackFeedback({ status: "preflight_failed", errors: ["err"] }, null)).toEqual({
      kind: "toast",
      message: "Sync failed before restore: err",
    });
    expect(formatRollbackFeedback({ status: "put_failed", message: "failed" }, null)).toEqual({
      kind: "toast",
      message:
        "Restored locally, but the server didn't update. Other devices will see the previous version until you retry.",
    });
  });

  it("formats server_unreachable, not_found, version_deleted, rom_not_installed, unsupported", () => {
    expect(formatRollbackFeedback({ status: "server_unreachable", message: "unreachable" }, null)).toEqual({
      kind: "toast",
      message: "Couldn't reach RomM. Check your connection and try again.",
    });
    expect(formatRollbackFeedback({ status: "not_found", message: "not found" }, null)).toEqual({
      kind: "toast",
      message: "RomM couldn't find this game's save data — nothing was restored.",
    });
    expect(formatRollbackFeedback({ status: "version_deleted" }, null)).toEqual({
      kind: "toast",
      message: "This version no longer exists on the server",
    });
    expect(formatRollbackFeedback({ status: "rom_not_installed" }, null)).toEqual({
      kind: "toast",
      message: "ROM is no longer installed locally. Reinstall and try again.",
    });
    expect(formatRollbackFeedback({ status: "unsupported" }, null)).toEqual({
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

describe("sortSaveSlots and filterSaveSlotsForDisplay", () => {
  const slots: SaveSlotSummary[] = [
    { slot: "beta", source: "server", count: 1, latest_updated_at: null },
    { slot: "", source: "server", count: 1, latest_updated_at: null },
    { slot: "alpha", source: "server", count: 1, latest_updated_at: null },
  ];

  it("sorts active first, alphabetical, legacy last", () => {
    const sorted = sortSaveSlots(slots, "alpha", true);
    expect(sorted.map((s) => s.slot)).toEqual(["alpha", "beta", ""]);
  });

  it("synthesizes missing active slot if known", () => {
    const sorted = sortSaveSlots(slots, "gamma", true);
    expect(sorted.map((s) => s.slot)).toEqual(["gamma", "alpha", "beta", ""]);
  });

  it("filters out legacy slot when activeSlot is null (legacy mode)", () => {
    const sorted = sortSaveSlots(slots, null, true);
    const filtered = filterSaveSlotsForDisplay(sorted, null);
    expect(filtered.map((s) => s.slot)).toEqual(["alpha", "beta"]);
  });

  it("preserves legacy slot when activeSlot is a named slot", () => {
    const sorted = sortSaveSlots(slots, "alpha", true);
    const filtered = filterSaveSlotsForDisplay(sorted, "alpha");
    expect(filtered.map((s) => s.slot)).toEqual(["alpha", "beta", ""]);
  });
});
