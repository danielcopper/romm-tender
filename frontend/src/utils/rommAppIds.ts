/**
 * Which Steam appIds are ours.
 *
 * Shared knowledge rather than a surface's, with four consumers: the launch
 * interceptor asks it before it touches a launch; the sync manager fills it in
 * as shortcuts are written; the plugin entry (`index.tsx`) fills it in too —
 * from the appId map at start-up, and again from a finished run's maps as the
 * net for an appId the unit loop did not reach — and is the only caller of
 * {@link unregisterRomMAppId}, on the appIds a finished prune reports removed;
 * and the panel's game-detail patch reads it to decide whether to swap Steam's
 * overview. It lived inside that patch file while there was one surface and
 * nothing forced the question.
 *
 * The set stays private to this module. A registry whose backing store anything
 * can reach is not a registry, and nothing in the toolchain would notice a
 * direct mutation — so every reader goes through a function here, including the
 * game-detail patch's debug line, which is what {@link rommAppIdCount} answers.
 */

// Cached set of RomM app IDs — updated by registerRomMAppId
const rommAppIds = new Set<number>();

export function registerRomMAppId(appId: number) {
  rommAppIds.add(appId);
}

export function unregisterRomMAppId(appId: number) {
  rommAppIds.delete(appId);
}

export function isRomMAppId(appId: number): boolean {
  return rommAppIds.has(appId);
}

/** How many appIds the registry holds — a diagnostic read, not a membership test. */
export function rommAppIdCount(): number {
  return rommAppIds.size;
}
