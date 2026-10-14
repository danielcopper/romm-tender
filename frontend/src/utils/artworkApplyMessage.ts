import type { EndpointFailure } from "../api/backend";

/** The toast that says what an `applyArtwork` answered. */
export function artworkApplyMessage(applied: number | EndpointFailure): string {
  if (typeof applied !== "number") return applied.message;
  if (applied === -1) return "Set a SteamGridDB API key in settings first";
  if (applied > 0) return `Artwork refreshed (${applied}/4 images applied)`;
  return "No artwork available for this game";
}
