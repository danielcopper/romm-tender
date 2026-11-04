/**
 * A multi-disc game's disc selection: what the picker shows for it, reading it,
 * and applying a pick. The glyphs and the menu's options are
 * UI, in `shared/DiscGlyphs.tsx`.
 */

import { getDiscSelection, selectDisc, logError, logWarn } from "../api/backend";
import type { DiscSelection } from "../api/backend";
import { showToast } from "./toast";
import { capturePruneLeaseAdmission, isPruneLeaseCancellation } from "./pruneLease";

/** A disc option's data value: a disc filename, or null for the m3u default. */
export type DiscOptionData = string | null;

export interface DiscDisplayState {
  isM3u: boolean;
  effectiveSelected: DiscOptionData;
  isPinned: boolean;
  showPlaylistFace: boolean;
  activeNum: string;
}

/**
 * Derives the effective disc selection, playlist state, pin state, and active disc number
 * from the backend selection response and locally tracked pin.
 * Returns null if the selection is not multi-disc or lacks required metadata.
 */
export function computeDiscDisplayState(
  selection: DiscSelection | null | undefined,
  selected: DiscOptionData,
): DiscDisplayState | null {
  // Single-disc / unknown / not-installed → render nothing.
  if (!selection?.multi_disc || !selection.discs || !selection.default) return null;

  const { discs, default: dflt } = selection;
  const isM3u = dflt.kind === "m3u";
  // The effective pin: an explicit selection, else the default target (null for
  // m3u, disc 1's filename otherwise).
  const effectiveSelected: DiscOptionData = selected ?? (isM3u ? null : dflt.filename);
  const isPinned = selected !== null;
  // The m3u playlist is active only when the default is m3u and nothing is pinned.
  const showPlaylistFace = isM3u && selected === null;
  const activeDisc = discs.find((d) => d.filename === effectiveSelected);
  const activeNum = activeDisc ? (/\d+/.exec(activeDisc.label)?.[0] ?? String(activeDisc.index)) : "";

  return {
    isM3u,
    effectiveSelected,
    isPinned,
    showPlaylistFace,
    activeNum,
  };
}

/**
 * Queries disc selection metadata for the given ROM id.
 */
export async function fetchDiscSelection(romId: number, logTag = "DiscSelector"): Promise<DiscSelection | null> {
  try {
    return await getDiscSelection(romId);
  } catch (e) {
    logError(`${logTag}: getDiscSelection failed: ${e}`);
    return null;
  }
}

export interface ExecuteDiscSelectionParams {
  romId: number;
  data: DiscOptionData;
  leaseOwner: string;
  onSelected?: (selected: DiscOptionData) => void;
  logTag?: string;
}

/** Pin the disc *data* (`null` follows the default again); *onSelected* hears the
 *  pin once it holds. The next start opens that disc. */
export async function executeDiscSelection({
  romId,
  data,
  leaseOwner,
  onSelected,
  logTag = "DiscSelector",
}: ExecuteDiscSelectionParams): Promise<void> {
  const admission = capturePruneLeaseAdmission(leaseOwner);
  try {
    const result = await selectDisc(romId, data);
    if (result.success) {
      onSelected?.(result.selected ?? null);
    } else {
      showToast(result.message || "Failed to select disc");
    }
  } catch (e) {
    // Leaving the game page cancels the pick — the disc may already be
    // persisted backend-side, so that is teardown and not a failure.
    if (isPruneLeaseCancellation(e, admission)) {
      logWarn(`${logTag}: disc selection continuation was cancelled: ${e}`);
      return;
    }
    // Observable catch effect: surface the failure so the user knows the pick
    // didn't take, and leave `selected` unchanged (revert to the prior pin).
    logError(`${logTag}: selectDisc failed: ${e}`);
    showToast("Failed to select disc");
  }
}
