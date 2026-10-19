/**
 * Every sentence the panel says about Tender's folder: Main's two notices, the
 * forget confirmation, the two questions asked before a typed place is chosen,
 * the settings rows, and a refused choice that names an emulator source. A
 * refusal that names no source is worded by the backend and shown as it came.
 */

import type { TenderFolderCondition, TenderFolderDrive, TenderPlaceRefusal } from "../types";
import { sourceName } from "./emulatorSourceWording";
import { formatBytes } from "./formatters";

export const TENDER_FOLDER_SECTION = "Tender folder";

const CONDITION_ENDINGS: Record<TenderFolderCondition, string> = {
  drive_not_mounted: "the drive is not connected.",
  folder_missing: "the folder is missing.",
  folder_not_writable: "the folder cannot be written to.",
};

/** The folder and why it cannot be used, as Main's notice and the settings' current place say it. */
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

export function useParentButton(parent: string): string {
  return `Use ${parent}`;
}

export const KEEP_AS_TYPED = "Keep as typed";

export const CURRENT_PLACE = "Current place";
export const INTERNAL_DEFAULT = "Internal storage — ~/Tender (default)";
export const ANOTHER_FOLDER = "Another folder…";

export function driveRowLabel(drive: TenderFolderDrive): string {
  return `${drive.name} — ${drive.folder}`;
}

export function driveFreeLine(drive: TenderFolderDrive): string {
  return `${formatBytes(drive.free_bytes)} free`;
}

/** The sentence a refused choice is shown with: the source named by Tender's name for it, else the backend's. */
export function placeRefusalSentence(refusal: TenderPlaceRefusal): string {
  const { source, folder, path } = refusal;
  if (source === undefined || folder === undefined || path === undefined) return refusal.message;
  const name = sourceName(source);
  if (refusal.reason === "place_above_rom_folder") {
    return `${name}'s ROM folder (${folder}) lies inside ${path}. Choose a place that holds no ROM folder.`;
  }
  if (refusal.reason === "place_in_rom_folder") {
    const where = refusal.system ? `${name}'s folder for ${refusal.system}` : `${name}'s ROM folder`;
    return `${path} lies inside ${where} (${folder}). Choose a place outside it.`;
  }
  return refusal.message;
}
