/**
 * Auto-artwork for a ROM shortcut's game page: SteamGridDB artwork once per
 * (appId, romId) pair.
 */

import { useEffect } from "react";
import { applyArtwork, fillEmptyArtwork } from "./artwork";
import { debugLog } from "../api/backend";

/** Which rom_id each appId has had auto-artwork applied for this session. Keyed
 *  on the pair, not the appId alone: a version switch re-binds the appId to a
 *  new rom_id whose artwork has to be applied afresh (#1298 item 3). */
const artworkApplied = new Map<number, number>();

export function _resetArtworkAppliedForTests(): void {
  artworkApplied.clear();
}

/**
 * Hook to auto-apply SteamGridDB artwork once per (appId, romId) pair.
 *
 * @param appId Steam shortcut App ID
 * @param romId RomM ROM ID, or null if unassigned / unknown
 * @param logContext Prefix for debug logging on failure
 */
export function useAutoArtwork(appId: number, romId: number | null, logContext: string = "Auto-artwork error"): void {
  // Apply SGDB artwork once per (appId, rom_id). A first visit fills only the
  // empty slots, so an image already there keeps it; a version switch (an
  // earlier rom_id applied for this appId) replaces every slot, as Refresh
  // Artwork does. Marked applied once the apply resolves, so a slot whose fetch
  // failed is asked again in the next session; an apply that throws — cut
  // short by leaving the page, or a Steam write failing in the replacing apply
  // — is not marked, and the next visit applies again.
  useEffect(() => {
    const appliedRomId = artworkApplied.get(appId);
    if (!romId || appliedRomId === romId) return;
    const apply = appliedRomId === undefined ? fillEmptyArtwork : applyArtwork;
    apply(romId, appId)
      .then(() => {
        artworkApplied.set(appId, romId);
      })
      .catch((e) => debugLog(`${logContext}: ${e}`));
  }, [appId, romId, logContext]);
}
