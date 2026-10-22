/**
 * Keeping a game's automatic artwork current on a visit to its page (#2381 D5, D26): the backend checks the automatic
 * slots against SteamGridDB at most once a week, and the images Tender's rule now picks are handed to Steam here, with
 * one notification naming the slots replaced, since Steam redraws the page when an image changes.
 */

import { isEndpointFailure, keepArtworkCurrent, debugLog, type ArtworkTabSlot } from "../api/backend";
import { applyKeptCurrentArtwork } from "./artwork";
import { loadArtworkSlots } from "./artworkSlotsStore";
import { detach } from "./detach";
import { showToast } from "./toast";

const SLOT_NOUNS: Readonly<Record<ArtworkTabSlot, string>> = {
  cover: "the cover",
  grid: "the wide grid",
  hero: "the hero",
  logo: "the logo",
  icon: "the icon",
};

/** The notification for the slots keeping current replaced, such as "Updated the logo and the hero from SteamGridDB". */
export function keptCurrentNotice(slots: readonly ArtworkTabSlot[]): string {
  const nouns = slots.map((slot) => SLOT_NOUNS[slot]);
  const last = nouns.pop();
  const named = nouns.length ? `${nouns.join(", ")} and ${last}` : last;
  return `Updated ${named} from SteamGridDB`;
}

/** Ask for the visit's check, hand Steam each image it replaced, and say once which slots changed. */
export async function keepArtworkCurrentOnVisit(appId: number, romId: number): Promise<void> {
  const answer = await keepArtworkCurrent(romId).catch((e: unknown) => {
    detach(debugLog(`Keeping artwork current: the check for rom ${romId} rejected: ${e}`));
    return null;
  });
  if (!answer || isEndpointFailure(answer) || answer.replaced.length === 0) return;
  const set = await applyKeptCurrentArtwork(romId, appId, answer.replaced);
  if (set.length === 0) return;
  showToast(keptCurrentNotice(set));
  if (set.includes("cover")) {
    globalThis.dispatchEvent(
      new CustomEvent("romm_data_changed", { detail: { type: "cover_refreshed", rom_id: romId } }),
    );
  }
  await loadArtworkSlots(appId, romId);
}
