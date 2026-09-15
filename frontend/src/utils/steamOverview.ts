/**
 * The reach into a live `SteamAppOverview` — Steam's own MobX-observed object,
 * looked up in Steam's own appStore.
 *
 * Only what the write touches lives here; what DECIDES which appId gets which
 * value, which items are still pending and when to stop retrying is
 * `metadataPatches.ts`, which reads the overview only through this module. The
 * seam is the object, not the judgement: the three guards below — the rating
 * only where the server sent one, rounded; the categories only where the
 * overview offers the setter; the timestamp only where the caller asked for it
 * — are this plugin's decisions rather than Steam's, so each is asserted in
 * both directions through its caller in `metadataPatches.test.ts` rather than
 * exempted. The timestamp is the pair worth stating: a single-app write stamps
 * `rt_last_time_played` because a session has just ended, a bulk pass must not
 * because it is no evidence the game was played, and only the caller knows
 * which it is.
 */
import type { RomMetadata } from "../types";
import { stateTransaction } from "./steamState";

/**
 * Steam's overview object for an app, or `null` when its appStore holds no
 * entry for that appId. Steam rebuilds appStore on every mount and may still be
 * loading shortcuts, so the callers treat a `null` as a "not yet" and retry —
 * but it is also what a shortcut deleted through Steam's own UI answers, and
 * that one never comes back: the binding it was reached through survives in the
 * backend's appId map until the next sync-start reconcile (see
 * `docs/architecture/steam-non-steam-shortcuts.md`). So the retry ladder's
 * "still unavailable after all retries" covers both, and this function cannot
 * tell them apart.
 */
export function overviewFor(appId: number): SteamAppOverview | null {
  return appStore.GetAppOverviewByAppID(appId);
}

/**
 * Write the metadata fields Steam's own UI reads — controller support, the
 * rating it shows as a metacritic score, and the store categories. Idempotent:
 * every write is an assignment or a set-insert of the same value.
 */
export function writeMetadataFields(overview: SteamAppOverview, metadata: RomMetadata): void {
  stateTransaction(() => {
    overview.controller_support = 2;

    if (metadata.average_rating != null) {
      overview.metacritic_score = Math.round(metadata.average_rating);
    }

    if (overview.m_setStoreCategories && metadata.steam_categories) {
      for (const cat of metadata.steam_categories) {
        overview.m_setStoreCategories.add(cat);
      }
    }
  });
}

/** What the overview held before {@link writePlaytimeFields} changed it. */
export interface PreviousPlaytime {
  minutes: number | undefined;
  lastPlayed: number | undefined;
}

/**
 * Write tracked playtime into the fields Steam's library shows, so a RomM
 * shortcut reads as played rather than "Never Played". Returns what the two
 * fields held beforehand, which is what the caller's debug line reports.
 */
export function writePlaytimeFields(
  overview: SteamAppOverview,
  totalMinutes: number,
  updateLastPlayed: boolean,
): PreviousPlaytime {
  const previous: PreviousPlaytime = {
    minutes: overview.minutes_playtime_forever,
    lastPlayed: overview.rt_last_time_played,
  };
  stateTransaction(() => {
    overview.minutes_playtime_forever = totalMinutes;
    if (updateLastPlayed) {
      overview.rt_last_time_played = Math.floor(Date.now() / 1000);
    }
  });
  return previous;
}
