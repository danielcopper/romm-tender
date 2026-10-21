/**
 * Every sentence the panel says about Tender's folder: Main's two notices, the
 * forget confirmation, the two questions asked before a typed place is chosen,
 * the settings section's status line, tiles and "Another folder…" field, and a
 * refused choice that names an emulator source. A refusal that names no source
 * is worded by the backend and shown as it came.
 */

import type {
  EndpointFailure,
  TenderFolderCondition,
  TenderFolderDriveKind,
  TenderFolderPlaceDrive,
  TenderFolderState,
  TenderPlaceRefusal,
} from "../types";
import { sourceName } from "./emulatorSourceWording";
import { formatBytes } from "./formatters";

export const TENDER_FOLDER_SECTION = "Tender folder";

const CONDITION_ENDINGS: Record<TenderFolderCondition, string> = {
  drive_not_mounted: "the drive is not connected.",
  folder_missing: "the folder is missing.",
  folder_not_writable: "the folder cannot be written to.",
};

/** The folder and why it cannot be used, as Main's notice says it. */
export function folderConditionLine(folder: string, condition: TenderFolderCondition): string {
  return `${folder}: ${CONDITION_ENDINGS[condition]}`;
}

export const UNREACHABLE_TITLE = "Tender's folder cannot be reached";
export const ELSEWHERE_TITLE = "Tender's files are recorded elsewhere";
export const NOTHING_REMOVED = "Nothing has been removed.";
export const CHOOSE_ANOTHER_PLACE = "Choose another place";
export const FORGET_RECORDED = "Forget what is recorded there";
export const USE_THAT_FOLDER = "Use that folder";

export function elsewhereLine(current: string, other: string): string {
  return `The settings name ${current} as Tender's folder, but Tender's files are recorded under ${other}.`;
}

export function forgetTitle(folder: string): string {
  return `Forget everything Tender has recorded under ${folder}?`;
}

export const FORGET_DESCRIPTION =
  "Its games will show as not downloaded, and Tender's links to its files are removed. No file is deleted.";
export const FORGET_BUTTON = "Forget";

export function createMissingQuestion(place: string, existing: string, missing: string): string {
  return `${place} does not exist yet; only ${existing} does. Create ${missing}/ there?`;
}

export const CREATE_BUTTON = "Create";

export function tenderNamedQuestion(place: string, parent: string): string {
  return (
    "Tender keeps its files in a folder named Tender inside the place you choose, " +
    `so this would make ${place}/Tender. Use ${parent} instead?`
  );
}

export function parentButton(parent: string): string {
  return `Use ${parent}`;
}

export const KEEP_AS_TYPED = "Keep as typed";

const DRIVE_KINDS: Record<TenderFolderDriveKind, string> = {
  sd_card: "SD card",
  usb_drive: "USB drive",
  drive: "Drive",
};

export const INTERNAL_KIND = "Internal storage";
export const INTERNAL_NAME = "This device";
export const INTERNAL_FOLDER = "~/Tender";
export const DEFAULT_TAG = "Default";
export const IN_USE_BADGE = "In use";
export const ANOTHER_FOLDER = "Another folder…";
const ANOTHER_FOLDER_IN_USE = "Another folder";
export const ANOTHER_FOLDER_LINE = "Type a path, for example a folder on one of the drives above";
export const ANOTHER_FOLDER_FIELD = "Tender puts its folder named Tender inside the place you enter.";
export const NOT_CONNECTED = "Not connected";
export const NO_DRIVE_NOTE = "Insert an SD card or connect a drive to keep Tender's folder there.";

/** The example under the "Another folder…" field, with the drives root this machine mounts drives in. */
export function anotherFolderExample(drivesRoot: string): string {
  return `For example ~/Games or ${drivesRoot}/Archive`;
}

/** What kind of drive a tile names, before the drive's own name. */
export function driveKind(drive: { kind: TenderFolderDriveKind }): string {
  return DRIVE_KINDS[drive.kind];
}

/** The drive whose tile is the place in use — the drive's root is the place — connected or not; `null` otherwise. */
export function driveInUse(state: TenderFolderState): TenderFolderPlaceDrive | null {
  return !state.default && state.drive !== null && state.drive.mount_point === state.place ? state.drive : null;
}

/** The place in use, as the status line names it: the internal storage, a drive by kind and name, or another folder. */
export function inUseLabel(state: TenderFolderState): string {
  if (state.default) return `In use: ${INTERNAL_KIND}`;
  const drive = driveInUse(state);
  return drive === null ? `In use: ${ANOTHER_FOLDER_IN_USE}` : `In use: ${driveKind(drive)} · ${drive.name}`;
}

const CONDITION_PILLS: Record<TenderFolderCondition, string> = {
  drive_not_mounted: NOT_CONNECTED,
  folder_missing: "Missing",
  folder_not_writable: "Not writable",
};

/** The pill beside the place in use: reachable, or why its folder cannot be used. */
export function conditionPill(condition: TenderFolderCondition | null): string {
  return condition === null ? "Reachable" : CONDITION_PILLS[condition];
}

/** The toast after a switch: the new place by the name its tile carries, or its folder where no tile names it. */
export function switchedToast(state: TenderFolderState): string {
  const drive = driveInUse(state);
  const label = state.default ? INTERNAL_NAME : drive === null ? state.folder : `${driveKind(drive)} · ${drive.name}`;
  return `Tender's folder is now on ${label}.`;
}

export function freeLine(bytes: number): string {
  return `${formatBytes(bytes)} free`;
}

/** The sentence a refusal is shown with: a refused choice names its source by Tender's name for it, every other
 *  refusal is shown as the backend worded it. */
export function placeRefusalSentence(
  refusal: EndpointFailure & Pick<TenderPlaceRefusal, "source" | "folder" | "system" | "path">,
): string {
  const { source, folder, path } = refusal;
  if (source === undefined || folder === undefined || path === undefined) return refusal.message;
  const name = sourceName(source);
  if (refusal.reason === "place_above_rom_folder") {
    return `${name}'s ROM folder (${folder}) lies inside ${path}. Choose another place.`;
  }
  if (refusal.reason === "place_in_rom_folder") {
    const where = refusal.system ? `${name}'s folder for ${refusal.system}` : `${name}'s ROM folder`;
    return `${path} lies inside ${where} (${folder}). Choose a place outside it.`;
  }
  return refusal.message;
}
