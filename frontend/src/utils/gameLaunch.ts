/**
 * The words for whether an installed game's file starts, one home for each
 * state's sentence (#2195 D2). The game page, a refused start — the Play
 * button's and Steam's own — and the emulator menu say them from here.
 *
 * Three states refuse a start: the game has to be installed in an emulator
 * first, the file is not one the system starts from, or the entry does not
 * start it, with the resolver's reason where it states one. The other two let
 * it go ahead: it starts, which says nothing unless the resolver's reading of
 * the file stopped short of something or the file is an archive whose contents
 * were not read, or whether it starts could not be established, which says so
 * (#2195 D40). This is the one home of that rule.
 */

import { HostTransportError } from "../api/host";
import type { AnsweringSource, GameLaunch, LaunchRefusalReason, LaunchStoppedShortReason } from "../types";
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
 * Why the entry does not start the file, for a refusal the resolver states a
 * reason of its own for (#2195 D40): the page's sentence and the emulator
 * menu's line. A menu line says only what stands in the way (D36).
 */
const REFUSED_BECAUSE: Record<
  LaunchRefusalReason,
  { page: (launch: GameLaunch) => string; line: (launch: GameLaunch) => string }
> = {
  takes_no_folder: {
    page: (launch) => `${launch.entry} cannot start a game installed as a folder.`,
    line: () => "cannot start a game installed as a folder",
  },
  nothing_to_start: {
    page: (launch) => `${launch.entry} finds nothing to start in ${launch.file}.`,
    line: () => "finds nothing to start in this folder",
  },
  not_a_game_file: {
    page: (launch) => `${launch.entry} cannot start ${launch.file}: it is neither a disc image nor a game program.`,
    line: (launch) => `${launch.file} is neither a disc image nor a game program`,
  },
  disc_not_found: {
    page: (launch) =>
      `${launch.entry} cannot start this game: it is a disc game, and no valid PS3_DISC.SFB stands beside its PS3_GAME folder.`,
    line: () => "the game's disc has no valid PS3_DISC.SFB",
  },
  disc_key_missing: {
    page: (launch) =>
      `${launch.entry} cannot start ${launch.file}: the image is encrypted and no key file for it was found.`,
    line: () => "the image is encrypted and has no key file",
  },
};

/**
 * What could not be established about a file the entry starts (#2195 D40), as
 * the page's note and the menu's line both finish their sentence.
 */
const STOPPED_SHORT_BECAUSE: Record<LaunchStoppedShortReason, (launch: GameLaunch) => string> = {
  content_missing: (launch) => `${launch.file} was not found`,
  content_unreadable: (launch) => `what is at ${launch.file} could not be read`,
  game_list_unread: () => "the emulator's own list of installed games was not read",
  game_kind_unfollowed: () => "the kind of game its PARAM.SFO names starts in a way that was not followed",
  inside_emulator_drive: () => "the game lies inside the emulator's own game folder, which starts games another way",
  emulator_drive_unknown: () => "where the emulator's internal drive is could not be read",
  savestate_unread: (launch) => `${launch.file} is a savestate, which was not read`,
  image_layout_unread: () => "the layout of the disc image could not be read",
  encryption_unread: () => "whether the disc image is encrypted could not be read",
  key_untested: () => "whether its key file fits the disc image was not checked",
  key_folder_unknown: () =>
    "no key file is beside the disc image, and where the emulator keeps its other key files could not be read",
  disc_folder_set: () =>
    "the emulator is set to use a disc folder of its own instead, and what that holds was not read",
  disc_folder_unknown: () => "where the emulator's own disc folder is could not be read",
  content_unchecked: (launch) =>
    `${launch.extension} files are taken, but the content of ${launch.file} was not checked`,
  version_dependent: (launch) => `whether it reads ${launch.extension} depends on the emulator's version`,
};

function refusedBecause(launch: GameLaunch): (typeof REFUSED_BECAUSE)[LaunchRefusalReason] | null {
  return launch.state === "not_this_emulator" && launch.reason !== null && launch.reason in REFUSED_BECAUSE
    ? REFUSED_BECAUSE[launch.reason as LaunchRefusalReason]
    : null;
}

function stoppedShortBecause(launch: GameLaunch): string | null {
  return launch.state === "starts" && launch.reason !== null && launch.reason in STOPPED_SHORT_BECAUSE
    ? STOPPED_SHORT_BECAUSE[launch.reason as LaunchStoppedShortReason](launch)
    : null;
}

/**
 * The entries the resolver names as starting the file, pointed at in the emulator
 * menu (#2195 D21), or nothing where it names none or the menu offers none.
 */
function otherEntriesPointer(launch: GameLaunch, canPick: boolean): string {
  if (!canPick || launch.other_entries.length === 0) return "";
  const choose = launch.other_entries.length === 1 ? "choose it" : "choose one";
  return ` ${launch.other_entries.join(" or ")} can — ${choose} in the emulator menu.`;
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
      const refused = refusedBecause(launch);
      if (refused !== null) return `${refused.page(launch)}${otherEntriesPointer(launch, canPick)}`;
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
 * there is nothing to say: whether it starts could not be established, what the
 * reading of the file stopped short of, or that the file is an archive whose
 * contents were not read. `source` is the source the answer came from.
 */
export function launchNote(launch: GameLaunch | null, source: AnsweringSource | null): string | null {
  if (launch === null) return null;
  if (launch.state === "not_established") {
    return `Whether ${launch.file} starts cannot be established: ${notEstablishedBecause(launch, source)}.`;
  }
  const stoppedShort = stoppedShortBecause(launch);
  if (stoppedShort !== null) return `Whether ${launch.entry} starts this game cannot be established: ${stoppedShort}.`;
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
      return refusedBecause(launch)?.line(launch) ?? `cannot start ${launch.extension} files`;
    case "not_established":
      return `whether it starts cannot be established: ${notEstablishedBecause(launch, source)}`;
    default: {
      const stoppedShort = stoppedShortBecause(launch);
      if (stoppedShort !== null) return `whether it starts the game is not established: ${stoppedShort}`;
      if (launch.format_unestablished) return `whether it reads ${launch.extension} is not established`;
      if (launch.contents_unread) return `what is inside ${launch.file} was not read`;
      return "can start it";
    }
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
