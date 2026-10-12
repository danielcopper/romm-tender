/**
 * The words the save-slot surfaces say: what a copy to a slot and a restore of a
 * version answer, why a slot switch failed, what deleting a slot will do, and
 * the refusals' own sentences. One home, so every surface that shows them says
 * the same thing; when and where to show them stays with each caller.
 */

import { displaySlot, formatSaveTimeAgo } from "./saveHelpers";
import type { CopySaveToSlotStatus, RollbackStatus, SlotDeleteInfo, SyncConflict } from "../types";

/** The toast for a restore refused as unsupported — the backend's own explanation where it gave one. */
export function unsupportedRestoreMessage(result: Extract<RollbackStatus, { status: "unsupported" }>): string {
  if (result.reason === "savefiles_in_content_dir") {
    return "Save sync is off for this game: its saves are written beside the game file.";
  }
  if (result.reason === "save_shape_unsupported" && result.message) {
    return result.message;
  }
  return "Restoring isn't available for multi-file saves yet.";
}

/** The toast for a copy refused as unsupported — the backend's own explanation where it gave one. */
export function unsupportedCopyMessage(result: Extract<CopySaveToSlotStatus, { status: "unsupported" }>): string {
  if (result.reason === "savefiles_in_content_dir") {
    return "Save sync is off for this game: its saves are written beside the game file.";
  }
  if (result.reason === "save_shape_unsupported" && result.message) {
    return result.message;
  }
  return "Copying isn't available for multi-file saves yet.";
}

export type CopyToSlotFeedback = { kind: "toast"; message: string } | { kind: "conflict"; conflict: SyncConflict };

/** Map a CopySaveToSlotStatus to user-facing toast feedback or conflict modal requirement. */
export function copyToSlotFeedback(result: CopySaveToSlotStatus, targetSlot: string): CopyToSlotFeedback {
  switch (result.status) {
    case "ok":
      return { kind: "toast", message: `Save copied to slot '${displaySlot(targetSlot)}'` };
    case "already_present":
      return { kind: "toast", message: `Already in slot '${displaySlot(targetSlot)}' as #${result.existing_id}` };
    case "conflict_blocked": {
      const first = result.conflicts[0];
      if (first) {
        return { kind: "conflict", conflict: first };
      }
      return { kind: "toast", message: "Copy blocked by a sync conflict. Sync this save, then try again." };
    }
    case "target_slot_busy":
      return {
        kind: "toast",
        message: `Slot '${displaySlot(targetSlot)}' has newer changes on another device — sync it first, then copy again.`,
      };
    case "preflight_failed":
      return {
        kind: "toast",
        message: `Sync failed before copy: ${result.errors[0] ?? "preflight error"}`,
      };
    case "server_unreachable":
      return { kind: "toast", message: "Couldn't reach RomM. Check your connection and try again." };
    case "not_found":
      return { kind: "toast", message: "RomM couldn't find this game's save data — nothing was copied." };
    case "version_deleted":
      return { kind: "toast", message: "This save no longer exists on the server." };
    case "rom_not_installed":
      return { kind: "toast", message: "ROM is no longer installed locally. Reinstall and try again." };
    case "unsupported":
      return { kind: "toast", message: unsupportedCopyMessage(result) };
    case "not_configured":
      return { kind: "toast", message: "Set up save slots for this game first, then copy." };
    case "copy_failed":
      return { kind: "toast", message: `Couldn't copy the save: ${result.message}` };
    case "invalid_slot_name":
      return { kind: "toast", message: "Enter a valid slot name." };
  }
}

export type RollbackFeedback = { kind: "toast"; message: string } | { kind: "conflict"; conflict: SyncConflict };

/** Map a RollbackStatus to user-facing toast feedback or conflict modal requirement. */
export function rollbackFeedback(result: RollbackStatus, versionUpdatedAt: string | null): RollbackFeedback {
  switch (result.status) {
    case "ok":
      return { kind: "toast", message: `Save restored from ${formatSaveTimeAgo(versionUpdatedAt)}` };
    case "conflict_blocked": {
      const first = result.conflicts[0];
      if (first) {
        return { kind: "conflict", conflict: first };
      }
      return { kind: "toast", message: "Restore blocked by a sync conflict. Sync this save, then try again." };
    }
    case "preflight_failed": {
      const detail = result.errors[0] ?? "preflight error";
      return { kind: "toast", message: `Sync failed before restore: ${detail}` };
    }
    case "put_failed":
      return {
        kind: "toast",
        message:
          "Restored locally, but the server didn't update. Other devices will see the previous version until you retry.",
      };
    case "rom_not_installed":
      return { kind: "toast", message: "ROM is no longer installed locally. Reinstall and try again." };
    case "version_deleted":
      return { kind: "toast", message: "This version no longer exists on the server" };
    case "server_unreachable":
      return { kind: "toast", message: "Couldn't reach RomM. Check your connection and try again." };
    case "not_found":
      return { kind: "toast", message: "RomM couldn't find this game's save data — nothing was restored." };
    case "unsupported":
      return { kind: "toast", message: unsupportedRestoreMessage(result) };
  }
}

/**
 * Error message for a switchSlot failure: a known reason's own wording, the
 * backend's explanation for `save_shape_unsupported`, and `fallback` otherwise.
 */
export function switchSlotFailureMessage(
  reason: string | undefined,
  message?: string,
  fallback = "Failed to switch slot",
): string {
  if (reason === "pending_uploads") {
    return "Sync your saves first — local changes haven't been uploaded";
  }
  if (reason === "server_unreachable") {
    return "Can't switch — RomM server is not reachable";
  }
  if (reason === "not_installed") {
    return "Can't switch — download the game first";
  }
  if (reason === "savefiles_in_content_dir") {
    return "Can't switch — this game's saves are written beside the game file";
  }
  if (reason === "save_shape_unsupported" && message) {
    return message;
  }
  return fallback;
}

/** Build confirmation text lines for deleting a slot. */
export function formatSlotDeleteLines(info: SlotDeleteInfo): string[] {
  const lines: string[] = [];
  if (info.source === "server" && info.server_save_count > 0) {
    const n = info.server_save_count;
    lines.push(
      `This will permanently delete ${n} save${n === 1 ? "" : "s"} from slot '${info.slot}' on the RomM server.`,
    );
  } else {
    lines.push(`This will remove slot '${info.slot}' from your local configuration.`);
  }
  if (info.local_file_count > 0) {
    const n = info.local_file_count;
    lines.push(`${n} tracked file${n === 1 ? "" : "s"} will be unlinked.`);
  }
  return lines;
}

/** Build the multi-line confirmation description for deleting a slot. */
export function formatSlotDeleteDescription(info: SlotDeleteInfo): string {
  return [...formatSlotDeleteLines(info), "This cannot be undone."].join("\n\n");
}
