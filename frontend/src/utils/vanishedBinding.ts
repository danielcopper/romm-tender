/**
 * What RomM has positively confirmed about the version each appId is bound to.
 *
 * The version picker learns what the server said about a bound id
 * (`get_version_list`), and it sits beside the play button and below the game
 * page's banner rather than above them — none can see another's state. This is
 * the seam between them: the picker publishes what it learned, the play button
 * reads it to stop offering a download that can only ever fail, and the banner
 * to say what being gone from RomM means.
 *
 * **Positive knowledge only.** An appId is anything but `live` solely when the
 * backend answered `bound_vanished` for it, which it does exclusively on a 404
 * for the bound id. A timeout, a transport error, or an unreachable server all
 * report `false` alongside `server_query_failed`, so an offline session never
 * disables anything — the fail-open rule the whole vanished-ROM feature is
 * built on.
 */

import type { VersionList } from "../api/backend";

/**
 * `live` — nothing says the bound version is gone. `version_gone` — it is, and
 * another version of the game is not. `game_gone` — every version the list
 * knows of is.
 */
export type BoundAvailability = "live" | "version_gone" | "game_gone";

type Listener = () => void;

const availabilityByAppId = new Map<number, BoundAvailability>();
const listeners = new Set<Listener>();

/** What one `get_version_list` answer says about its bound version. */
export function boundAvailabilityOf(list: VersionList): BoundAvailability {
  if (!list.bound_vanished) return "live";
  const others = (list.versions ?? []).filter((version) => !version.active);
  return others.some((version) => !version.vanished) ? "version_gone" : "game_gone";
}

/** Record what the server said about `appId`'s bound ROM. */
export function setBoundAvailability(appId: number, availability: BoundAvailability): void {
  if (boundAvailability(appId) === availability) return;
  availabilityByAppId.set(appId, availability);
  for (const listener of listeners) listener();
}

export function boundAvailability(appId: number): BoundAvailability {
  return availabilityByAppId.get(appId) ?? "live";
}

/** Whether RomM has positively confirmed `appId`'s bound ROM is gone. */
export function isBoundVanished(appId: number): boolean {
  return boundAvailability(appId) !== "live";
}

export function onBoundAvailabilityChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test seam — production never clears the map, it is keyed per appId. */
export function resetBoundAvailability(): void {
  availabilityByAppId.clear();
  for (const listener of listeners) listener();
}
