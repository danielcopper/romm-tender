/**
 * The frontend half of the shortcut icon job
 * (docs/architecture/steam-non-steam-shortcuts.md, "Shortcut icons"): points each
 * shortcut in a batch at the icon file the backend wrote, through SteamClient
 * (same page, "shortcuts.vdf is memory-authoritative").
 */

import { debugLog } from "../api/backend";
import type { ShortcutIconsData } from "../types";
import { isArtworkApplyInFlight } from "./artwork";
import { detach } from "./detach";
import { isPruneLeaseCancelled, withPruneLease } from "./pruneLease";

/**
 * Set each icon in the batch, under the batch's prune lease, and answer how many
 * were set. A shortcut whose artwork the game page is applying right now is left
 * to that apply: its icon is newer than the one the job fetched.
 */
export async function applyShortcutIcons(data: ShortcutIconsData): Promise<number> {
  return withPruneLease(data.prune_lease_token, "Shortcut icons", async (signal) => {
    let applied = 0;
    for (const { app_id: appId, icon_path: iconPath } of data.icons) {
      if (isPruneLeaseCancelled(signal)) break;
      if (isArtworkApplyInFlight(appId)) continue;
      try {
        SteamClient.Apps.SetShortcutIcon(appId, iconPath);
        applied++;
      } catch (e) {
        detach(debugLog(`Shortcut icons: could not set the icon of appId ${appId}: ${e}`));
      }
    }
    return applied;
  });
}
