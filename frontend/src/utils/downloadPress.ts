/**
 * When a game page's Download takes no press, and what a ROM's cached detail
 * answers for it, for every Play button that offers Download. Each button words
 * and draws these answers itself.
 */

import type { CachedGameDetail } from "../api/backend";

/** What a ROM's cached detail says about downloading it. */
export interface DownloadAnswers {
  /** Something already sits where the download would write. */
  targetOccupied: boolean;
  /** The platform folder holds what could be this game under another name. */
  candidatePresent: boolean;
  /** RomM holds no file for the ROM, so there is nothing to download. */
  noFileOnRomm: boolean;
}

export const NO_DOWNLOAD_ANSWERS: DownloadAnswers = {
  targetOccupied: false,
  candidatePresent: false,
  noFileOnRomm: false,
};

/** The cached detail's answers; a field it leaves out reads as nothing there and a file on RomM. */
export function downloadAnswers(
  cached: Pick<CachedGameDetail, "target_path_occupied" | "adoption_candidate_present" | "has_file_on_disk">,
): DownloadAnswers {
  return {
    targetOccupied: cached.target_path_occupied === true,
    candidatePresent: cached.adoption_candidate_present === true,
    noFileOnRomm: cached.has_file_on_disk === false,
  };
}

interface DownloadPressState {
  offline: boolean;
  /** A press's own request is in flight, or the download it started still runs. */
  pending: boolean;
  /** The version the shortcut is bound to is gone from RomM. */
  boundVanished: boolean;
  noFileOnRomm: boolean;
}

/** Whether Download takes no press. */
export function downloadTakesNoPress({ offline, pending, boundVanished, noFileOnRomm }: DownloadPressState): boolean {
  return offline || pending || boundVanished || noFileOnRomm;
}
