/**
 * The frontend half of the shortcut artwork job
 * (docs/architecture/steam-non-steam-shortcuts.md, "Shortcut artwork"): points each
 * shortcut in a batch at the icon file the backend wrote, through SteamClient
 * (same page, "shortcuts.vdf is memory-authoritative"), then sets each wide grid
 * and logo the batch names, one image at a time.
 */

import { logError } from "../api/backend";
import type { ShortcutArtworkData } from "../types";
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
    let applied = setIcons(data, stopped);
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

function setIcons(data: ShortcutArtworkData, stopped: () => boolean): number {
  let applied = 0;
  for (const { app_id: appId, icon_path: iconPath } of data.icons) {
    if (stopped()) break;
    if (isArtworkApplyInFlight(appId)) continue;
    try {
      SteamClient.Apps.SetShortcutIcon(appId, iconPath);
      applied++;
    } catch (e) {
      logError(`Shortcut artwork: could not set the icon of appId ${appId}: ${e}`);
    }
  }
  return applied;
}
