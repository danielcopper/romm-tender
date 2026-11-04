/**
 * The words for an emulator a game or a platform cannot start with, and for a
 * stored pick whose label no longer exists (#2195 D5, D7, D23), and for an entry
 * the resolver gives no complete start command for (#2221 D2, D20). One home
 * for each sentence: the game page, the emulator menu, the platform page and a
 * refused start — the Play button's and Steam's own — say the same words.
 */

import { getPlatformCoreInfo, logError } from "../api/backend";
import type {
  CoreInfo,
  EmulatorOption,
  EmulatorStart,
  GameLaunch,
  SourceDefault,
  StartCommandReason,
  StartCommandVerdict,
  StartRemark,
  VanishedPick,
} from "../types";
import { isNotEstablished, notEstablishedCause } from "./emulatorInstallation";
import { launchRefusal } from "./gameLaunch";
import { cannotStartSentence, sourceName } from "./emulatorSourceWording";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { withTimeout } from "./withTimeout";

const CHOOSE_ANOTHER = " Choose another emulator in the emulator menu.";

/**
 * Whether the emulator menu offers *entry* as a pick: every entry but one the
 * resolver states not installed (#2195 D9, D23), whether Tender can start a game with it or not.
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
 * Why the resolver gives no complete command for an entry, as the words after "Tender cannot start <entry> yet: ".
 * A reason this map does not hold reads as the command not being read.
 */
const COMMAND_REFUSED_BECAUSE: Record<Exclude<StartCommandReason, "entry_unknown">, string> = {
  command_unread: "how ES-DE starts it could not be read",
  desktop_file: "it starts through a .desktop file",
  injected_file_unread: "a file its command reads in could not be read",
  no_program: "its command names no program",
  rom_root_unknown: "where its games are kept could not be established",
  command_too_long: "its command is longer than can be read",
  frontend_build_unread: "which ES-DE build the source ships could not be read",
  environment_unread: "what it needs from RetroDECK's start could not be read",
  environment_unknown: "what it needs from RetroDECK's start is not known yet",
  folder_unplaced: "the folder it starts in could not be placed",
};

/** The words for a launch-command refusal: its own, else that the command could not be read. */
function commandRefusedBecause(reason: string): string {
  return reason in COMMAND_REFUSED_BECAUSE
    ? COMMAND_REFUSED_BECAUSE[reason as keyof typeof COMMAND_REFUSED_BECAUSE]
    : COMMAND_REFUSED_BECAUSE.command_unread;
}

/** Whether `reason` is one of the resolver's launch-command refusals rather than the entry's installation. */
function isCommandReason(reason: string | null): boolean {
  return reason !== null && (reason in COMMAND_REFUSED_BECAUSE || reason === "entry_unknown");
}

/** The reason a start is refused where the answering source is one Tender starts no game through yet (#2221 D21). */
const SOURCE_STARTS_NO_GAMES = "source_starts_no_games";

/** Whether `reason` refuses the start for the entry's command or its installation — what is said beside the file's
 *  own refusal rather than instead of it (#2221 D20, #2698 D8). */
function isEntryStartReason(reason: string | null): boolean {
  return isCommandReason(reason) || isNotEstablished(reason);
}

/** Why the entry's installation could not be established, as the sentence a page says under that emulator. */
function installationRefusalSentence(reason: string, entry: string): string {
  const cause = notEstablishedCause(reason);
  return `Whether ${entry} is installed could not be established${cause === null ? "" : `: ${cause}`}.`;
}

/**
 * What the emulator menu says under an entry about the resolver's command for the game, or `null`, and whether
 * Tender can start the game with it: a refusal for the answering source names that source (#2221 D21), one for the
 * entry's installation says nothing beside the row's own note, any other one says why (#2221 D20).
 */
export function startVerdictLine(verdict: StartCommandVerdict | null): { tenderStarts: boolean; line: string | null } {
  if (verdict === null || verdict.starts || verdict.reason === null || verdict.reason === "not_installed") {
    return { tenderStarts: true, line: null };
  }
  if (verdict.reason === SOURCE_STARTS_NO_GAMES)
    return { tenderStarts: false, line: cannotStartSentence(verdict.source ?? "") };
  if (isNotEstablished(verdict.reason)) return { tenderStarts: false, line: null };
  return { tenderStarts: false, line: commandRefusalLine(verdict.reason) };
}

/** Why the resolver gives no complete command for `entry`, as one sentence (#2221 D2). */
export function commandRefusalSentence(reason: string, entry: string): string {
  if (reason === "entry_unknown") return `Your choice '${entry}' no longer exists in its emulator source.`;
  return `Tender cannot start ${entry} yet: ${commandRefusedBecause(reason)}.`;
}

/** The emulator menu's short line for an entry the resolver gives no complete command for (#2221 D20). */
export function commandRefusalLine(reason: string): string {
  if (reason === "entry_unknown") return "no longer exists in its emulator source";
  return `Tender cannot start it yet: ${commandRefusedBecause(reason)}`;
}

/** What is said beside a start the resolver's command lets go ahead, one sentence each (#2708 D1). */
export function startRemarkSentences(remarks: readonly StartRemark[] | undefined): string[] {
  return (remarks ?? []).map((remark) =>
    remark.placeholder === null
      ? "ES-DE leaves a placeholder unfilled in this command."
      : `ES-DE leaves ${remark.placeholder} unfilled in this command.`,
  );
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
      case SOURCE_STARTS_NO_GAMES:
        return cannotStartSentence(start.source ?? "");
      default:
        if (start.reason !== null && isNotEstablished(start.reason)) {
          return `${installationRefusalSentence(start.reason, entry)}${pointer}`;
        }
        if (start.reason !== null && isCommandReason(start.reason)) {
          return `${commandRefusalSentence(start.reason, entry)}${pointer}`;
        }
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

/** The sentence for a game no emulator starts: no entry is in effect, or its system lists none (#2221 D20). */
export const NO_EMULATOR_STARTS = "No emulator starts this game.";

/**
 * Why a game cannot start, or `null`: its emulator's refusal first, then its
 * file's (#2195 D2) — except where the resolver gives no complete command for
 * the entry: then both are said, the file's first (#2221 D20, #2698 D8). A game
 * no emulator starts says so. The one composition the game page and a refused
 * start both say.
 */
export function startRefusal(start: EmulatorStart, launch: GameLaunch | null, canPick: boolean): string | null {
  if (start.state === "no_entry") return NO_EMULATOR_STARTS;
  const emulator = emulatorStartRefusal(start, "this game", canPick);
  const file = launchRefusal(launch, canPick);
  if (isEntryStartReason(start.reason) && file !== null && emulator !== null) return `${file} ${emulator}`;
  return emulator ?? file;
}

/**
 * Why a game cannot start, from its core read ({@link startRefusal}), or `null`.
 * `canPick` says the page offers an emulator menu for the sentence to point at; absent, it is read off the entries.
 */
export function gameStartRefusal(
  info: CoreInfo,
  canPick = gameCanPickAnother(info.emulators, info.active_core_label),
): string | null {
  return startRefusal(info.start, info.launch ?? null, canPick);
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
