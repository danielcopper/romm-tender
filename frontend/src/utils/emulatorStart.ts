/**
 * The words for an emulator a game or a platform cannot start with, and for a
 * stored pick whose label no longer exists (#2195 D5, D7, D21, D23). One home
 * for each sentence: the game page, the platform page and a refused start —
 * the Play button's and Steam's own — say the same words.
 */

import { getPlatformCoreInfo, logError } from "../api/backend";
import type { EmulatorDataReason, EmulatorOption, EmulatorStart, SourceDefault, VanishedPick } from "../types";
import { isNotEstablished, notEstablishedCause } from "./emulatorInstallation";
import { emulatorDataReasonSentence, sourceName } from "./emulatorSourceWording";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { withTimeout } from "./withTimeout";

const CHOOSE_ANOTHER = "Choose another emulator in the emulator menu.";

/**
 * Why a start with this answer is refused, as one sentence about `subject`
 * ("this game", "these games"), or `null` where nothing stands in its way. An
 * entry Tender cannot bake starts through RetroDECK's own start, which says
 * nothing here.
 */
export function emulatorStartRefusal(start: EmulatorStart, subject: string): string | null {
  const entry = start.entry ?? "The emulator";
  if (start.state === "cannot_start") {
    switch (start.reason) {
      case "not_installed":
        return `${entry} is not installed, so ${subject} cannot start. ${CHOOSE_ANOTHER}`;
      case "start_script_runs_nothing":
        return `RetroDECK's start script finds no emulator for ${entry}, so ${subject} cannot start. ${CHOOSE_ANOTHER}`;
      case "folder_install":
        return `${entry} cannot start a game installed as a folder. ${start.folder_entry ?? "Another entry"} can — choose it in the emulator menu.`;
      default:
        return `${entry} cannot start ${subject}. ${CHOOSE_ANOTHER}`;
    }
  }
  if (start.state === "choose") {
    const cause = notEstablishedCause(start.reason);
    const why = cause === null ? "" : ` (${cause})`;
    return `No emulator is the default, because whether ${entry} is installed could not be established${why}. Choose an emulator in the emulator menu.`;
  }
  return null;
}

/** Whether this answer refuses a start. */
export function startRefused(start: EmulatorStart): boolean {
  return start.state === "cannot_start" || start.state === "choose";
}

/** The sentence for a stored pick whose label no longer exists in its source. */
export function vanishedPickSentence(vanished: VanishedPick): string {
  const source = vanished.source === null ? "its emulator source" : sourceName(vanished.source);
  const instead =
    vanished.used === null ? "there is no default to use instead" : `the default '${vanished.used}' is used`;
  return `Your choice '${vanished.label}' no longer exists in ${source}; ${instead}.`;
}

/**
 * One source's own default for a platform, as the platform page states it: the
 * entry it ships first, the one chosen in it inside ES-DE where that differs,
 * or why it has none.
 */
export function sourceDefaultSentence(entry: SourceDefault): string {
  const name = sourceName(entry.kind);
  if (entry.default !== null) {
    const chosen = entry.chosen_in_frontend === null ? "" : ` (${entry.chosen_in_frontend} chosen in ES-DE)`;
    return `${name}'s default: ${entry.default}${chosen}.`;
  }
  if (entry.reason === "no_entry") return `${name} lists no emulator for this platform.`;
  if (isNotEstablished(entry.reason)) {
    const cause = notEstablishedCause(entry.reason);
    return `${name}'s default could not be established${cause === null ? "" : `: ${cause}`}.`;
  }
  // Any other reason is the refusal of the source's list, in the words the picker uses for it.
  const source = { kind: entry.kind, starts_games: false };
  return `${name}: ${emulatorDataReasonSentence(entry.reason as EmulatorDataReason | null, source)}`;
}

/**
 * The game page's line where the user chose an emulator for the game inside
 * ES-DE that is not the one Tender starts it with, or `null`. What is set in
 * ES-DE is ES-DE's; Tender's own choice is made in its emulator menu (#2195 D7).
 */
export function frontendChoiceSentence(
  emulators: readonly EmulatorOption[],
  activeLabel: string | null,
): string | null {
  const chosen = emulators.find((entry) => entry.chosen_in_frontend);
  if (chosen === undefined || chosen.label === activeLabel) return null;
  return `${chosen.label} is chosen for this game in ES-DE; Tender starts it with its own choice, set in the emulator menu.`;
}

/**
 * Why the ROM `romId` cannot start with its emulator, read fresh for a start —
 * the Play button's and Steam's own Play on the shortcut alike — or `null`.
 * A read that fails or gets no answer in time refuses nothing: a check of this
 * kind never traps a game.
 */
export async function emulatorRefusalForRom(romId: number, context: string): Promise<string | null> {
  try {
    const info = await withTimeout(getPlatformCoreInfo(romId), LOCAL_CALL_LIMIT_MS);
    return emulatorStartRefusal(info.start, "this game");
  } catch (e) {
    logError(`${context}: emulator check failed (allowing launch): ${e}`);
    return null;
  }
}
