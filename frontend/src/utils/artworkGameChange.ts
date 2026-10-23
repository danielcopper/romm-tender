/**
 * What a visit to a game's page does with its artwork. Once RomM's SteamGridDB id has replaced the game the user chose
 * (#2381 D17), the backend discarded the images chosen under that game, and here the slots are set to Tender's pick
 * under RomM's game — a slot set in Steam left alone — and the user is told once. Then the automatic artwork is kept
 * current (D5).
 */

import { isEndpointFailure, setArtworkSlotAutomatic, debugLog, type SupersededGame } from "../api/backend";
import { applyAutomaticArtwork, applySteamGridDbCover, applyTabSlotImage } from "./artwork";
import { keepArtworkCurrentOnVisit } from "./artworkKeepCurrent";
import { loadArtworkGame } from "./artworkSlotsStore";
import { detach } from "./detach";
import { showToast } from "./toast";

/** The notice for a game whose chosen images went back to automatic under RomM's game. */
export function supersededNotice(superseded: SupersededGame): string {
  const name = superseded.name ?? "another game";
  return `RomM now maps this game to ${name}; your chosen images went back to automatic.`;
}

/**
 * Read the page's SteamGridDB game; where RomM's replaced the chosen one, apply automatic and say so once. Then keep the
 * automatic artwork current.
 */
export async function settleArtworkGame(appId: number, romId: number): Promise<void> {
  const answer = await loadArtworkGame(appId, romId);
  if (answer?.superseded) await applySupersededGame(appId, romId, answer.superseded);
  await keepArtworkCurrentOnVisit(appId, romId);
}

/**
 * Say once that RomM's game replaced the chosen one, and set the slots it emptied of a choice to automatic; a cover
 * showing Tender's SteamGridDB pick is picked anew under RomM's game.
 */
export async function applySupersededGame(appId: number, romId: number, superseded: SupersededGame): Promise<void> {
  showToast(supersededNotice(superseded));
  await applyAutomaticArtwork(romId, appId);
  if (!superseded.slots.includes("cover")) {
    await applySteamGridDbCover(romId, appId);
    return;
  }
  // The cover goes back to RomM's through the endpoint that reloads it under its own rules.
  const cover = await setArtworkSlotAutomatic(romId, "cover").catch((e: unknown) => {
    detach(debugLog(`The cover of appId ${appId} could not go back to RomM's: ${e}`));
    return null;
  });
  if (cover && !isEndpointFailure(cover)) await applyTabSlotImage(romId, appId, "cover", cover, false);
}
