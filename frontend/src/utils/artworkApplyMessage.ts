import type { EndpointFailure } from "../api/backend";

/**
 * The toast that says what an `applyArtwork` answered; *steamGridDbCover* counts a cover Tender set from SteamGridDB
 * beside the four other images (#2381 D23).
 */
export function artworkApplyMessage(applied: number | EndpointFailure, steamGridDbCover = false): string {
  if (typeof applied !== "number") return applied.message;
  if (applied === -1) return "Set a SteamGridDB API key in Settings first";
  const cover = steamGridDbCover ? 1 : 0;
  if (applied + cover > 0) return `Artwork refreshed (${applied + cover}/${4 + cover} images applied)`;
  return "No artwork available for this game";
}
