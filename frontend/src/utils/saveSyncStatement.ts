/**
 * What a game's SAVES tab says of its saves, worded from one answer to whether they sync
 * (`get_save_sync_availability`, #2149). Every sentence here is the panel's own; the one
 * that says why a game's saves do not sync is the backend's `message`, passed through.
 */

import type { SaveStatus, SaveSyncAvailability } from "../types";
import { SAVEFILES_IN_CONTENT_DIR_REASON } from "./launchGate";

/** The empty active slot's line where the answer cannot say that the game's saves sync. */
export const NO_SAVE_YET = "No save yet.";

/** An empty slot's line where this device was read and RomM could not be asked (D10). */
export const NO_SAVE_ON_DEVICE = "No save on this device.";

/** RetroArch's setting, the usual cause of saves written beside the game file, and the way back (D2). */
export const CONTENT_DIR_HINT =
  "The usual cause is RetroArch's 'Write Saves to Content Directory' (RetroArch › Settings › Saving); if it is on, turning it off lets save sync work again.";

/** Whether *answer* refuses the saves of a game that is downloaded: the tab then says why, and is marked (D1, D5). */
export function refusesDownloadedGame(answer: SaveSyncAvailability | null): boolean {
  return !!answer && !answer.available && answer.installed;
}

/** The hint that stands under a refusal's sentence, or `null` for a reason that has none. */
export function refusalHint(reason: string | undefined): string | null {
  return reason === SAVEFILES_IN_CONTENT_DIR_REASON ? CONTENT_DIR_HINT : null;
}

/** The line an active slot without a save reads (D3): the emulator named where the answer says the saves sync. */
export function noSaveLine(answer: SaveSyncAvailability | null): string {
  if (!answer?.available || !answer.installed) return NO_SAVE_YET;
  if (answer.emulator_label === null) {
    return "No save yet — this game's saves sync with RomM once its emulator writes one.";
  }
  return `No save yet — ${answer.emulator_label}'s saves for this game sync with RomM once it writes one.`;
}

/** The line a slot showing no save file reads (D10): *noSaveLine* only where this device and RomM were both read, the
 *  device's own line where RomM could not be asked, and none while no status is held. */
export function emptySlotLine(saveStatus: SaveStatus | null, noSaveLine: string): string | null {
  if (!saveStatus) return null;
  return saveStatus.server_query_failed ? NO_SAVE_ON_DEVICE : noSaveLine;
}

/** What a game that is not downloaded reads above its slots (D4): a line, and the refusal's sentence where its saves
 *  would not sync; `null` for a game that is downloaded or no answer. */
export function notDownloadedStatement(
  answer: SaveSyncAvailability | null,
): { line: string; refusal: string | null } | null {
  if (!answer || answer.installed) return null;
  if (!answer.available && !answer.established) {
    return { line: "Not downloaded. Whether its saves will sync could not be established.", refusal: null };
  }
  if (!answer.available) {
    return { line: "Not downloaded. Once it is, its saves will not sync:", refusal: answer.message ?? "" };
  }
  if (answer.emulator_label === null) {
    return { line: "Not downloaded. Once it is, its saves sync with RomM.", refusal: null };
  }
  return {
    line: `Not downloaded. Once it is, ${answer.emulator_label}'s saves for this game sync with RomM.`,
    refusal: null,
  };
}
