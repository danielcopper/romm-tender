/**
 * What a shortcut the sync converged away leaves to the shortcut its game keeps (#2645 D2).
 *
 * The backend names, for each such removal, the shortcut that stays (`kept_app_id`). Before the removal, the user's
 * Steam collections — Favorites among them — and Hidden carry over to it, and so does each image set in Steam in a slot
 * where the kept shortcut has none set in Steam. Tender's own collections are left alone: the run's collection maps
 * file the kept shortcut there. Every part is best effort, and a part that fails is logged and holds nothing up: the
 * backend unbound the row already, so a shortcut left behind would belong to no version at all.
 */

import { debugLog, getArtworkSlots, isEndpointFailure, logWarn, type ArtworkSlotRow } from "../api/backend";
import { STEAM_ASSET_TYPES } from "./artwork";
import { writeSlotArtwork } from "./artworkSlotsStore";
import { slotThumbnailUrls } from "./artworkThumbs";
import { foldCollectionName } from "./collectionName";
import { detach } from "./detach";

/** A `sync_stale` removal the convergence made: the shortcut that goes, and the one its game keeps. */
export interface ConvergedRemoval {
  rom_id: number;
  app_id: number;
  kept_rom_id: number;
  kept_app_id: number;
}

/** Whether *entry* is a converged removal rather than a plain stale one. */
export function isConvergedRemoval(entry: { rom_id: number; app_id: number }): entry is ConvergedRemoval {
  return typeof (entry as Partial<ConvergedRemoval>).kept_app_id === "number";
}

/** Carry over to the kept shortcut what Steam holds of the one *removal* takes away. */
export async function carryOverShortcutState(removal: ConvergedRemoval): Promise<void> {
  for (const [part, carry] of [
    ["collections", carryOverCollections],
    ["Hidden", carryOverHidden],
    ["artwork", carryOverArtwork],
  ] as const) {
    try {
      await carry(removal);
    } catch (e) {
      logWarn(`Carry-over: the ${part} of appId ${removal.app_id} did not reach appId ${removal.kept_app_id}: ${e}`);
    }
  }
}

async function carryOverCollections({ app_id, kept_app_id }: ConvergedRemoval): Promise<void> {
  const kept = appStore.GetAppOverviewByAppID(kept_app_id);
  if (!kept) return;
  for (const collection of collectionStore.userCollections) {
    if (!collection.apps.has(app_id) || collection.apps.has(kept_app_id)) continue;
    if (foldCollectionName(collection.displayName).startsWith("romm: ")) continue;
    collection.AsDragDropCollection().AddApps([kept]);
    await collection.Save();
  }
}

async function carryOverHidden({ app_id, kept_app_id }: ConvergedRemoval): Promise<void> {
  if (typeof collectionStore.BIsHidden !== "function" || typeof collectionStore.SetAppsAsHidden !== "function") return;
  if (collectionStore.BIsHidden(app_id) && !collectionStore.BIsHidden(kept_app_id)) {
    collectionStore.SetAppsAsHidden([kept_app_id], true);
  }
}

async function carryOverArtwork(removal: ConvergedRemoval): Promise<void> {
  const [removed, kept] = await Promise.all([
    getArtworkSlots(removal.rom_id, removal.app_id),
    getArtworkSlots(removal.kept_rom_id, removal.kept_app_id),
  ]);
  if (isEndpointFailure(removed) || isEndpointFailure(kept)) {
    throw new Error(isEndpointFailure(removed) ? removed.message : (kept as { message: string }).message);
  }
  const keptSetInSteam = new Set(kept.slots.filter((row) => row.state === "set_in_steam").map((row) => row.slot));
  for (const row of removed.slots) {
    // The icon is a path in shortcuts.vdf rather than an image in the grid folder.
    if (row.slot === "icon" || row.state !== "set_in_steam" || keptSetInSteam.has(row.slot)) continue;
    const image = await steamsImage(removal.app_id, row);
    if (image === null) {
      detach(debugLog(`Carry-over: the ${row.slot} of appId ${removal.app_id} could not be read`));
      continue;
    }
    const assetType = STEAM_ASSET_TYPES[row.slot];
    await writeSlotArtwork(removal.kept_app_id, row.slot, () =>
      SteamClient.Apps.SetCustomArtworkForApp(removal.kept_app_id, image.base64, image.type, assetType),
    );
  }
}

/** The image Steam shows for *row* of shortcut *appId*, read from the first of its URLs that loads. */
async function steamsImage(
  appId: number,
  row: ArtworkSlotRow,
): Promise<{ base64: string; type: "png" | "jpg" } | null> {
  for (const url of slotThumbnailUrls(appId, row)) {
    const answer = await fetch(url).catch(() => null);
    if (!answer?.ok) continue;
    const blob = await answer.blob();
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return { base64: btoa(binary), type: blob.type === "image/jpeg" ? "jpg" : "png" };
  }
  return null;
}
