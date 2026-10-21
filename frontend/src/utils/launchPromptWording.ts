/**
 * The words of five prompts: three a game start can raise — the emulator core
 * changed, RomM unreachable over unsynced changes, save sync unavailable — the
 * one a version switch raises over unsynced saves, and the one Stop raises over
 * a running game. Every title, sentence and button label Tender writes for them.
 * One home, so every surface that draws one of these prompts asks the same
 * question the same way; the drawing itself stays with each surface.
 */

// ── Every prompt ──

/** The exit every one of the prompts offers; it starts nothing and switches nothing. */
export const CANCEL_LABEL = "Cancel";

// ── The core change: the emulator core differs from the one the saves came from ──

export const CORE_CHANGE_TITLE = "Emulator Core Changed";

export const CORE_CHANGE_WARNING_HEADING = "Save Compatibility Warning";

export const CORE_CHANGE_WARNING =
  "Some emulator cores use incompatible save formats. Continuing may overwrite your existing saves with data the " +
  "previous core can't read.";

export const CORE_CHANGE_CONTINUE_LABEL = "Continue";

// ── Offline drift: RomM unreachable and the local save has unsynced changes ──

export const OFFLINE_DRIFT_TITLE = "RomM Unreachable";

export const OFFLINE_DRIFT_DESCRIPTION =
  "Your local save has unsynced changes. Playing now may create a conflict you'll resolve later. Start anyway?";

export const OFFLINE_DRIFT_START_LABEL = "Start Anyway";

export const OFFLINE_DRIFT_RETRY_LABEL = "Retry connection";

// ── Fallback launch: the pre-launch sync failed without a conflict, or a step of the check got no answer ──

export const FALLBACK_LAUNCH_TITLE = "Save Sync Unavailable";

export const FALLBACK_LAUNCH_LABEL = "Launch Anyway";

/** The message the verdict carries when it has one, else a sentence of ours. */
export function fallbackLaunchDescription(message?: string): string {
  const reason = message?.trim();
  if (!reason) return "Couldn't sync saves with RomM server. Launch with local saves?";
  if (/[.!?]$/.test(reason)) return `${reason} Launch with local saves?`;
  return `${reason} — launch with local saves?`;
}

// ── Unsynced saves: switching away from a version whose saves haven't been synced to RomM ──

export const UNSYNCED_SAVES_TITLE = "Unsynced saves";

export interface UnsyncedSavesDescriptionArgs {
  versionName: string;
  /** `unchecked`: the check could not read, so the sentence says Tender couldn't check. */
  stranding: "unsynced" | "unchecked";
  /** Why the prompt offers no sync first, or `null` when it offers one. */
  noSync: "unreachable" | "version_gone" | null;
}

/**
 * A save changed since its last sync and one never synced are one case to the
 * user, since what they can do about either is the same. Where the prompt
 * offers no sync, the sentence says why.
 */
export function unsyncedSavesDescription({ versionName, stranding, noSync }: UnsyncedSavesDescriptionArgs): string {
  const what =
    stranding === "unchecked"
      ? `Tender couldn't check whether "${versionName}" has saves that haven't been synced to RomM.`
      : `"${versionName}" has saves that haven't been synced to RomM.`;
  if (noSync === "version_gone") {
    return `${what} This version is no longer available on RomM, so its saves can't be uploaded. They stay on this device.`;
  }
  const why = noSync === "unreachable" ? " RomM is not reachable right now, so they can't be synced first." : "";
  return `${what}${why} They stay on disk, but won't sync until you switch back.`;
}

/** Offered only while RomM is reachable and still has the version being left. */
export const SYNC_AND_SWITCH_LABEL = "Sync now & switch";

export const SWITCH_ANYWAY_LABEL = "Switch anyway";

// ── Stop game: ending a running emulator ──

export const STOP_GAME_TITLE = "Stop Game?";

/**
 * Promises nothing about the save: the backend asks the emulator to exit and
 * forces it if it refuses, so whether an in-flight write completes is the
 * emulator's business, not ours.
 */
export const STOP_GAME_DESCRIPTION = "Any progress since the last in-game save may be lost.";

export const STOP_GAME_LABEL = "Stop Game";
