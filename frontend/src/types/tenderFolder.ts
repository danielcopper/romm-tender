import type { EndpointFailure } from "./api";
import type { WireReasonSubset } from "./reasons";

/**
 * Tender's folder as the backend answers it: where it lies, whether it can be
 * used, which other folders something is recorded under, and the drives a
 * place may lie on.
 */

/** Why the folder cannot be used now: its drive gone, the folder missing, or not writable. */
export type TenderFolderCondition = "drive_not_mounted" | "folder_missing" | "folder_not_writable";

/** A writable drive mounted under `/run/media/<user>/`, offered as a place with `Tender` at its root. */
export interface TenderFolderDrive {
  name: string;
  mount_point: string;
  /** The folder a choice of this drive makes, `Tender` at its root. */
  folder: string;
  free_bytes: number;
}

/** The answer of `get_tender_folder` and of the writes beside it. */
export interface TenderFolderState {
  /** The chosen place, the folder `Tender` lies in. */
  place: string;
  /** The folder itself. */
  folder: string;
  /** Whether the place is the internal default, `~/Tender`. */
  default: boolean;
  /** Why the folder cannot be used now, whatever is recorded under it; `null` where it can. */
  condition: TenderFolderCondition | null;
  /** The condition, only while something is recorded under the folder: Main's notice. */
  unreachable: TenderFolderCondition | null;
  /** The other folders something is recorded under, as the stores gave them. */
  elsewhere: string[];
  drives: TenderFolderDrive[];
}

/** What to ask before a typed place is chosen: the answer of `check_tender_place`. */
export interface TenderPlaceAsks {
  place: string;
  folder: string;
  /** The folder above a typed place whose last folder is named Tender, offered instead; `null` otherwise. */
  tender_parent: string | null;
  /** The deepest folder of the place that exists. */
  existing: string;
  /** The part below `existing` still to be created; `null` where the whole place exists. */
  missing: string | null;
}

/** Why a place was refused when it was checked or chosen. */
export type TenderPlaceRefusal = EndpointFailure & {
  reason: WireReasonSubset<
    | "place_in_rom_folder"
    | "place_above_rom_folder"
    | "place_not_writable"
    | "place_not_absolute"
    | "place_drive_not_mounted"
    | "sources_unreadable"
    | "prune_active"
    | "invalid_value"
  >;
  /** The source whose ROM folder the place lies in or above, by kind. */
  source?: string;
  /** That ROM folder, or the system's folder. */
  folder?: string;
  /** The system whose folder the place lies in; `null` for the source's ROM folder itself. */
  system?: string | null;
  /** Tender's folder at the place, as the place was typed with `/Tender` after it. */
  path?: string;
};

/** Why going back to a folder was refused. */
export type TenderFolderRefusal = EndpointFailure & {
  reason: WireReasonSubset<"invalid_value" | "prune_active">;
};

/** Why forgetting what is recorded under a folder was refused: the rules forgetting a download holds, or a stale view. */
export type TenderFolderForgetRefusal = EndpointFailure & {
  reason: WireReasonSubset<"invalid_value" | "blocked_by_update" | "blocked_by_migration" | "prune_active">;
};
