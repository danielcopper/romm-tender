import type { TenderFolderState } from "../types";

/** Tender's folder at its default place, created and writable, with nothing recorded elsewhere and no drive. */
export const TENDER_FOLDER_AT_HOME: TenderFolderState = {
  place: "/home/deck",
  folder: "/home/deck/Tender",
  default: true,
  condition: null,
  unreachable: null,
  elsewhere: [],
  drive: null,
  home_free_bytes: 41 * 1024 ** 3,
  drives_root: "/run/media/deck",
  drives: [],
};
