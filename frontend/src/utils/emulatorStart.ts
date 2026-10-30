/**
 * The words for an emulator a game or a platform cannot start with, and for a
 * stored pick whose label no longer exists (#2195 D5, D7, D23). One home
 * for each sentence: the game page, the platform page and a refused start —
 * the Play button's and Steam's own — say the same words.
 */

import { getPlatformCoreInfo, logError } from "../api/backend";
import type { CoreInfo, EmulatorOption, EmulatorStart, SourceDefault, VanishedPick } from "../types";
import { notEstablishedCause } from "./emulatorInstallation";
import { launchRefusal } from "./gameLaunch";
import { sourceName } from "./emulatorSourceWording";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { withTimeout } from "./withTimeout";

const CHOOSE_ANOTHER = " Choose another emulator in the emulator menu.";

/**
 * Whether the emulator menu offers *entry* as a pick: every entry but one the
 * resolver states not installed (#2195 D9, D23), whether Tender can bake it or not.
 */
export function isChoosable(entry: EmulatorOption): boolean {
  return entry.reason !== "not_installed";
}

/**
 * Whether the emulator menu offers an entry to pick other than the one in
 * effect — what a sentence may point the reader at.
 */
export function canPickAnother(emulators: readonly EmulatorOption[], activeLabel: string | null): boolean {
  return emulators.some((entry) => isChoosable(entry) && entry.label !== activeLabel);
}

/** {@link canPickAnother} for a game's page, whose emulator menu is offered only with more than one emulator. */
export function gameCanPickAnother(emulators: readonly EmulatorOption[], activeLabel: string | null): boolean {
  return emulators.length > 1 && canPickAnother(emulators, activeLabel);
}

/**
 * Why a start with this answer is refused, as one sentence about `subject`
 * ("this game", "these games"), or `null` where nothing stands in its way.
 * `canPick` says the emulator menu offers another entry; only then does the
 * sentence point at it.
 */
export function emulatorStartRefusal(start: EmulatorStart, subject: string, canPick: boolean): string | null {
  const entry = start.entry ?? "The emulator";
  const pointer = canPick ? CHOOSE_ANOTHER : "";
  if (start.state === "cannot_start") {
    switch (start.reason) {
      case "not_installed":
        return `${entry} is not installed, so ${subject} cannot start.${pointer}`;
      case "start_script_runs_nothing":
        return `RetroDECK's start script cannot start ${entry}, so ${subject} cannot start.${pointer}`;
      case "not_bakeable":
        return `Tender cannot start ${entry} yet.${pointer}`;
      default:
        return `${entry} cannot start ${subject}.${pointer}`;
    }
  }
  if (start.state === "choose") {
    const cause = notEstablishedCause(start.reason);
    const why = cause === null ? "" : ` (${cause})`;
    const choose = canPick ? " Choose an emulator in the emulator menu." : "";
    return `No emulator is the default, because whether ${entry} is installed could not be established${why}.${choose}`;
  }
  return null;
}

/** Whether this answer refuses a start. */
export function startRefused(start: EmulatorStart): boolean {
  return start.state === "cannot_start" || start.state === "choose";
}

/** The sentence for a stored pick whose label no longer exists in its source, naming what is used instead. */
export function vanishedPickSentence(vanished: VanishedPick): string {
  const source = vanished.source === null ? "its emulator source" : sourceName(vanished.source);
  let instead: string;
  if (vanished.used === null) instead = "there is no default to use instead";
  else if (vanished.used_layer === "platform") instead = `your platform choice '${vanished.used}' is used`;
  else instead = `the default '${vanished.used}' is used`;
  return `Your choice '${vanished.label}' no longer exists in ${source}; ${instead}.`;
}

/**
 * The platform page's lines for the emulators the user chose inside ES-DE, one
 * per source in *sources* that has such a choice, the source named where more
 * than one is listed (#2195 D25).
 */
export function frontendChoiceLines(sources: readonly SourceDefault[]): string[] {
  const named = sources.length > 1;
  return sources.flatMap((entry) => {
    if (entry.chosen_in_frontend === null) return [];
    const line = `${entry.chosen_in_frontend} chosen in ES-DE`;
    return [named ? `${sourceName(entry.kind)}: ${line}` : line];
  });
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
  return `${chosen.label} is chosen in ES-DE; Tender starts this game with the emulator chosen in its emulator menu.`;
}

/** Why a game cannot start, from its core read: its emulator's refusal first, then its file's (#2195 D2), or `null`. */
export function gameStartRefusal(info: CoreInfo): string | null {
  const canPick = gameCanPickAnother(info.emulators, info.active_core_label);
  return emulatorStartRefusal(info.start, "this game", canPick) ?? launchRefusal(info.launch ?? null, canPick);
}

/**
 * Why the ROM `romId` cannot start — with its emulator, or its file with that
 * emulator (#2195 D2) — read fresh for Steam's own Play on the shortcut, or
 * `null`. A read that fails or gets no answer in time refuses nothing: a check
 * of this kind never traps a game.
 */
export async function emulatorRefusalForRom(romId: number, context: string): Promise<string | null> {
  try {
    return gameStartRefusal(await withTimeout(getPlatformCoreInfo(romId), LOCAL_CALL_LIMIT_MS));
  } catch (e) {
    logError(`${context}: emulator check failed (allowing launch): ${e}`);
    return null;
  }
}
