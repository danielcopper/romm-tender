/**
 * Where the ARTWORK tab's row thumbnails come from: Steam's own copy of each slot's image (#2381 D14).
 *
 * Steam serves its grid folder to the renderer under `https://steamloopback.host/customimages/`, and its
 * `appStore.GetCustom…URLs` getters answer the URLs it tries for a slot, `.jpg` before `.png`, each with a
 * cache-buster that moves when artwork is set. The row shows the first URL that loads, as Steam's library does, so it
 * shows what Steam shows — Tender's image, the user's pick, or one set through Steam — and no image passes through the
 * backend. Both are Steam internals, measured on the desktop client in Desktop Mode (2026-10-21); a missing getter or a
 * list where nothing loads leaves the row its placeholder.
 */

import type { ArtworkSlotRow, ArtworkTabSlot } from "../api/backend";

const LOOPBACK = "https://steamloopback.host";

type UrlGetter =
  "GetCustomVerticalCapsuleURLs" | "GetCustomLandcapeImageURLs" | "GetCustomHeroImageURLs" | "GetCustomLogoImageURLs";

const GETTERS: Readonly<Record<Exclude<ArtworkTabSlot, "icon">, UrlGetter>> = {
  cover: "GetCustomVerticalCapsuleURLs",
  grid: "GetCustomLandcapeImageURLs",
  hero: "GetCustomHeroImageURLs",
  logo: "GetCustomLogoImageURLs",
};

/**
 * The URLs to try, in order, for the thumbnail of *row* of shortcut *appId*; empty where none can be named. The icon
 * has no getter: it is Tender's `<appid>_icon.png` while the icon is Tender's, and nothing once it is set in Steam,
 * whose file may lie anywhere on the disk.
 */
export function slotThumbnailUrls(appId: number, row: ArtworkSlotRow): string[] {
  let overview: SteamAppOverview | null = null;
  try {
    overview = appStore.GetAppOverviewByAppID(appId);
  } catch {
    return [];
  }
  if (row.slot === "icon") {
    if (row.state === "set_in_steam" || (row.source !== "tender" && row.source !== "chosen")) return [];
    const version = overview?.rt_custom_image_mtime;
    return [`${LOOPBACK}/customimages/${appId}_icon.png${version === undefined ? "" : `?v=${version}`}`];
  }
  if (!overview) return [];
  const getter = appStore[GETTERS[row.slot]];
  if (typeof getter !== "function") return [];
  try {
    return getter.call(appStore, overview).map((url) => (url.startsWith("/") ? `${LOOPBACK}${url}` : url));
  } catch {
    return [];
  }
}

/**
 * The URLs to try for the cover Steam shows for shortcut *appId* — the GAME INFO tab's cover (#2381 D18) — the same
 * list the ARTWORK tab's cover row tries; empty where Steam names none.
 */
export function coverUrls(appId: number): string[] {
  return slotThumbnailUrls(appId, {
    slot: "cover",
    state: "automatic",
    source: "romm",
    image_id: null,
    has_choice: false,
  });
}
