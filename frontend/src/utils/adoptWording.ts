/**
 * The words of the "already on your device" dialogs a download opens (#260,
 * ADR-0028) — every word of theirs Tender writes, fixed labels included, except
 * the kind of an entry ("file", "folder", "shortcut"), which is
 * `ENTRY_KIND_LABEL` in `formatters.ts` — and the toasts about the same case:
 * how a Download press ended, and a resume refused because something now sits
 * at the game's location. One home, so every surface that draws those
 * dialogs states the same case the same way; the drawing itself stays with each
 * surface. Why a sentence says what it says sits on the constant
 * or function that builds it, so whoever draws a dialog sees which sentences
 * must not be softened.
 */

import { ENTRY_KIND_LABEL, formatBytes } from "./formatters";
import type {
  AdoptionCandidate,
  CandidatesFoundResult,
  CandidateVanishedResult,
  RenameCollision,
  TargetOccupiedResult,
  UnusableNamesakeResult,
} from "../types";

// ── Every dialog ──

/** The exit every one of the dialogs offers; it changes nothing on disk. */
export const CANCEL_LABEL = "Cancel";

// ── The comparison dialog: content at the game's location, or one candidate ──

export const EXISTING_TITLE = "This Game Is Already on Your Device";

/** The heading over the side that describes what is on disk. */
export const ON_THIS_DEVICE_HEADING = "On this device";

/** The heading over the side that describes what the server would send. */
export const ON_THE_SERVER_HEADING = "On the server";

export const CHECK_AGAINST_SERVER_LABEL = "Check Against Server";

export const DOWNLOAD_INSTEAD_LABEL = "Download Instead";

/** Names the deletion it confirms, and is never shortened to a label that hides it. */
export const DELETE_AND_DOWNLOAD_LABEL = "Delete and Download";

export const GO_BACK_LABEL = "Go Back";

/** Locale-formatted date and time from POSIX epoch seconds; the empty string for a zero stamp. */
export function formatModifiedAt(epochSeconds: number): string {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString();
}

/**
 * The noun for what is in the way. A `null` kind is something the backend looked
 * at and has no word for — a named pipe, a socket — so this has none either,
 * rather than calling it a file: that guess is what let one be offered as a game.
 */
export function nounFor(occupied: TargetOccupiedResult): string {
  const kind = occupied.existing.kind;
  return kind === null ? "thing" : ENTRY_KIND_LABEL[kind];
}

/**
 * Whether what is at the path is the game's own content, and so whether the
 * numbers `stat` returned describe the game at all. Only a file or a directory
 * is: a symlink's size and mtime are the link's own — when it was pointed
 * somewhere, not when the game was last touched — and a kindless entry's belong
 * to something the plugin has no word for.
 *
 * Everything under "On this device" is read as being about this game's copy, so
 * a measurement that is not gets left out rather than qualified. Half that
 * column already says so where the size would be; a bare "Last changed" beside
 * it is the one line still implying otherwise.
 */
export function describesTheGame(occupied: TargetOccupiedResult): boolean {
  return occupied.existing.kind === "file" || occupied.existing.kind === "dir";
}

/**
 * The "Last changed …" line under the existing side, or `null` when it must not
 * be shown — no stamp, or a stamp that is not the game's (`describesTheGame`).
 */
export function lastChangedLine(occupied: TargetOccupiedResult): string | null {
  const modified = formatModifiedAt(occupied.existing.modified_at);
  return modified && describesTheGame(occupied) ? `Last changed ${modified}` : null;
}

/**
 * The sentence under the title. `candidate`, here and in `existingSize`,
 * `sizeVerdict` and `replaceWarning`, is what `AdoptionDialogs.showExisting`
 * (`adoptFlow.ts`) says a `candidatePath` means.
 */
export function existingIntro(occupied: TargetOccupiedResult, candidate: boolean): string {
  const noun = nounFor(occupied);
  return candidate
    ? `This ${noun} carries this game's name. Tender did not put it there, so it will not be touched until you decide.`
    : `A ${noun} is already where this game would be downloaded. Tender did not put it there, so it will not be ` +
        "touched until you decide.";
}

/**
 * How the existing side's size is stated. Only a file or a folder has a byte
 * count that is the game's; a shortcut's `stat` reports the length of the path
 * it stores, which is a real-looking number about nothing the user is deciding
 * on, and a kindless entry's is not the game's either. Those say so instead —
 * printing the number and disclaiming it two lines below still puts it beside
 * the server's real one, to be read as a comparison.
 *
 * A candidate folder is the third case and a different reason: the search stays
 * on the platform folder's top level, because descending into one multi-file
 * game can mean tens of thousands of files, so nothing measured it. "0 B" about
 * something that may be gigabytes is the one thing this must not print.
 */
export function existingSize(occupied: TargetOccupiedResult, candidate: boolean): string {
  if (candidate && occupied.existing.kind === "dir") return "Folder — not measured";
  if (occupied.existing.kind === "link") return "Shortcut — no size of its own";
  if (occupied.existing.kind === null) return "No size to show";
  return formatBytes(occupied.existing.size_bytes);
}

/** How the server side's size is stated; a zero is the server stating none. */
export function incomingSize(occupied: TargetOccupiedResult): string {
  return occupied.incoming.size_bytes ? formatBytes(occupied.incoming.size_bytes) : "Size unknown";
}

/**
 * One sentence on how the two sizes relate — never two bare numbers to subtract,
 * and never our own choice not to measure reported as the server's silence.
 */
export function sizeVerdict(occupied: TargetOccupiedResult, candidate: boolean): string {
  if (candidate && occupied.existing.kind === "dir") {
    return "Folders are not measured before you open this, so the two sizes are not compared.";
  }
  if (occupied.existing.kind === "link") {
    return "A shortcut is not the game's bytes, so there is nothing here to compare.";
  }
  if (occupied.existing.kind === null) return "This is not a file or a folder, so there is nothing here to compare.";
  if (occupied.sizes_match === null) return "The server did not state a size, so the two cannot be compared.";
  if (occupied.sizes_match) return "Both are the same size.";
  const delta = occupied.existing.size_bytes - occupied.incoming.size_bytes;
  return delta > 0
    ? `What is here is ${formatBytes(delta)} larger than what the server would send.`
    : `What is here is ${formatBytes(-delta)} smaller than what the server would send.`;
}

/**
 * Shown only for a candidate: content at the game's own location is used where
 * it lies, while a candidate is renamed into place, saves and savestates with it,
 * so an adopted install ends up indistinguishable from a downloaded one. Stated
 * before the user chooses, because the rename is a change to their own filing.
 */
export function renameNotice(occupied: TargetOccupiedResult): string {
  return (
    `Using it renames it to ${occupied.incoming.name}, and moves any saves and savestates named after it with it, ` +
    "so this game works the same as one Tender downloaded."
  );
}

/** The adopt exit's label: the offer, or — disabled — why it is not one. */
export function adoptButtonLabel(occupied: TargetOccupiedResult): string {
  return occupied.adoptable ? "Use These Files" : `Can't use this ${nounFor(occupied)} for this game`;
}

/**
 * The content check's in-flight line; `progress` is 0..1. A check sets it to 0
 * when it starts, so no frame yet and a zero frame read alike; `null` (no check
 * running) reads the same, though no dialog shows the line then.
 */
export function verifyProgressLabel(progress: number | null): string {
  return progress === null || progress === 0
    ? "Checking the files…"
    : `Checking the files… ${Math.round(progress * 100)}%`;
}

/** The verdict shown when the content check never reached the server. */
export const VERIFY_UNREACHABLE_MESSAGE = "Couldn't reach the server to check these files";

/**
 * What the second confirmation promises will be destroyed. Three sentences,
 * because three different things are: a file or folder may be the user's own
 * dump and is gone for good, a shortcut is one line of filesystem bookkeeping
 * whose target survives, and a kindless entry is something the plugin can only
 * say it is removing.
 */
export function replaceWarning(occupied: TargetOccupiedResult, candidate: boolean): string {
  const name = occupied.existing.name;
  if (occupied.existing.kind === "link") {
    return `Downloading deletes the shortcut that is here now — ${name}. Whatever it points at is left alone. Continue?`;
  }
  if (occupied.existing.kind === null) {
    return `Downloading removes what is here now — ${name}. Tender cannot tell what it is, only that it goes. Continue?`;
  }
  return (
    `Downloading deletes the ${nounFor(occupied)} that is here now — ${name}, ${existingSize(occupied, candidate)}. ` +
    "If it is your own dump, patch or romhack, it is gone. Continue?"
  );
}

// ── The candidate list: two or more files under another name ──

export const CANDIDATES_TITLE = "This Game May Already Be on Your Device";

export const CANDIDATES_INTRO =
  "These files sit in the same folder and carry this game's name. Tender did not put them there, so nothing is " +
  "touched until you pick one.";

/** The line under a candidate's name: what its offer rests on, then its size. */
export function candidateDetail(candidate: AdoptionCandidate): string {
  const size = candidate.is_dir ? "folder" : formatBytes(candidate.size_bytes);
  return `${candidate.detail} — ${size}`;
}

/** Shown only when `found.truncated`. */
export function candidatesTruncatedNote(found: CandidatesFoundResult): string {
  return `Only the ${found.candidates.length} strongest matches are shown — there are more in this folder.`;
}

export function noneOfTheseLabel(found: CandidatesFoundResult): string {
  return `None of These — Download ${found.incoming.name}`;
}

// ── The collision decision: names the rename needs are taken ──

export const COLLISIONS_TITLE = "Some of These Names Are Taken";

/**
 * Names the files, never the game: on one of the two paths that reach this
 * dialog the game is not renamed at all.
 */
export const COLLISIONS_INTRO =
  "Moving this game's files to the name your server uses would land on files that already exist. Nothing has been " +
  "moved yet.";

export const COLLISION_KIND_LABEL: Record<RenameCollision["kind"], string> = {
  rom: "game file",
  save: "save",
  savestate: "savestate",
};

export const COLLISIONS_REPLACE_LABEL = "Replace Them";
export const COLLISIONS_KEEP_LABEL = "Keep Them";

/**
 * Says for both exits that nothing is destroyed, because this dialog is the only
 * place the user sees it while choosing. Implying that Keep's move was clean is
 * the one thing this sentence must not do.
 */
export const COLLISIONS_CONSEQUENCES =
  "Replace does not delete the files listed above — each is moved into a .romm-backup folder beside it, so you can " +
  "put one back by hand if you pick wrong. Keep leaves them alone and leaves this game's old-named saves where they " +
  "are — nothing is lost, but nothing will be reading them either.";

// ── The namesake nothing can adopt ──

export const UNUSABLE_TITLE = "Something With This Name Is Already Here";

/**
 * There is nothing to take over, so this does not offer a choice between copies:
 * it says the download produces a **second copy** beside the first. Saying that
 * out loud is the whole point — the button that led here may well have read
 * *Use Existing Files*, and a multi-gigabyte transfer starting after that with no
 * word would be the worst of both.
 */
export function unusableIntro(unusable: UnusableNamesakeResult): string {
  const servedWord = unusable.served_is_dir ? "a folder of several files" : "a single file";
  return (
    `Your server sends this game as ${servedWord}, and what is in this folder is not something Tender can use as ` +
    "this game. Downloading leaves you with two copies — the one below, and the one it fetches."
  );
}

/** Shown only when `unusable.truncated`. */
export function unusableTruncatedNote(unusable: UnusableNamesakeResult): string {
  return `Only the first ${unusable.existing.length} are shown — there are more in this folder.`;
}

export function unusableDownloadLabel(unusable: UnusableNamesakeResult): string {
  return `Download ${unusable.incoming.name} Anyway`;
}

export const UNUSABLE_DOWNLOAD_NOTE =
  "Nothing above is renamed, moved or deleted — the download lands beside it under your server's name.";

// ── The backstop: the page found a copy and the search now finds nothing ──

export const VANISHED_TITLE = "The Copy on This Device Cannot Be Found";

/**
 * Claims no cause, because none is known: what the page found is either gone or
 * no longer matches, and both are true of the ordinary case where the file was
 * deleted between opening the page and pressing.
 */
export const VANISHED_INTRO =
  "This game's page found a copy on this device, and looking again now turns up nothing that matches. Nothing has " +
  "been changed on your device.";

export function vanishedDownloadLabel(vanished: CandidateVanishedResult): string {
  return `Download ${vanished.incoming.name}`;
}

export const VANISHED_DOWNLOAD_NOTE = "Or cancel and look in the folder yourself first.";

// ── Toasts: how a Download press ended, and a resume refused over the location ──

/** A refused download whose answer carried no message of its own. */
export const DOWNLOAD_REFUSED_TOAST = "Download failed";

/** The download request threw: no verdict came back at all. */
export const DOWNLOAD_THREW_TOAST = "Download failed — is RomM server running?";

/** A refused adoption whose answer carried no message of its own. */
export const ADOPT_REFUSED_TOAST = "Couldn't use the existing files";

/** The adoption request threw: no verdict came back at all. */
export const ADOPT_THREW_TOAST = "Couldn't use the existing files — is RomM server running?";

/** The adoption is recorded; a ROM with no name reads as "ROM". */
export function adoptedToast(romName: string): string {
  return `${romName || "ROM"} is ready to play`;
}

/** The toast for a paused download whose resume found something else at the game's location. */
export const RESUME_TARGET_OCCUPIED_TOAST =
  "Something else is at this game's location now — cancel the download and start again";
