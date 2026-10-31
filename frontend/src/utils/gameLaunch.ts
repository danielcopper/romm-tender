/**
 * The words for whether an installed game's file starts, one home for each
 * state's sentence (#2195 D2). The game page, a refused start — the Play
 * button's and Steam's own — and the emulator menu say them from here.
 *
 * Three states refuse a start: the game has to be installed in an emulator
 * first, the file is not one the system starts from, or the entry does not
 * start it. The other two let it go ahead: it starts, which says nothing unless
 * the file is an archive whose contents were not read, or whether it starts
 * could not be established, which says so. This is the one home of that rule.
 *
 * Beside them are the notifications for a page whose check of whether the game
 * can start went wrong, and for a start pressed after it that then fails.
 */

import { HostTransportError } from "../api/host";
import type { AnsweringSource, GameLaunch } from "../types";
import { sourceName } from "./emulatorSourceWording";
import { TimeoutError } from "./withTimeout";

const REFUSING: ReadonlySet<GameLaunch["state"]> = new Set([
  "needs_installing",
  "not_a_launch_file",
  "not_this_emulator",
]);

/**
 * Why the verdict could not be established, worded; the reason is Tender's word
 * for the resolver's. `source` is the source that answered, named as the user
 * knows it (#2195 D36). A launch answer with no source behind it is `no_source`:
 * none detected, or every one detected switched off.
 */
function notEstablishedBecause(launch: GameLaunch, source: AnsweringSource | null): string {
  const named = source === null ? "the emulator source" : sourceName(source.kind);
  switch (launch.reason) {
    case "system_unknown":
      return `${named} does not list the system ${launch.platform_name}`;
    case "entry_unknown":
      return `${named} has no emulator named ${launch.entry}`;
    case "catalogue_unread":
      return `${named}'s list of emulators could not be read`;
    case "no_source":
      return "no emulator source is installed and switched on";
    default:
      return `${named} gave no answer`;
  }
}

/**
 * The extensions a system starts from, as a sentence lists them: extensions
 * that differ only in case once, in the spelling the resolver names first
 * (#2195 D34). Display only — whether a file starts is the resolver's
 * case-exact answer.
 */
function listedExtensions(extensions: readonly string[]): string {
  const seen = new Set<string>();
  const shown: string[] = [];
  for (const extension of extensions) {
    const key = extension.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    shown.push(extension);
  }
  return shown.join(", ");
}

/** Whether this verdict refuses a start. */
export function launchRefused(launch: GameLaunch | null): boolean {
  return launch !== null && REFUSING.has(launch.state);
}

/**
 * Why the game cannot start with its entry, as one sentence, or `null` where
 * the verdict lets it start. `canPick` says the emulator menu offers another
 * entry; only then does the sentence point at it.
 */
export function launchRefusal(launch: GameLaunch | null, canPick: boolean): string | null {
  if (launch === null) return null;
  switch (launch.state) {
    case "needs_installing": {
      const names = launch.install_in ?? [];
      return names.length === 0
        ? "This game has to be installed first."
        : `This game has to be installed in ${names.join(" or ")} first.`;
    }
    case "not_a_launch_file":
      return `${launch.file} is not the file ${launch.platform_name} starts from; it starts from ${listedExtensions(launch.starts_from)}.`;
    case "not_this_emulator": {
      if (launch.other_entries.length === 0) return `${launch.entry} does not start ${launch.extension}.`;
      const pointer = canPick ? " Choose another emulator in the emulator menu." : "";
      return `${launch.entry} does not start ${launch.extension}; other emulators of this system do.${pointer}`;
    }
    default:
      return null;
  }
}

/**
 * What the page says about a verdict that lets the game start, or `null` where
 * there is nothing to say: whether it starts could not be established, or the
 * file is an archive whose contents were not read. `source` is the source the
 * answer came from.
 */
export function launchNote(launch: GameLaunch | null, source: AnsweringSource | null): string | null {
  if (launch === null) return null;
  if (launch.state === "not_established") {
    return `Whether ${launch.file} starts cannot be established: ${notEstablishedBecause(launch, source)}.`;
  }
  if (launch.state === "starts" && launch.contents_unread) {
    return `What is inside ${launch.file} was not read, so whether ${launch.entry} finds a game in it cannot be established.`;
  }
  return null;
}

/**
 * The short line the emulator menu shows under an entry for its verdict on the
 * game (#2195 D22, D29, D36). `source` is the source the answer came from.
 */
export function entryLaunchLine(launch: GameLaunch, source: AnsweringSource | null): string {
  switch (launch.state) {
    case "needs_installing":
      return "cannot start it: the game has to be installed first";
    case "not_a_launch_file":
      return `${launch.platform_name} does not start from ${launch.extension}`;
    case "not_this_emulator":
      return `cannot start ${launch.extension} files`;
    case "not_established":
      return `whether it starts cannot be established: ${notEstablishedBecause(launch, source)}`;
    default:
      if (launch.format_unestablished) return `whether it reads ${launch.extension} is not established`;
      if (launch.contents_unread) return `what is inside ${launch.file} was not read`;
      return "can start it";
  }
}

/**
 * What went wrong where the page's check of whether the game can start failed
 * itself, as the notification says it (#2195 D32, D36). A timeout's and a lost
 * connection's own detail is for the log, not the sentence.
 */
export function startCheckFailureSentence(e: unknown): string {
  const lead = "Tender could not check whether this game can start";
  if (e instanceof TimeoutError) return `${lead}: Tender's backend did not answer in time.`;
  if (e instanceof HostTransportError) return `${lead}: the connection to Tender's backend was lost.`;
  return `${lead}: ${e instanceof Error ? e.message : String(e)}.`;
}

/**
 * What the notification says where a start, pressed after the page's check
 * went wrong, then fails itself (#2195 D33).
 */
export function startFailedSentence(e: unknown): string {
  return `Tender could not start this game: ${e instanceof Error ? e.message : String(e)}`;
}
