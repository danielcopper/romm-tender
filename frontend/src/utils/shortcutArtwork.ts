/**
 * The frontend half of the shortcut artwork job
 * (docs/architecture/steam-non-steam-shortcuts.md, "Shortcut artwork"): points each
 * shortcut in a batch at the icon file the backend wrote, through SteamClient
 * (same page, "shortcuts.vdf is memory-authoritative"), reports what Steam made
 * of each SteamGridDB icon, then sets each wide grid and logo the batch names,
 * one image at a time.
 */

import { debugLog, logError, reportSgdbSlotArtwork } from "../api/backend";
import type { ShortcutArtworkData, ShortcutIconRecord } from "../types";
import { detach } from "./detach";
import { isArtworkApplyInFlight, setJobSlotArtwork } from "./artwork";
import { isPruneLeaseCancelled, withPruneLease } from "./pruneLease";

/**
 * Set each icon, wide grid and logo in the batch, under the batch's prune lease,
 * and answer how many were set. A shortcut whose artwork the game page is
 * applying right now is left to that apply ({@link isArtworkApplyInFlight} says
 * why).
 */
export async function applyShortcutArtwork(data: ShortcutArtworkData): Promise<number> {
  return withPruneLease(data.prune_lease_token, "Shortcut artwork", async (signal) => {
    const stopped = (): boolean => isPruneLeaseCancelled(signal);
    let applied = await setIcons(data, stopped);
    // One image at a time: each is fetched, set and reported before the next is
    // fetched, so only one is in the renderer at once — the awaits are
    // sequential by design. S9382 is raised on the await line, so its NOSONAR
    // must stay there; prettier-ignore stops Prettier from moving it.
    for (const { app_id: appId, rom_id: romId, slot } of data.artwork) {
      if (stopped()) break;
      if (isArtworkApplyInFlight(appId)) continue;
      try {
        // prettier-ignore
        if (await setJobSlotArtwork(romId, appId, slot, stopped)) applied++; // NOSONAR(typescript:S9382)
      } catch (e) {
        logError(`Shortcut artwork: could not set the ${slot} of appId ${appId}: ${e}`);
      }
    }
    return applied;
  });
}

async function setIcons(data: ShortcutArtworkData, stopped: () => boolean): Promise<number> {
  let applied = 0;
  const reports: Promise<void>[] = [];
  for (const { app_id: appId, icon_path: iconPath, record } of data.icons) {
    if (stopped()) break;
    if (isArtworkApplyInFlight(appId)) continue;
    let outcome: "set" | "refused" = "set";
    try {
      SteamClient.Apps.SetShortcutIcon(appId, iconPath);
      applied++;
    } catch (e) {
      outcome = "refused";
      logError(`Shortcut artwork: could not set the icon of appId ${appId}: ${e}`);
    }
    if (record) reports.push(reportIcon(record, outcome));
  }
  await Promise.all(reports);
  return applied;
}

/** Report what Steam made of a SteamGridDB icon for the backend to record. */
async function reportIcon(record: ShortcutIconRecord, outcome: "set" | "refused"): Promise<void> {
  const { rom_id: romId, sgdb_id: sgdbId, image_id: imageId, sha256 } = record;
  await reportSgdbSlotArtwork(romId, "icon", outcome, sgdbId, imageId, sha256).catch((e) => {
    detach(debugLog(`Shortcut artwork: reporting the icon for rom ${romId} failed: ${e}`));
  });
}
