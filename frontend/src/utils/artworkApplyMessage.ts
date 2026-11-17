import type { EndpointFailure } from "../api/backend";
import { HostTransportError } from "../api/host";

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

/**
 * The toast where applying artwork threw a transport failure of Tender's own socket: *lead* and the lost connection,
 * as `startCheckFailureSentence` (`gameLaunch.ts`) words it (#2776 D3). `null` for any other throw.
 */
export function lostConnectionMessage(lead: string, e: unknown): string | null {
  return e instanceof HostTransportError ? `${lead}: the connection to Tender's backend was lost.` : null;
}
