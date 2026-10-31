/**
 * What a version picker's row says beside the version's name: its badges, and
 * the hint beside them.
 */

import type { VersionInfo } from "../api/backend";
import { NO_FILE_ON_ROMM_LABEL } from "./noFileOnRommWording";

export const DEFAULT_BADGE = "Default";
export const DOWNLOADED_BADGE = "Downloaded";
export const NOT_SYNCED_BADGE = "not synced";

/** The label a row (or a singleton binding) carries once RomM 404s its exact id. */
export const VANISHED_HINT = "No longer available on RomM";

/** The label of a row another shortcut of this game is on. */
const BOUND_ELSEWHERE_HINT = "On another shortcut of this game";

const CONFLICTING_MATCH_HINT = "conflicting metadata match in RomM";

/** The label of a row the user hid in RomM. */
const HIDDEN_HINT = "Hidden in RomM";

/** The hint *v*'s row carries, or `null` where it carries none. */
export function versionRowHint(v: VersionInfo): string | null {
  if (v.vanished) return VANISHED_HINT;
  // Ahead of the no-file label: an installed version without a file is
  // otherwise selectable, so that label alone would read as an offer.
  if (v.bound_elsewhere) return BOUND_ELSEWHERE_HINT;
  if (v.no_file) return NO_FILE_ON_ROMM_LABEL;
  if (!v.switchable) return CONFLICTING_MATCH_HINT;
  // Last: it explains no refusal, so any hint that does goes first.
  if (v.hidden) return HIDDEN_HINT;
  return null;
}
