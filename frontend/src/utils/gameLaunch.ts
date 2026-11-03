/**
 * The words for whether an installed game's file starts, one home for each
 * state's sentence (#2195 D2). The game page, a refused start — the Play
 * button's and Steam's own — and the emulator menu say them from here.
 *
 * Three states refuse a start: the game has to be installed in an emulator
 * first, the file is not one the system starts from, or the entry does not
 * start it, with the resolver's reason where it states one. The other two let
 * it go ahead: it starts, which says nothing unless the resolver states
 * something beside it — its reading of the file stopped short of something, its
 * format included, the file is an archive whose contents were not read, the
 * start goes on without a file it opens, or the emulator does not read every
 * disc a playlist lists — each said in its own words (#2195 D40, D41; #2698 D7,
 * D8); or whether it starts could not be established, which says so. This is
 * the one home of that rule.
 *
 * Beside them are the notifications for a page whose check of whether the game
 * can start went wrong, and for a start pressed after it that then fails.
 */

import { HostTransportError } from "../api/host";
import type {
  AnsweringSource,
  GameLaunch,
  LaunchGoesOnReason,
  LaunchNoteReason,
  LaunchRefusalReason,
  LaunchStatement,
  LaunchStoppedShortReason,
} from "../types";
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
    line: () => "cannot start it: neither a disc image nor a game program",
  },
  disc_not_found: {
    page: (launch) =>
      `${launch.entry} cannot start this game: it is a disc game, and no PS3_GAME folder above it has a valid PS3_DISC.SFB beside it.`,
    line: () => "the game's disc cannot be mounted",
  },
  disc_key_missing: {
    page: (launch) =>
      `${launch.entry} cannot start ${launch.file}: the image is encrypted and no key file for it was found.`,
    line: () => "the image is encrypted and has no key file",
  },
  opened_file_missing: {
    page: (launch) => `${launch.entry} cannot start ${launch.file}: ${refusalFile(launch)} is missing.`,
    line: (launch) => `${refusalFile(launch)} is missing`,
  },
  track_refused: {
    page: (launch) =>
      `${launch.entry} cannot start ${launch.file}: ${refusalFile(launch)} is a track of a type it does not take.`,
    line: (launch) => `${refusalFile(launch)} is a track of a type it does not take`,
  },
  sheet_refused: {
    page: (launch) =>
      `${launch.entry} cannot start ${launch.file}: it refuses the cue sheet or playlist ${refusalFile(launch)}.`,
    line: (launch) => `it refuses the cue sheet or playlist ${refusalFile(launch)}`,
  },
  format_refused: {
    page: (launch) =>
      `${launch.entry} cannot start ${launch.file}: ${refusalFile(launch)} is not a disc image it opens.`,
    line: (launch) => `${refusalFile(launch)} is not a disc image it opens`,
  },
  path_too_long: {
    page: (launch) =>
      `${launch.entry} cannot start ${launch.file}: the path of ${refusalFile(launch)} is too long for it to open.`,
    line: (launch) => `the path of ${refusalFile(launch)} is too long for it to open`,
  },
  playlist_refused: {
    page: (launch) => `${launch.entry} cannot start ${launch.file}: it starts no game from a playlist.`,
    line: () => "it starts no game from a playlist",
  },
};

/**
 * What could not be established about a file the entry starts (#2195 D40), as
 * the page's note and the menu's line both finish their sentence. `file` is the
 * file the statement is about.
 */
const STOPPED_SHORT_BECAUSE: Record<LaunchStoppedShortReason, (launch: GameLaunch, file: string) => string> = {
  content_missing: (launch) => `${launch.file} was not found`,
  content_unreadable: () => "what RPCS3 would look at could not be read",
  game_list_unread: () => "the emulator's own list of installed games was not read",
  game_kind_unfollowed: () => "its PARAM.SFO names a kind of game whose start was not followed",
  inside_emulator_drive: () => "the game lies inside the emulator's own game folder, which starts games another way",
  emulator_drive_unknown: () => "where the emulator's internal drive is could not be read",
  savestate_unread: (launch) => `RPCS3 opens ${launch.file} as a savestate, which was not followed`,
  image_layout_unread: () =>
    "the disc image states a region count RPCS3 does not accept, and what its start does then was not followed",
  encryption_unread: () => "whether the disc image is encrypted could not be read",
  key_untested: () => "whether its key file fits the disc image was not checked",
  key_folder_unknown: () =>
    "no key file is beside the disc image, and where the emulator keeps its other key files could not be read",
  disc_folder_set: () =>
    "the emulator's own disc folder holds a disc it mounts instead, and which disc that boots was not followed",
  disc_folder_unknown: () => "where the emulator's own disc folder is could not be read",
  content_unchecked: (launch) =>
    `${launch.extension} files are taken, but the content of ${launch.file} was not checked`,
  version_dependent: (launch) => `whether it reads ${launch.extension} depends on the emulator's version`,
  opened_format_unread: (_launch, file) => `how it opens ${file} was not checked`,
  opened_content_unread: (_launch, file) => `what it makes of ${file} is not established`,
  opened_file_unread: (_launch, file) => `${file} could not be checked`,
  patch_setting_unread: () => "whether it loads a patch for the disc image could not be read",
  opens_past_bound: (_launch, file) =>
    `the check stopped at one of its limits at ${file}, so what it opens past that was not checked`,
  path_too_long_unchecked: (_launch, file) =>
    `the path of ${file} is too long for it to read in full, and what it does then was not checked`,
};

/**
 * What a start goes on without (#2698 D7): a file the emulator opens, which it goes on without, as the page's note
 * and the menu's line both finish their sentence.
 */
const GOES_ON_BECAUSE: Record<LaunchGoesOnReason, string> = {
  goes_on_file_missing: "it is missing",
  goes_on_track_refused: "it is a track of a type it does not take",
  goes_on_sheet_refused: "it refuses this cue sheet or playlist",
  goes_on_format_refused: "it is not a disc image it opens",
  goes_on_path_too_long: "its path is too long for it to use in full",
};

/**
 * What else the emulator is established to do short of the whole start (#2698 D7), as the page's note and the
 * menu's line both finish their sentence after "can start …, but".
 */
const STARTS_WITH_A_NOTE_BECAUSE: Record<LaunchNoteReason, (file: string) => string> = {
  playlist_discs_past_limit: (file) => `does not read every disc ${file} lists`,
};

/** One statement as the page's note says it and as the emulator menu's line says it. */
interface Said {
  note: string;
  line: string;
}

/**
 * *statement* in its own group's words (#2698 D8), or `null` where Tender words none. `tenderStarts` false drops
 * the menu's "can start it" (#2195 D42) and keeps the rest of the line.
 */
function said(launch: GameLaunch, statement: LaunchStatement, tenderStarts: boolean): Said | null {
  const file = statement.file ?? launch.file;
  const reason = statement.reason;
  if (reason in STOPPED_SHORT_BECAUSE) {
    const why = STOPPED_SHORT_BECAUSE[reason as LaunchStoppedShortReason](launch, file);
    return {
      note: `Whether ${launch.entry} starts this game cannot be established: ${why}.`,
      line: `whether it starts the game is not established: ${why}`,
    };
  }
  if (reason in GOES_ON_BECAUSE) {
    const without = `without ${file}: ${GOES_ON_BECAUSE[reason as LaunchGoesOnReason]}`;
    return {
      note: `${launch.entry} can start this game, but goes on ${without}.`,
      line: tenderStarts ? `can start it, ${without}` : without,
    };
  }
  if (reason in STARTS_WITH_A_NOTE_BECAUSE) {
    const note = STARTS_WITH_A_NOTE_BECAUSE[reason as LaunchNoteReason](file);
    return {
      note: `${launch.entry} can start this game, but ${note}.`,
      line: tenderStarts ? `can start it, but ${note}` : note,
    };
  }
  if (reason === "format_unestablished") {
    return {
      note: `Whether ${launch.entry} reads ${launch.extension} files is not established.`,
      line: `whether it reads ${launch.extension} is not established`,
    };
  }
  if (reason === "contents_unread") {
    return {
      note: `What is inside ${launch.file} was not read, so whether ${launch.entry} finds a game in it cannot be established.`,
      line: `what is inside ${launch.file} was not read`,
    };
  }
  return null;
}

/**
 * Everything said beside the verdict, in the resolver's order (#2698 D8): the answer's `statements`, or, on an
 * answer that carries none, its reason and its two notes.
 */
function statementsOf(launch: GameLaunch): LaunchStatement[] {
  if (launch.statements !== undefined) return launch.statements;
  if (launch.state !== "starts") return [];
  const statements: LaunchStatement[] = [];
  if (launch.reason !== null) {
    // On a start the reason is a statement's word; one Tender words none of is left unsaid by `said`.
    statements.push({ reason: launch.reason as LaunchStatement["reason"], file: launch.reason_file ?? null });
  }
  if (launch.format_unestablished) statements.push({ reason: "format_unestablished", file: null });
  if (launch.contents_unread) statements.push({ reason: "contents_unread", file: null });
  return statements;
}

/** Every statement beside the verdict that Tender words, in order. */
function allSaid(launch: GameLaunch, tenderStarts: boolean): Said[] {
  return statementsOf(launch).flatMap((statement) => said(launch, statement, tenderStarts) ?? []);
}

/** The file the refusal's reason is about, named as the sentences name it: its name, else the game's own file. */
function refusalFile(launch: GameLaunch): string {
  return launch.reason_file ?? launch.file;
}

/**
 * *text* as a sentence of its own, with a full stop after it: its first letter capitalized, unless it begins with
 * the name of a file in *files*, which is never changed.
 */
function asSentence(text: string, files: readonly string[]): string {
  if (files.some((file) => text.startsWith(file))) return ` ${text}.`;
  return ` ${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}

/**
 * What a refused start says after its own sentence (#2698 D8): each file the emulator would have gone on without,
 * what else it is established to do, then each thing the resolver could not establish, each as a sentence of its
 * own.
 */
function besideARefusal(launch: GameLaunch): string[] {
  const statements = statementsOf(launch);
  const goesOn = statements.flatMap((statement) =>
    statement.reason in GOES_ON_BECAUSE
      ? [
          ` It would go on without ${statement.file ?? launch.file}: ${GOES_ON_BECAUSE[statement.reason as LaunchGoesOnReason]}.`,
        ]
      : [],
  );
  const notes = statements.flatMap((statement) =>
    statement.reason in STARTS_WITH_A_NOTE_BECAUSE
      ? [` It ${STARTS_WITH_A_NOTE_BECAUSE[statement.reason as LaunchNoteReason](statement.file ?? launch.file)}.`]
      : [],
  );
  const notEstablished = statements.flatMap((statement) => {
    const file = statement.file ?? launch.file;
    const files = [file, launch.file];
    if (statement.reason in STOPPED_SHORT_BECAUSE) {
      return [asSentence(STOPPED_SHORT_BECAUSE[statement.reason as LaunchStoppedShortReason](launch, file), files)];
    }
    if (statement.reason === "format_unestablished" || statement.reason === "contents_unread") {
      return [asSentence(said(launch, statement, true)!.line, files)];
    }
    return [];
  });
  return [...goesOn, ...notes, ...notEstablished];
}

function refusedBecause(launch: GameLaunch): (typeof REFUSED_BECAUSE)[LaunchRefusalReason] | null {
  return launch.state === "not_this_emulator" && launch.reason !== null && launch.reason in REFUSED_BECAUSE
    ? REFUSED_BECAUSE[launch.reason as LaunchRefusalReason]
    : null;
}

/** A menu line with the count of the statements it does not show (#2698 D8). */
function withMore(line: string, more: number): string {
  return more > 0 ? `${line} +${more} more` : line;
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
 * Why the game cannot start with its entry, or `null` where the verdict lets it
 * start: the refusal's sentence, then what the resolver says beside it (#2698
 * D8). `canPick` says the emulator menu offers another entry; only then does the
 * text point at it.
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
      const beside = besideARefusal(launch).join("");
      if (refused !== null) return `${refused.page(launch)}${beside}${otherEntriesPointer(launch, canPick)}`;
      if (launch.other_entries.length === 0) return `${launch.entry} does not start ${launch.extension}.${beside}`;
      const pointer = canPick ? " Choose another emulator in the emulator menu." : "";
      return `${launch.entry} does not start ${launch.extension}; other emulators of this system do.${beside}${pointer}`;
    }
    default:
      return null;
  }
}

/**
 * Everything the page says under Play about a verdict that lets the game start, in order, each in its own group's
 * words and none of them weighed against another (#2698 D8): whether it starts could not be established, what the
 * reading of the file stopped short of, what its start goes on without or what else it is established to do short
 * of the whole start, whether the entry reads its format (#2195 D41), and that the file is an archive whose
 * contents were not read. Empty where there is nothing to say. `source` is the source the answer came from.
 */
export function launchNotes(launch: GameLaunch | null, source: AnsweringSource | null): string[] {
  if (launch === null) return [];
  if (launch.state === "not_established") {
    return [`Whether ${launch.file} starts cannot be established: ${notEstablishedBecause(launch, source)}.`];
  }
  if (launch.state !== "starts") return [];
  return allSaid(launch, true).map((each) => each.note);
}

/** The first of {@link launchNotes}, or `null` where there is nothing to say. */
export function launchNote(launch: GameLaunch | null, source: AnsweringSource | null): string | null {
  return launchNotes(launch, source)[0] ?? null;
}

/**
 * The short line the emulator menu shows under an entry for its verdict on the
 * game (#2195 D22, D29, D36), or `null` where there is nothing to say: the first
 * statement, with how many more there are (#2698 D8), or a plain "can start it"
 * — none for an entry Tender cannot start (`tenderStarts` false), which its row
 * already says it cannot (D42); beside a statement, only the "can start it"
 * goes. `source` is the source the answer came from.
 */
export function entryLaunchLine(
  launch: GameLaunch,
  source: AnsweringSource | null,
  tenderStarts = true,
): string | null {
  switch (launch.state) {
    case "needs_installing":
      return "cannot start it: the game has to be installed first";
    case "not_a_launch_file":
      return `${launch.platform_name} does not start from ${launch.extension}`;
    case "not_this_emulator": {
      const more = besideARefusal(launch).length;
      return withMore(refusedBecause(launch)?.line(launch) ?? `cannot start ${launch.extension} files`, more);
    }
    case "not_established":
      return `whether it starts cannot be established: ${notEstablishedBecause(launch, source)}`;
    default: {
      const lines = allSaid(launch, tenderStarts).map((each) => each.line);
      if (lines.length > 0) return withMore(lines[0]!, lines.length - 1);
      return tenderStarts ? "can start it" : null;
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

/**
 * What the notification says where a start, pressed after the page's check
 * went wrong, then fails itself (#2195 D33).
 */
export function startFailedSentence(e: unknown): string {
  return `Tender could not start this game: ${e instanceof Error ? e.message : String(e)}`;
}
