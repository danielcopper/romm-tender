/**
 * Where the ARTWORK tab's row thumbnails come from: Steam's own copy of each slot's image (#2381 D14).
 *
 * Steam serves its grid folder to the renderer under `https://steamloopback.host/customimages/`, and its
 * `appStore.GetCustom…URLs` getters answer the URLs it tries for a slot, `.jpg` before `.png`. The row shows the first
 * URL that loads, as Steam's library does, so it shows what Steam shows — Tender's image, the user's pick, or one set
 * through Steam — and no image passes through the backend. Both are Steam internals, measured on the desktop client in
 * Desktop Mode (2026-10-21); a missing getter or a list where nothing loads leaves the row its placeholder.
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
 * whose file may lie anywhere on the disk. Each URL carries *token*, the slot's `artworkThumbToken`, and the row's
 * `signature` in place of Steam's own `v=`, which is one value per shortcut: every image set moves it for all four
 * getters, and it never moves for the icon, nor for a slot set back to an image under the same name.
 */
export function slotThumbnailUrls(appId: number, row: ArtworkSlotRow, token = ""): string[] {
  return thumbnailUrls(appId, row, token, row.signature, false);
}

/**
 * The URLs to try for the cover Steam shows for shortcut *appId* — the GAME INFO tab's cover (#2381 D18) — the list the
 * ARTWORK tab's cover row tries; empty where Steam names none. *signature* is the cover row's, where the tab's read is
 * held. Steam's own `v=` stays beside it: no read follows a cover set through Steam's own menu, so only `v=` moves for
 * it, and GAME INFO shows one image, whose reload on another slot's write costs little.
 */
export function coverUrls(appId: number, token = "", signature: string | null = null): string[] {
  return thumbnailUrls(
    appId,
    { slot: "cover", state: "automatic", source: "romm", image_id: null, has_choice: false, signature: "" },
    token,
    signature,
    true,
  );
}

function thumbnailUrls(
  appId: number,
  row: ArtworkSlotRow,
  token: string,
  signature: string | null,
  keepSteamsBuster: boolean,
): string[] {
  let overview: SteamAppOverview | null = null;
  try {
    overview = appStore.GetAppOverviewByAppID(appId);
  } catch {
    return [];
  }
  if (row.slot === "icon") {
    if (row.state === "set_in_steam" || (row.source !== "tender" && row.source !== "chosen")) return [];
    return [withBusters(`${LOOPBACK}/customimages/${appId}_icon.png`, token, signature, keepSteamsBuster)];
  }
  if (!overview) return [];
  const getter = appStore[GETTERS[row.slot]];
  if (typeof getter !== "function") return [];
  try {
    return getter
      .call(appStore, overview)
      .map((url) => withBusters(url.startsWith("/") ? `${LOOPBACK}${url}` : url, token, signature, keepSteamsBuster));
  } catch {
    return [];
  }
}

/** *url* with Tender's token and *signature*, where there is one; Steam's `v=` goes unless *keepSteamsBuster*. */
function withBusters(url: string, token: string, signature: string | null, keepSteamsBuster: boolean): string {
  const parsed = new URL(url);
  if (!keepSteamsBuster) parsed.searchParams.delete("v");
  if (signature !== null) parsed.searchParams.set("s", signature);
  parsed.searchParams.set("t", token);
  return parsed.toString();
}
